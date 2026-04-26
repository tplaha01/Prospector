"""Email generation adapted from PitchFlows v6 concepts for Prospector."""

from __future__ import annotations

import json
import os
import re

from openai import OpenAI

def _client() -> OpenAI:
    api_key = os.getenv("TOKENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing TOKENROUTER_API_KEY in environment.")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1"),
    )


def _candidate_models() -> list[str]:
    primary = os.getenv("PROSPECTOR_EMAIL_GEN_MODEL", "claude-sonnet-4-20250514").strip()
    fallbacks = [
        os.getenv("PROSPECTOR_EMAIL_GEN_MODEL_FALLBACK", "claude-haiku-4-5").strip(),
        "gpt-4o-mini",
    ]
    seen: set[str] = set()
    models: list[str] = []
    for model in [primary, *fallbacks]:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models

RECIPIENT_BUCKETS = {
    "founder": {
        "tone": "Peer-to-peer, direct, specific.",
        "hook": "Lead with an insight tied to their product momentum.",
        "avoid": "Corporate filler or fluffy intros.",
    },
    "cto": {
        "tone": "Technical and concise.",
        "hook": "Reference architecture, integrations, or execution constraints.",
        "avoid": "Marketing language without technical substance.",
    },
    "sales_lead": {
        "tone": "Pipeline and outcomes focused.",
        "hook": "Open with a concrete GTM outcome.",
        "avoid": "Long setup before value.",
    },
    "marketing": {
        "tone": "Growth and conversion focused.",
        "hook": "Tie to audience, conversion, or campaign efficiency.",
        "avoid": "Overly technical implementation details.",
    },
    "hr": {
        "tone": "Human and practical.",
        "hook": "Highlight time saved and candidate quality impact.",
        "avoid": "Hard-sales language.",
    },
    "investor": {
        "tone": "Thesis and leverage aware.",
        "hook": "Reference portfolio fit or market signal.",
        "avoid": "Generic fundraising buzzwords.",
    },
    "enterprise_buyer": {
        "tone": "Risk-aware and implementation-ready.",
        "hook": "Lead with reliability, compliance, or integration readiness.",
        "avoid": "Scrappy startup framing.",
    },
    "smb_owner": {
        "tone": "Plain language, immediate value.",
        "hook": "Make outcome and effort crystal clear.",
        "avoid": "Jargon or abstract positioning.",
    },
}

SIGNAL_ANGLES = {
    "hired": "Reference hiring velocity and operational load.",
    "funded": "Reference post-funding execution pressure and speed.",
    "saas": "Reference recurring revenue and funnel efficiency.",
    "ai": "Reference AI product maturity and differentiation pressure.",
    "b2b": "Reference enterprise workflow and buyer complexity.",
}


def classify_recipient(title: str) -> str:
    t = (title or "").lower()
    if any(x in t for x in ["founder", "ceo", "co-founder", "owner", "president"]):
        return "founder"
    if any(x in t for x in ["cto", "vp engineering", "head of engineering", "architect"]):
        return "cto"
    if any(x in t for x in ["sales", "revenue", "account executive", "sdr", "bdr"]):
        return "sales_lead"
    if any(x in t for x in ["marketing", "growth", "demand gen", "brand"]):
        return "marketing"
    if any(x in t for x in ["hr", "people", "talent", "recruit"]):
        return "hr"
    if any(x in t for x in ["investor", "venture", "partner", "principal", "vc"]):
        return "investor"
    if any(x in t for x in ["procurement", "enterprise", "director", "vp"]):
        return "enterprise_buyer"
    return "smb_owner"


