"""Prospector agent loop with OpenAI-compatible tool calling via TokenRouter."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import AsyncGenerator
from urllib.parse import urlparse

from openai import OpenAI

from memory import already_contacted, build_lead_key, save_lead, save_prospect_seen
from tools.email_gen import generate_email
from tools.email_score import score_email
from tools.enrich import enrich_contact
from tools.search import web_search

def _client() -> OpenAI:
    api_key = os.getenv("TOKENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing TOKENROUTER_API_KEY in environment.")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1"),
    )

SYSTEM_PROMPT = """You are Prospector, an autonomous B2B prospecting and outreach agent.

Objectives:
1. Find real companies matching the ICP using web_search.
2. Enrich top candidates with enrich_contact.
3. Draft personalized outreach with generate_email.
4. Score drafts with score_email.
5. ONLY output leads with score >= 70
6. If score < 70 → refine or discard
7. Prefer 1 excellent lead over multiple average ones
8. If no strong personalization signal exists → SKIP the lead

Rules:
- Use only real, verifiable companies.
- Be explicit and concise in reasoning before tool calls.
- Prioritize quality over quantity.
- Stop when max_leads qualified leads are surfaced.
- Do not run more than 3 web_search calls in a row without at least one enrich_contact call.
- Avoid generic market-overview, investor-list, and startup-directory queries when a concrete company name can be searched instead.
- Once a search result names a concrete company, resolve its official site and enrich it before broadening the search further.
- Never claim "qualified leads identified" unless score_email has produced score >= 70 and the lead was actually saved.
- Never invent email addresses, employee counts, funding stage, or founder names.
- If a tool fails, report the failure plainly and continue only with tool-backed facts.
- If no qualified leads are found, end with a concise "no qualified leads found" summary and suggested ICP refinement.
- Only pass direct company pages to enrich_contact (homepage/about/team/contact). Never pass directories, listings, news, press, jobs, or social URLs.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search for VERY SPECIFIC companies matching ICP. "
                "Include funding stage, recent launches/funding/hiring activity, target job titles, and tech stack when known. "
                "Example query: 'Series A fintech startup CTO Stripe API launch 2025'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Specific company/ICP search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enrich_contact",
            "description": "Scrape and enrich company/contact context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_url": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "contact_title": {"type": "string"},
                },
                "required": ["company_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_email",
            "description": "Draft a personalized cold email using enriched contact data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "object"},
                    "sender_info": {"type": "string"},
                    "goal": {"type": "string"},
                },
                "required": ["contact", "sender_info", "goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_email",
            "description": "Score a cold email for quality across key dimensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "contact_context": {"type": "string"},
                },
                "required": ["subject", "body"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "web_search": web_search,
    "enrich_contact": enrich_contact,
    "generate_email": generate_email,
    "score_email": score_email,
}

BLOCKED_HOST_FRAGMENTS = [
    "linkedin.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "podbean.com",
    "techcrunch.com",
    "forbes.com",
    "crunchbase.com",
    "ycombinator.com",
    "getlatka.com",
    "medium.com",
    "substack.com",
    "reddit.com",
    "wikipedia.org",
    "topstartups.io",
    "fundraiseinsider.com",
    "growthlist.co",
    "simplify.jobs",
    "prnewswire.com",
    "jobs.ashbyhq.com",
    "glassdoor.com",
    "indeed.com",
    "lever.co",
    "greenhouse.io",
    "reuters.com",
    "startupscience.io",
    "wellfound.com",
    "builtinnyc.com",
    "workmotion.com",
    "4dayweek.io",
]

FALLBACK_COMPANY_URLS = [
    "https://plane.so/",
    "https://attio.com/",
    "https://merge.dev/",
    "https://posthog.com/",
    "https://render.com/",
    "https://vercel.com/",
]

DEMO_CANDIDATES = [
    {
        "company_url": "https://valeriehealth.com/",
        "contact_name": "Pete Shalek",
        "contact_title": "Founder/CEO",
        "keywords": ["health", "healthcare", "provider", "clinic", "medical", "ai"],
    },
    {
        "company_url": "https://www.assorthealth.com/",
        "contact_name": "Jeffery Liu",
        "contact_title": "Co-Founder/CEO",
        "keywords": ["health", "healthcare", "provider", "clinic", "medical", "ai"],
    },
    {
        "company_url": "https://attio.com/",
        "contact_name": "",
        "contact_title": "Founder/CEO",
        "keywords": ["crm", "sales", "gtm", "pipeline", "saas", "b2b"],
    },
    {
        "company_url": "https://posthog.com/",
        "contact_name": "",
        "contact_title": "Founder/CEO",
        "keywords": ["product", "analytics", "developer", "saas", "b2b", "ai"],
    },
]

