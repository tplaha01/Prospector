"""Cold email scorer for Prospector using TokenRouter Claude Haiku."""

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
    primary = os.getenv("PROSPECTOR_EMAIL_SCORE_MODEL", "claude-haiku-4-5").strip()
    fallbacks = [
        os.getenv("PROSPECTOR_EMAIL_SCORE_MODEL_FALLBACK", "gpt-4o-mini").strip(),
        "claude-sonnet-4-20250514",
    ]
    seen: set[str] = set()
    models: list[str] = []
    for model in [primary, *fallbacks]:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


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


def score_email(subject: str, body: str, contact_context: str = "") -> dict:
    prompt = f"""You are evaluating one cold outreach email.

Subject: {subject}
Body: {body}
Contact context: {contact_context or 'not provided'}

Score each dimension 0-25:
1) personalization: specific to this recipient and context
2) clarity: value proposition is immediately understandable
3) open_rate: subject likely to earn an open
4) cta: ask is clear, low-friction, and replyable

Also provide one improvement suggestion under 20 words.

Return strict JSON only:
{{
  "total": 0,
  "personalization": 0,
  "clarity": 0,
  "open_rate": 0,
  "cta": 0,
  "suggestion": ""
}}"""

    try:
        client = _client()
        response = None
        last_error: Exception | None = None
        for model in _candidate_models():
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=220,
                    response_format={"type": "json_object"},
                )
                break
            except Exception as exc:
                last_error = exc
                if "model_not_found" in str(exc):
                    continue
                raise
        if response is None:
            raise last_error or RuntimeError("No model available for email scoring.")
        data = _parse_json_payload(response.choices[0].message.content or "{}")
        if not data:
            raise ValueError("Score model returned non-JSON payload.")

        personalization = int(data.get("personalization", 0) or 0)
        clarity = int(data.get("clarity", 0) or 0)
        open_rate = int(data.get("open_rate", 0) or 0)
        cta = int(data.get("cta", 0) or 0)
        total = int(data.get("total", 0) or 0)
        if total <= 0:
            total = personalization + clarity + open_rate + cta

        return {
            "total": max(0, min(100, total)),
            "personalization": max(0, min(25, personalization)),
            "clarity": max(0, min(25, clarity)),
            "open_rate": max(0, min(25, open_rate)),
            "cta": max(0, min(25, cta)),
            "suggestion": str(data.get("suggestion", "")).strip(),
        }
    except Exception:
        personalization = 18 if contact_context else 12
        clarity = 18 if 20 <= len(body or "") <= 500 else 12
        open_rate = 14 if 3 <= len((subject or "").split()) <= 8 else 10
        cta = 16 if "?" in (body or "") or "15" in (body or "") else 10
        total = personalization + clarity + open_rate + cta
        return {
            "total": max(0, min(100, total)),
            "personalization": max(0, min(25, personalization)),
            "clarity": max(0, min(25, clarity)),
            "open_rate": max(0, min(25, open_rate)),
            "cta": max(0, min(25, cta)),
            "suggestion": "Add one concrete company-specific signal in line one.",
            "fallback_scored": True,
        }