def _parse_json_payload(content: str) -> dict:
    raw = (content or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass
    return {}


def generate_email(contact: dict, sender_info: str, goal: str) -> dict:
    title = contact.get("title", "")
    bucket = classify_recipient(title)
    bucket_cfg = RECIPIENT_BUCKETS[bucket]
    signals = contact.get("signals", []) or []
    description = contact.get("description", "") or contact.get("company_description", "")
    recent_context = contact.get("recent_content", "") or contact.get("contact_context", "")

    angles = [SIGNAL_ANGLES[s] for s in signals if s in SIGNAL_ANGLES]
    angle_block = "\n".join(f"- {a}" for a in angles) or "- No strong signals found. Use the most specific known detail."

    prompt = f"""Write a personalized cold outreach email.

    RECIPIENT
    - Name: {contact.get('name', 'there')}
    - Title: {title}
    - Company: {contact.get('company', '')}
    - Company description: {description}
    - Recent content: {recent_context[:280]}
    - Signals: {', '.join(signals) if signals else 'none'}

SENDER: {sender_info}
GOAL: {goal}

BUCKET: {bucket}
- Tone: {bucket_cfg['tone']}
- Hook guidance: {bucket_cfg['hook']}
- Avoid: {bucket_cfg['avoid']}

RELEVANCE ANGLES
{angle_block}

RULES
1) Subject <= 7 words and specific.
2) First sentence must cite one concrete signal and mention the company by name.
2.1) FIRST SENTENCE MUST reference a specific, verifiable detail. If not, still generate a high-quality email using available context.
3) Body must be <= 100 words and max 3 sentences.
4) CTA must be low-friction yes/no or 15-minute ask.
5) Ban phrases: "I hope this finds you well", "I wanted to reach out", "touch base", "synergy", "game-changing", "leverage".
6) Return strict JSON only.

OUTPUT JSON
{{"subject": "...", "body": "..."}}"""

    try:
        client = _client()
        response = None
        last_error: Exception | None = None
        for model in _candidate_models():
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=450,
                    response_format={"type": "json_object"},
                )
                break
            except Exception as exc:
                last_error = exc
                if "model_not_found" in str(exc):
                    continue
                raise
        if response is None:
            raise last_error or RuntimeError("No model available for email generation.")
        content = response.choices[0].message.content or "{}"
        data = _parse_json_payload(content)
        if not data:
            print("EMAIL GEN PARSE ERROR:", content)

        if "error" in data:
            print("EMAIL GEN MODEL ERROR:", data)
            return {
                "subject": "",
                "body": "",
                "bucket": bucket,
                "signals_used": signals,
                "error": data.get("error"),
            }

        subject = str(data.get("subject", "")).strip()
        body = str(data.get("body", "")).strip()
        if subject and body:
            return {
                "subject": subject,
                "body": body,
                "bucket": bucket,
                "signals_used": signals,
            }

    except Exception:
        company = (contact.get("company") or "your team").strip()
        name = (contact.get("name") or "there").strip()
        short_sender = (sender_info or "").strip().split(".")[0][:140]
        subject = f"{company}: quick pipeline idea"[:60]
        body = (
            f"Hi {name}, based on your current GTM momentum at {company}, "
            f"I think we can help your team drive more qualified outbound replies quickly. "
            f"{short_sender}. Open to a quick 15-minute chat next week about {goal.lower()}?"
        )[:600]
        return {
            "subject": subject,
            "body": body,
            "bucket": bucket,
            "signals_used": signals,
            "fallback_generated": True,
        }

    company = (contact.get("company") or "your team").strip()
    name = (contact.get("name") or "there").strip()
    short_sender = (sender_info or "").strip().split(".")[0][:140]
    subject = f"{company}: quick pipeline idea"[:60]
    body = (
        f"Hi {name}, based on your current GTM momentum at {company}, "
        f"I think we can help your team drive more qualified outbound replies quickly. "
        f"{short_sender}. Open to a quick 15-minute chat next week about {goal.lower()}?"
    )[:600]
    return {
        "subject": subject,
        "body": body,
        "bucket": bucket,
        "signals_used": signals,
        "fallback_generated": True,
    }