BLOCKED_PATH_FRAGMENTS = [
    "/blog",
    "/news",
    "/press",
    "/podcast",
    "/careers",
    "/jobs",
    "/compare",
    "/organization/",
    "/companies/industry",
    "/the-pitch-by-deel",
    "/organization/",
    "/p/",
    "/feed",
]

ALLOWED_PATH_PREFIXES = [
    "",
    "/",
    "/about",
    "/about-us",
    "/team",
    "/contact",
]


def _is_likely_company_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.netloc or "").lower()
    if not host:
        return False
    if any(fragment in host for fragment in BLOCKED_HOST_FRAGMENTS):
        return False

    path = (parsed.path or "").lower()
    if any(fragment in path for fragment in BLOCKED_PATH_FRAGMENTS):
        return False

    if not any(path == prefix or path.startswith(prefix + "/") for prefix in ALLOWED_PATH_PREFIXES if prefix):
        if path not in {"", "/"}:
            return False

    # Heavy query pages are low-value enrichment targets.
    if parsed.query and len(parsed.query) > 40:
        return False

    if host.count(".") < 1:
        return False

    return True


def _pick_company_url(search_result: dict) -> str:
    results = search_result.get("results") if isinstance(search_result, dict) else []
    if not isinstance(results, list):
        return ""

    for item in results:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        if not _is_likely_company_url(url):
            continue
        return url
    return ""


COMPANY_ACTION_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9&.'/-]*(?:\s+[A-Z][A-Za-z0-9&.'/-]*){0,4})\s+"
    r"(?:raises|raised|secures|secured|announces|announced|hiring|lands|closed|closes|picks up|nabs|brings)"
)


def _normalize_company_candidate(value: str) -> str:
    cleaned = re.sub(r"^(exclusive|breaking|watch|analysis)\s*:\s*", "", (value or "").strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|,.;")
    if len(cleaned) < 3 or len(cleaned.split()) > 6:
        return ""
    lowered = cleaned.lower()
    blocked_terms = {
        "series",
        "seed",
        "healthcare",
        "startup",
        "founder",
        "jobs",
        "careers",
        "chief of staff",
        "experience",
        "today",
        "job board",
        "customer stories",
        "book a demo",
        "for payers",
        "documents",
        "customers",
        "privacy policy",
        "about",
        "official site",
        "exclusive",
    }
    if lowered in blocked_terms:
        return ""
    if any(token in lowered for token in ["hiring", "jobs", "careers", "funding", "series a healthcare startups"]):
        return ""
    if any(token in lowered for token in ["recruiter", "chief", "marketing", "associate", "executive", "staff", "manager", "officer", "lead"]):
        return ""
    words = cleaned.split()
    if not all(word[:1].isupper() or word.isupper() for word in words):
        return ""
    return cleaned


def _extract_company_candidates(search_result: dict) -> list[str]:
    results = search_result.get("results") if isinstance(search_result, dict) else []
    if not isinstance(results, list):
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for item in results[:6]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        text_candidates: list[str] = []

        title_base = re.split(r"\s*[-|:]\s*", title, maxsplit=1)[0].strip()
        if title_base:
            text_candidates.append(title_base)

        for source_text in (title, snippet):
            match = COMPANY_ACTION_PATTERN.search(source_text)
            if match:
                text_candidates.append(match.group(1))

        if not text_candidates:
            title_match = re.match(r"([A-Z][A-Za-z0-9&.'/-]*(?:\s+[A-Z][A-Za-z0-9&.'/-]*){0,3})", title)
            if title_match:
                text_candidates.append(title_match.group(1))

        for raw in text_candidates:
            candidate = _normalize_company_candidate(raw)
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _discover_company_url_from_results(search_result: dict, used_company_urls: set[str]) -> str:
    query_context = " ".join(str(search_result.get("query", "")).split()[:6]).strip()
    for company_name in _extract_company_candidates(search_result)[:4]:
        discovery_queries = [f'"{company_name}" official site']
        if query_context:
            discovery_queries.insert(0, f'"{company_name}" official site {query_context}')
        for discovery_query in discovery_queries:
            site_search = web_search(discovery_query)
            url = _pick_company_url(site_search)
            if not url:
                continue
            normalized = url.strip().lower()
            if normalized in used_company_urls:
                continue
            return url
    return ""


def _candidate_models() -> list[str]:
    primary = os.getenv("PROSPECTOR_AGENT_MODEL", "claude-sonnet-4-20250514").strip()
    fallbacks = [
        os.getenv("PROSPECTOR_AGENT_MODEL_FALLBACK", "claude-haiku-4-5").strip(),
        "gpt-4o-mini",
    ]
    seen: set[str] = set()
    models: list[str] = []
    for model in [primary, *fallbacks]:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


def _has_strong_personalization_signal(contact: dict) -> bool:
    if not isinstance(contact, dict):
        return False
    signals = contact.get("signals") or []
    if isinstance(signals, list) and len(signals) > 0:
        return True
    context = (contact.get("contact_context") or contact.get("recent_content") or "").strip()
    if len(context) >= 80:
        return True
    description = (contact.get("description") or "").strip()
    return len(description) >= 60


def _build_contact_context(description: str = "", recent_content: str = "") -> str:
    parts = []
    if (description or "").strip():
        parts.append((description or "").strip())
    if (recent_content or "").strip():
        parts.append((recent_content or "").strip())
    return " ".join(parts)[:400]


def _select_demo_candidates(icp: str, used_company_urls: set[str]) -> list[dict]:
    lowered_icp = (icp or "").lower()
    scored: list[tuple[int, dict]] = []
    for candidate in DEMO_CANDIDATES:
        url = candidate.get("company_url", "").strip().lower()
        if not url or url in used_company_urls:
            continue
        score = sum(1 for keyword in candidate.get("keywords", []) if keyword in lowered_icp)
        scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in scored]


