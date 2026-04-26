"""Contact enrichment by scraping public company pages."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SIGNAL_KEYWORDS = {
    "hired": ["we're hiring", "careers", "open roles", "join our team", "hiring"],
    "funded": ["raised", "series a", "series b", "seed round", "backed by", "funding"],
    "saas": ["free trial", "pricing", "subscription", "monthly", "dashboard"],
    "ai": ["ai", "machine learning", "llm", "gpt", "claude", "artificial intelligence"],
    "b2b": ["enterprise", "business", "teams", "api", "integrations", "workflow"],
}

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}")


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith("http://") and not u.startswith("https://"):
        return f"https://{u}"
    return u


def _company_from_title(title: str) -> str:
    if not title:
        return ""
    return title.split("|")[0].split("-")[0].strip()[:80]


def _company_from_domain(company_url: str) -> str:
    try:
        host = urlparse(company_url).netloc.lower().replace("www.", "")
    except Exception:
        return ""
    if not host:
        return ""
    base = host.split(".")[0].replace("-", " ").replace("_", " ").strip()
    return " ".join(part.capitalize() for part in base.split())[:80]


def _pick_company_name(soup: BeautifulSoup, url: str) -> str:
    for attrs in (
        {"property": "og:site_name"},
        {"name": "application-name"},
        {"name": "twitter:site"},
    ):
        meta = soup.find("meta", attrs=attrs)
        content = (meta.get("content") or "").strip() if meta else ""
        if content:
            return content[:80]

    title = soup.find("title")
    from_title = _company_from_title(title.get_text(strip=True) if title else "")
    if from_title and len(from_title.split()) <= 6:
        return from_title

    return _company_from_domain(url)


def enrich_contact(company_url: str, contact_name: str = "", contact_title: str = "") -> dict:
    url = _normalize_url(company_url)
    contact = {
        "company_url": url,
        "name": contact_name or "",
        "title": contact_title or "",
        "company": "",
        "description": "",
        "recent_content": "",
        "signals": [],
        "email": "",
        "error": None,
    }

    if not url:
        contact["error"] = "company_url is required"
        return contact

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Prospector/1.0)"},
            timeout=10,
            allow_redirects=True,
        )
        response.raise_for_status()

        html = response.text or ""
        soup = BeautifulSoup(html, "html.parser")

        contact["company"] = _pick_company_name(soup, response.url or url)

        meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if meta:
            contact["description"] = (meta.get("content") or "")[:450]

        paragraphs = []
        for p in soup.find_all("p")[:10]:
            text = p.get_text(" ", strip=True)
            if len(text) > 20:
                paragraphs.append(text)
        contact["recent_content"] = " ".join(paragraphs)[:900]

        full_text = f"{contact['description']} {contact['recent_content']}".lower()
        detected = [key for key, needles in SIGNAL_KEYWORDS.items() if any(n in full_text for n in needles)]
        contact["signals"] = detected

        emails = [e for e in EMAIL_REGEX.findall(html) if not any(x in e.lower() for x in ["noreply", "example", "test"])]
        if emails:
            contact["email"] = emails[0]

    except Exception as exc:
        contact["error"] = str(exc)

    return contact
