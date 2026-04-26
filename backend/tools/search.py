"""Search tool with Serper primary and DuckDuckGo fallback."""

from __future__ import annotations

import os

import requests

SERPER_URL = "https://google.serper.dev/search"
DDG_URL = "https://api.duckduckgo.com/"
BLOCKED_HOST_FRAGMENTS = [
    "linkedin.com",
    "crunchbase.com",
    "techcrunch.com",
    "forbes.com",
    "fortune.com",
    "businessinsider.com",
    "finance.yahoo.com",
    "fiercehealthcare.com",
    "cnbc.com",
    "therecursive.com",
    "startup-weekly.com",
    "venturebeat.com",
    "prnewswire.com",
    "reddit.com",
    "wikipedia.org",
    "glassdoor.com",
    "indeed.com",
    "greenhouse.io",
    "lever.co",
    "wellfound.com",
    "builtinnyc.com",
    "jobleads.com",
    "jobs.generalcatalyst.com",
    "jobs.primary.vc",
    "ashbyhq.com",
    "ycombinator.com",
    "youtube.com",
]
BLOCKED_PATH_FRAGMENTS = [
    "/jobs",
    "/careers",
    "/blog",
    "/news",
    "/press",
    "/company/",
    "/companies/",
]


def _compact_result(title: str, url: str, snippet: str) -> dict:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "snippet": (snippet or "").strip(),
    }


def _is_preferred_result(url: str) -> bool:
    value = (url or "").strip().lower()
    return value.startswith("http") and not any(item in value for item in [*BLOCKED_HOST_FRAGMENTS, *BLOCKED_PATH_FRAGMENTS])


def _rank_results(results: list[dict]) -> list[dict]:
    preferred = [item for item in results if _is_preferred_result(item.get("url", ""))]
    remaining = [item for item in results if item not in preferred]
    return preferred + remaining


def web_search(query: str) -> dict:
    if not query or not query.strip():
        return {"results": [], "query": query, "error": "Query is required."}

    api_key = os.getenv("SERPER_API_KEY", "").strip()

    if api_key:
        try:
            response = requests.post(
                SERPER_URL,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 10},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            results = [
                _compact_result(item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
                for item in data.get("organic", [])[:8]
                if item.get("link")
            ]
            return {"results": _rank_results(results), "query": query, "source": "serper"}
        except Exception as exc:
            serper_error = str(exc)
        else:
            serper_error = ""
    else:
        serper_error = "SERPER_API_KEY not set"

    try:
        response = requests.get(
            DDG_URL,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()

        results: list[dict] = []
        topics = data.get("RelatedTopics", [])
        for topic in topics:
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append(
                    _compact_result(topic.get("Text", ""), topic.get("FirstURL", ""), topic.get("Text", ""))
                )
            for nested in topic.get("Topics", []) if isinstance(topic, dict) else []:
                if nested.get("FirstURL"):
                    results.append(
                        _compact_result(nested.get("Text", ""), nested.get("FirstURL", ""), nested.get("Text", ""))
                    )
        return {
            "results": _rank_results(results)[:8],
            "query": query,
            "source": "duckduckgo",
            "fallback_reason": serper_error,
        }
    except Exception as exc:
        return {"results": [], "query": query, "error": str(exc), "fallback_reason": serper_error}