def _prepare_demo_contact(candidate: dict, enriched: dict) -> dict:
    company_url = enriched.get("company_url") or candidate.get("company_url", "")
    description = enriched.get("description", "")
    recent_content = enriched.get("recent_content", "")
    return {
        "name": enriched.get("name") or candidate.get("contact_name", ""),
        "title": enriched.get("title") or candidate.get("contact_title", ""),
        "company": enriched.get("company", ""),
        "company_url": company_url,
        "email": enriched.get("email", ""),
        "signals": enriched.get("signals", []),
        "description": description,
        "contact_context": _build_contact_context(description, recent_content),
    }


def _save_and_emit_lead(current_lead: dict, total: int, icp: str) -> tuple[bool, dict | None]:
    email_value = (current_lead.get("email") or "").strip().lower()
    lead_key = build_lead_key(
        company=current_lead.get("company", ""),
        company_url=current_lead.get("company_url", ""),
        contact_name=current_lead.get("name", ""),
        contact_title=current_lead.get("title", ""),
        email=email_value,
    )
    if not lead_key.strip("|"):
        return False, {
            "type": "thought",
            "content": "Skipping lead — not enough company identity to save it reliably.",
        }

    if already_contacted(email_value, lead_key=lead_key):
        return False, {
            "type": "thought",
            "content": "Skipping — already seen in saved leads.",
        }

    saved = save_lead(
        email=email_value,
        company=current_lead.get("company", ""),
        contact_name=current_lead.get("name", ""),
        company_url=current_lead.get("company_url", ""),
        contact_title=current_lead.get("title", ""),
        enrichment=current_lead,
        subject=current_lead.get("subject", ""),
        draft=current_lead.get("body", ""),
        score=total,
        icp=icp,
    )
    if not saved:
        return False, {
            "type": "thought",
            "content": "Skipping lead — could not persist it to memory.",
        }

    return True, {"type": "lead", "data": current_lead}


def _run_auto_outreach_pipeline(
    current_lead: dict,
    sender_info: str,
    goal: str,
    icp: str,
    *,
    auto: bool = False,
    fallback: bool = False,
) -> tuple[list[dict], bool, dict]:
    flags = {}
    if auto:
        flags["auto"] = True
    if fallback:
        flags["fallback"] = True

    events: list[dict] = []
    generate_args = {
        "contact": current_lead,
        "sender_info": sender_info,
        "goal": goal,
    }
    events.append(
        {
            "type": "tool_call",
            "tool": "generate_email",
            "args": {
                "contact": {
                    "company": current_lead.get("company", ""),
                    "title": current_lead.get("title", ""),
                },
                "sender_info": "...",
                "goal": goal,
            },
            **flags,
        }
    )
    try:
        email_result = generate_email(**generate_args)
    except Exception as exc:
        email_result = {"error": str(exc)}
    events.append(
        {
            "type": "tool_result",
            "tool": "generate_email",
            "result": email_result,
            **flags,
        }
    )
    if not isinstance(email_result, dict) or email_result.get("error"):
        return events, False, current_lead

    current_lead.update(
        {
            "subject": email_result.get("subject", ""),
            "body": email_result.get("body", ""),
            "bucket": email_result.get("bucket", ""),
            "signals_used": email_result.get("signals_used", []),
        }
    )

    score_args = {
        "subject": current_lead.get("subject", ""),
        "body": current_lead.get("body", ""),
        "contact_context": current_lead.get("contact_context", ""),
    }
    events.append(
        {
            "type": "tool_call",
            "tool": "score_email",
            "args": {
                "subject": current_lead.get("subject", ""),
                "body": "...",
            },
            **flags,
        }
    )
    try:
        score_result = score_email(**score_args)
    except Exception as exc:
        score_result = {"error": str(exc)}
    events.append(
        {
            "type": "tool_result",
            "tool": "score_email",
            "result": score_result,
            **flags,
        }
    )
    if not isinstance(score_result, dict) or score_result.get("error"):
        return events, False, current_lead

    total = int(score_result.get("total", 0) or 0)
    current_lead["total"] = total
    current_lead["suggestion"] = score_result.get("suggestion", "")
    if total < 75:
        suggestion = current_lead.get("suggestion", "")
        events.append(
            {
                "type": "thought",
                "content": (
                    "Improving draft after low score: "
                    f"{suggestion or 'make it more specific and higher quality.'}"
                ),
            }
        )
        improved_goal = (
            f"{goal}. Improve the previous email using this feedback: "
            f"{suggestion}. Make it more specific and higher quality."
        )
        events.append(
            {
                "type": "tool_call",
                "tool": "generate_email",
                "args": {
                    "contact": {
                        "company": current_lead.get("company", ""),
                        "title": current_lead.get("title", ""),
                    },
                    "sender_info": "...",
                    "goal": improved_goal,
                },
                **flags,
            }
        )
        try:
            retry_email = generate_email(contact=current_lead, sender_info=sender_info, goal=improved_goal)
        except Exception as exc:
            retry_email = {"error": str(exc)}
        events.append(
            {
                "type": "tool_result",
                "tool": "generate_email",
                "result": retry_email,
                **flags,
            }
        )
        if isinstance(retry_email, dict) and not retry_email.get("error"):
            current_lead.update(
                {
                    "subject": retry_email.get("subject", current_lead.get("subject", "")),
                    "body": retry_email.get("body", current_lead.get("body", "")),
                    "bucket": retry_email.get("bucket", current_lead.get("bucket", "")),
                    "signals_used": retry_email.get("signals_used", current_lead.get("signals_used", [])),
                }
            )
            events.append(
                {
                    "type": "tool_call",
                    "tool": "score_email",
                    "args": {
                        "subject": current_lead.get("subject", ""),
                        "body": "...",
                    },
                    **flags,
                }
            )
            try:
                retry_score = score_email(
                    subject=current_lead.get("subject", ""),
                    body=current_lead.get("body", ""),
                    contact_context=current_lead.get("contact_context", ""),
                )
            except Exception as exc:
                retry_score = {"error": str(exc)}
            events.append(
                {
                    "type": "tool_result",
                    "tool": "score_email",
                    "result": retry_score,
                    **flags,
                }
            )
            if isinstance(retry_score, dict) and not retry_score.get("error"):
                total = int(retry_score.get("total", total) or total)
                current_lead["total"] = total
                current_lead["suggestion"] = retry_score.get("suggestion", "")

    if total < 70:
        events.append(
            {
                "type": "thought",
                "content": "Discarding lead — score below 70 after refinement.",
            }
        )
        return events, False, current_lead

    saved, final_event = _save_and_emit_lead(current_lead, total, icp)
    if final_event:
        events.append(final_event)
    return events, saved, current_lead


async def run_prospector(
    icp: str,
    sender_info: str,
    goal: str,
    max_leads: int = 1,
) -> AsyncGenerator[dict, None]:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Find and draft outreach for this ICP:\n"
                f"ICP: {icp}\n"
                f"Sender info: {sender_info}\n"
                f"Goal: {goal}\n"
                f"Max quality leads: {max_leads}"
            ),
        },
    ]

    max_iterations = 15
    qualified_leads = 0
    current_lead: dict = {}
    consecutive_search_calls = 0
    total_search_calls = 0
    total_enrich_calls = 0
    auto_enrich_attempts = 0

    search_cap = max(5, int(os.getenv("PROSPECTOR_MAX_CONSECUTIVE_SEARCHES", "8") or 8))
    total_search_limit = max(10, int(os.getenv("PROSPECTOR_TOTAL_SEARCH_LIMIT", "24") or 24))
    fallback_limit = max(2, int(os.getenv("PROSPECTOR_FALLBACK_COMPANIES", "3") or 3))
    allow_generic_fallbacks = os.getenv("PROSPECTOR_ENABLE_GENERIC_FALLBACKS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    used_company_urls: set[str] = set()

    for _ in range(max_iterations):
        try:
            client = _client()
            response = None
            last_error: Exception | None = None
            for model in _candidate_models():
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        max_tokens=4096,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if "model_not_found" in str(exc):
                        continue
                    raise

            if response is None:
                raise last_error or RuntimeError("No model available for Prospector run.")
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        msg = response.choices[0].message
        thought = (msg.content or "").strip()
        if thought:
            yield {"type": "thought", "content": thought}

        assistant_message = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_message)

        if response.choices[0].finish_reason == "stop" or not msg.tool_calls:
            if qualified_leads == 0:
                no_leads_message = (
                    "No qualified leads found in this run. "
                    "Try narrowing ICP by vertical, role, and employee range."
                )
                yield {
                    "type": "error",
                    "message": no_leads_message,
                }
                yield {
                    "type": "done",
                    "reason": "no_qualified_leads",
                    "content": no_leads_message,
                    "metrics": {
                        "qualified_leads": qualified_leads,
                        "search_calls": total_search_calls,
                        "enrich_calls": total_enrich_calls,
                        "auto_enrich_attempts": auto_enrich_attempts,
                    },
                }
                return
            yield {
                "type": "done",
                "reason": "agent_stop",
                "content": thought or "Prospecting complete.",
                "metrics": {
                    "qualified_leads": qualified_leads,
                    "search_calls": total_search_calls,
                    "enrich_calls": total_enrich_calls,
                    "auto_enrich_attempts": auto_enrich_attempts,
                },
            }
            return

        tool_messages = []
        for tool_call in msg.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                fn_args = {}

            yield {"type": "tool_call", "tool": fn_name, "args": fn_args}

            if fn_name == "web_search":
                consecutive_search_calls += 1
                total_search_calls += 1
            else:
                consecutive_search_calls = 0
                if fn_name == "enrich_contact":
                    total_enrich_calls += 1
                    requested_url = str(fn_args.get("company_url", "")).strip().lower()
                    if requested_url:
                        used_company_urls.add(requested_url)

            if total_search_calls > total_search_limit:
                if qualified_leads == 0:
                    demo_candidates = _select_demo_candidates(icp, used_company_urls)
                    if demo_candidates:
                        yield {
                            "type": "thought",
                            "content": (
                                "Search quality is shaky. Switching to a curated demo-safe company set "
                                "to produce at least one strong, verifiable lead."
                            ),
                        }
                    for candidate in demo_candidates[:2]:
                        candidate_url = candidate.get("company_url", "")
                        demo_enrich_args = {
                            "company_url": candidate_url,
                            "contact_name": candidate.get("contact_name", ""),
                            "contact_title": candidate.get("contact_title", "Founder/CEO"),
                        }
                        yield {
                            "type": "tool_call",
                            "tool": "enrich_contact",
                            "args": demo_enrich_args,
                            "auto": True,
                            "fallback": True,
                            "demo": True,
                        }
                        try:
                            demo_enrich = enrich_contact(**demo_enrich_args)
                        except Exception as exc:
                            demo_enrich = {"error": str(exc)}
                        total_enrich_calls += 1
                        yield {
                            "type": "tool_result",
                            "tool": "enrich_contact",
                            "result": demo_enrich,
                            "auto": True,
                            "fallback": True,
                            "demo": True,
                        }
                        if not isinstance(demo_enrich, dict) or demo_enrich.get("error"):
                            continue

                        current_lead = _prepare_demo_contact(candidate, demo_enrich)
                        if not _has_strong_personalization_signal(current_lead):
                            yield {
                                "type": "thought",
                                "content": "Skipping demo fallback candidate — weak personalization signal.",
                            }
                            continue

                        save_prospect_seen(
                            company=current_lead.get("company", ""),
                            company_url=current_lead.get("company_url", ""),
                            contact_name=current_lead.get("name", ""),
                            contact_title=current_lead.get("title", ""),
                            email=current_lead.get("email", ""),
                            icp=icp,
                        )
                        pipeline_events, saved, current_lead = _run_auto_outreach_pipeline(
                            current_lead,
                            sender_info,
                            goal,
                            icp,
                            auto=True,
                            fallback=True,
                        )
                        for event in pipeline_events:
                            if event.get("type") in {"tool_call", "tool_result", "thought"}:
                                event["demo"] = True
                            yield event
                        if saved:
                            qualified_leads += 1
                            yield {
                                "type": "done",
                                "reason": "demo_candidate_saved",
                                "content": f"Recovered the run with a verified demo candidate: {current_lead.get('company', 'lead')}.",
                                "metrics": {
                                    "qualified_leads": qualified_leads,
                                    "search_calls": total_search_calls,
                                    "enrich_calls": total_enrich_calls,
                                    "auto_enrich_attempts": auto_enrich_attempts,
                                },
                            }
                            return

                if qualified_leads == 0 and allow_generic_fallbacks:
                    yield {
                        "type": "thought",
                        "content": (
                            "Search quality is low. Switching to fallback company domains "
                            "to finish this run with verifiable outputs."
                        ),
                    }

                    fallback_candidates = [
                        url for url in FALLBACK_COMPANY_URLS if url.lower() not in used_company_urls
                    ][:fallback_limit]

                    for candidate_url in fallback_candidates:
                        fallback_enrich_args = {
                            "company_url": candidate_url,
                            "contact_title": "Founder or VP Sales",
                        }
                        yield {
                            "type": "tool_call",
                            "tool": "enrich_contact",
                            "args": fallback_enrich_args,
                            "auto": True,
                            "fallback": True,
                        }

                        if not _is_likely_company_url(candidate_url):
                            fallback_enrich = {
                                "error": "Rejected fallback URL for enrichment.",
                                "rejected_url": candidate_url,
                            }
                        else:
                            try:
                                fallback_enrich = enrich_contact(**fallback_enrich_args)
                            except Exception as exc:
                                fallback_enrich = {"error": str(exc)}

                        total_enrich_calls += 1
                        yield {
                            "type": "tool_result",
                            "tool": "enrich_contact",
                            "result": fallback_enrich,
                            "auto": True,
                            "fallback": True,
                        }

                        if not isinstance(fallback_enrich, dict) or fallback_enrich.get("error"):
                            continue

                        current_lead = {
                            "name": fallback_enrich.get("name", ""),
                            "title": fallback_enrich.get("title", "Founder or VP Sales"),
                            "company": fallback_enrich.get("company", ""),
                            "company_url": fallback_enrich.get("company_url", candidate_url),
                            "email": fallback_enrich.get("email", ""),
                            "signals": fallback_enrich.get("signals", []),
                            "description": fallback_enrich.get("description", ""),
                            "contact_context": _build_contact_context(
                                fallback_enrich.get("description", ""),
                                fallback_enrich.get("recent_content", ""),
                            ),
                        }

                        if not _has_strong_personalization_signal(current_lead):
                            yield {
                                "type": "thought",
                                "content": "Skipping lead — no strong personalization signal.",
                            }
                            continue

                        save_prospect_seen(
                            company=current_lead.get("company", ""),
                            company_url=current_lead.get("company_url", ""),
                            contact_name=current_lead.get("name", ""),
                            contact_title=current_lead.get("title", ""),
                            email=current_lead.get("email", ""),
                            icp=icp,
                        )

                        pipeline_events, saved, current_lead = _run_auto_outreach_pipeline(
                            current_lead,
                            sender_info,
                            goal,
                            icp,
                            auto=True,
                            fallback=True,
                        )
                        for event in pipeline_events:
                            yield event
                        if saved:
                            qualified_leads += 1
                            if qualified_leads >= max_leads:
                                yield {
                                    "type": "done",
                                    "reason": "qualified_reached",
                                    "content": f"Reached {qualified_leads} qualified leads.",
                                    "metrics": {
                                        "qualified_leads": qualified_leads,
                                        "search_calls": total_search_calls,
                                        "enrich_calls": total_enrich_calls,
                                        "auto_enrich_attempts": auto_enrich_attempts,
                                    },
                                }
                                return

                message = (
                    "Stopped: search exhausted without enough high-quality company domains. "
                    "Refine ICP with one vertical and concrete role targets."
                )
                yield {"type": "error", "message": message}
                yield {
                    "type": "done",
                    "reason": "search_exhausted",
                    "content": message,
                    "metrics": {
                        "qualified_leads": qualified_leads,
                        "search_calls": total_search_calls,
                        "enrich_calls": total_enrich_calls,
                        "auto_enrich_attempts": auto_enrich_attempts,
                    },
                }
                return

            if consecutive_search_calls > search_cap:
                yield {
                    "type": "thought",
                    "content": (
                        "Too many consecutive searches without enough progress. "
                        "Focus on specific company domains or recent company events next."
                    ),
                }
                consecutive_search_calls = search_cap

            try:
                fn = TOOL_DISPATCH.get(fn_name)
                if fn_name == "enrich_contact":
                    requested_url = str(fn_args.get("company_url", "")).strip()
                    if not _is_likely_company_url(requested_url):
                        result = {
                            "error": (
                                "Rejected company_url for enrich_contact. "
                                "Use a direct company homepage/about/team/contact URL, not directories/news/jobs/listings."
                            ),
                            "rejected_url": requested_url,
                        }
                    else:
                        result = fn(**fn_args) if fn else {"error": f"Unknown tool: {fn_name}"}
                else:
                    result = fn(**fn_args) if fn else {"error": f"Unknown tool: {fn_name}"}
            except Exception as exc:
                result = {"error": str(exc)}

            if fn_name == "web_search" and isinstance(result, dict) and consecutive_search_calls >= 2:
                candidate_url = _pick_company_url(result)
                if not candidate_url:
                    candidate_url = _discover_company_url_from_results(result, used_company_urls)
                if candidate_url:
                    auto_enrich_attempts += 1
                    auto_args = {
                        "company_url": candidate_url,
                        "contact_title": "VP Sales or Head of Growth",
                    }
                    yield {
                        "type": "tool_call",
                        "tool": "enrich_contact",
                        "args": auto_args,
                        "auto": True,
                    }
                    if not _is_likely_company_url(candidate_url):
                        auto_result = {
                            "error": (
                                "Rejected auto_enrich URL. "
                                "Need direct company homepage/about/team/contact URL."
                            ),
                            "rejected_url": candidate_url,
                        }
                    else:
                        try:
                            auto_result = enrich_contact(**auto_args)
                        except Exception as exc:
                            auto_result = {"error": str(exc)}

                    total_enrich_calls += 1
                    consecutive_search_calls = 0

                    if isinstance(auto_result, dict):
                        current_lead = {
                            "name": auto_result.get("name", ""),
                            "title": auto_result.get("title", auto_args["contact_title"]),
                            "company": auto_result.get("company", ""),
                            "company_url": auto_result.get("company_url", candidate_url),
                            "email": auto_result.get("email", ""),
                            "signals": auto_result.get("signals", []),
                            "description": auto_result.get("description", ""),
                            "contact_context": _build_contact_context(
                                auto_result.get("description", ""),
                                auto_result.get("recent_content", ""),
                            ),
                        }

                    yield {
                        "type": "tool_result",
                        "tool": "enrich_contact",
                        "result": auto_result,
                        "auto": True,
                    }
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(
                                {
                                    "web_search": result,
                                    "auto_enrich": auto_result,
                                    "auto_enrich_url": candidate_url,
                                }
                            ),
                        }
                    )
                    if isinstance(auto_result, dict) and not auto_result.get("error"):
                        if not _has_strong_personalization_signal(current_lead):
                            yield {
                                "type": "thought",
                                "content": "Skipping auto-enriched company — no strong personalization signal yet.",
                            }
                        else:
                            save_prospect_seen(
                                company=current_lead.get("company", ""),
                                company_url=current_lead.get("company_url", ""),
                                contact_name=current_lead.get("name", ""),
                                contact_title=current_lead.get("title", ""),
                                email=current_lead.get("email", ""),
                                icp=icp,
                            )
                            pipeline_events, saved, current_lead = _run_auto_outreach_pipeline(
                                current_lead,
                                sender_info,
                                goal,
                                icp,
                                auto=True,
                            )
                            for event in pipeline_events:
                                yield event
                            if saved:
                                qualified_leads += 1
                                if qualified_leads >= max_leads:
                                    yield {
                                        "type": "done",
                                        "reason": "qualified_reached",
                                        "content": f"Reached {qualified_leads} qualified leads.",
                                        "metrics": {
                                            "qualified_leads": qualified_leads,
                                            "search_calls": total_search_calls,
                                            "enrich_calls": total_enrich_calls,
                                            "auto_enrich_attempts": auto_enrich_attempts,
                                        },
                                    }
                                    return
                    continue

            if fn_name == "enrich_contact" and isinstance(result, dict):
                current_lead = {
                    "name": result.get("name", ""),
                    "title": result.get("title", ""),
                    "company": result.get("company", ""),
                    "company_url": result.get("company_url", ""),
                    "email": result.get("email", ""),
                    "signals": result.get("signals", []),
                    "description": result.get("description", ""),
                    "contact_context": _build_contact_context(
                        result.get("description", ""),
                        result.get("recent_content", ""),
                    ),
                }
                if not _has_strong_personalization_signal(current_lead):
                    yield {
                        "type": "thought",
                        "content": "Skipping lead — no strong personalization signal.",
                    }
                    yield {"type": "tool_result", "tool": fn_name, "result": result}
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        }
                    )
                    continue
                save_prospect_seen(
                    company=current_lead.get("company", ""),
                    company_url=result.get("company_url", ""),
                    contact_name=current_lead.get("name", ""),
                    contact_title=current_lead.get("title", ""),
                    email=current_lead.get("email", ""),
                    icp=icp,
                )

            if fn_name == "generate_email" and isinstance(result, dict):
                current_lead.update(
                    {
                        "subject": result.get("subject", ""),
                        "body": result.get("body", ""),
                        "bucket": result.get("bucket", ""),
                        "signals_used": result.get("signals_used",[])
                    }
                )

            if fn_name == "score_email" and isinstance(result, dict):
                total = int(result.get("total", 0) or 0)
                current_lead["total"] = total
                current_lead["suggestion"] = result.get("suggestion", "")
                should_skip_lead = False
                if total < 75:
                    suggestion = current_lead.get("suggestion", "")
                    yield {
                        "type": "thought",
                        "content": (
                            "Improving draft after low score: "
                            f"{suggestion or 'make it more specific and higher quality.'}"
                        ),
                    }
                    improved_goal = (
                        f"{goal}. Improve the previous email using this feedback: "
                        f"{suggestion}. Make it more specific and higher quality."
                    )
                    retry_email = generate_email(contact=current_lead, sender_info=sender_info, goal=improved_goal)
                    if isinstance(retry_email, dict) and not retry_email.get("error"):
                        current_lead.update(
                            {
                                "subject": retry_email.get("subject", current_lead.get("subject", "")),
                                "body": retry_email.get("body", current_lead.get("body", "")),
                                "bucket": retry_email.get("bucket", current_lead.get("bucket", "")),
                                "signals_used": retry_email.get("signals_used",[])
                            }
                        )
                        retry_score = score_email(
                            subject=current_lead.get("subject", ""),
                            body=current_lead.get("body", ""),
                            contact_context=current_lead.get("contact_context", ""),
                        )
                        if isinstance(retry_score, dict) and not retry_score.get("error"):
                            total = int(retry_score.get("total", total) or total)
                            current_lead["total"] = total
                            current_lead["suggestion"] = retry_score.get("suggestion", "")

                if total < 70:
                    yield {
                        "type": "thought",
                        "content": "Discarding lead — score below 70 after refinement.",
                    }
                    should_skip_lead = True

                if not current_lead.get("subject") or not current_lead.get("body"):
                    should_skip_lead = True

                if not should_skip_lead:
                    saved, event = _save_and_emit_lead(current_lead, total, icp)
                    if event:
                        yield event
                    if saved:
                        qualified_leads += 1

            yield {"type": "tool_result", "tool": fn_name, "result": result}

            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

            if qualified_leads >= max_leads:
                yield {
                    "type": "done",
                    "reason": "qualified_reached",
                    "content": f"Reached {qualified_leads} qualified leads.",
                    "metrics": {
                        "qualified_leads": qualified_leads,
                        "search_calls": total_search_calls,
                        "enrich_calls": total_enrich_calls,
                        "auto_enrich_attempts": auto_enrich_attempts,
                    },
                }
                return

        messages.extend(tool_messages)
        await asyncio.sleep(0)

    yield {
        "type": "done",
        "reason": "max_iterations",
        "content": "Stopped after max iterations.",
        "metrics": {
            "qualified_leads": qualified_leads,
            "search_calls": total_search_calls,
            "enrich_calls": total_enrich_calls,
            "auto_enrich_attempts": auto_enrich_attempts,
        },
    }
