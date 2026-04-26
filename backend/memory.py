"""SQLite persistence for Prospector leads and sessions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "prospector_memory.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str) -> str:
    return (value or "").strip().lower()


def build_lead_key(
    company: str = "",
    company_url: str = "",
    contact_name: str = "",
    contact_title: str = "",
    email: str = "",
) -> str:
    """Create a stable dedupe key even when no public email is available."""
    return "|".join(
        [
            _clean(company_url),
            _clean(company),
            _clean(contact_name),
            _clean(contact_title),
            _clean(email),
        ]
    )


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                lead_key TEXT,
                company TEXT,
                company_url TEXT,
                contact_name TEXT,
                enrichment_json TEXT,
                email_subject TEXT,
                email_body TEXT,
                score REAL DEFAULT 0,
                status TEXT DEFAULT 'drafted',
                created_at TEXT,
                icp_used TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                icp TEXT,
                sender_info TEXT,
                goal TEXT,
                leads_found INTEGER DEFAULT 0,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS prospects_seen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT UNIQUE,
                company TEXT,
                company_url TEXT,
                contact_name TEXT,
                contact_title TEXT,
                email TEXT,
                seen_at TEXT,
                icp_used TEXT
            );
            """
        )
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        if "lead_key" not in existing_columns:
            conn.execute("ALTER TABLE leads ADD COLUMN lead_key TEXT")
        if "company_url" not in existing_columns:
            conn.execute("ALTER TABLE leads ADD COLUMN company_url TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_lead_key ON leads(lead_key)"
        )


def save_prospect_seen(
    company: str,
    company_url: str,
    contact_name: str = "",
    contact_title: str = "",
    email: str = "",
    icp: str = "",
) -> bool:
    init_db()
    dedupe_key = "|".join(
        [
            (company_url or "").strip().lower(),
            (email or "").strip().lower(),
            (contact_name or "").strip().lower(),
            (contact_title or "").strip().lower(),
            (icp or "").strip().lower(),
        ]
    )
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO prospects_seen
            (dedupe_key, company, company_url, contact_name, contact_title, email, seen_at, icp_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dedupe_key,
                company,
                company_url,
                contact_name,
                contact_title,
                email,
                _now_iso(),
                icp,
            ),
        )
        return cur.rowcount > 0


def already_contacted(email: str = "", lead_key: str = "") -> bool:
    email_value = _clean(email)
    lead_key_value = _clean(lead_key)
    if not email_value and not lead_key_value:
        return False
    init_db()
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM leads
            WHERE (? != '' AND lower(ifnull(email, '')) = ?)
               OR (? != '' AND lower(ifnull(lead_key, '')) = ?)
            LIMIT 1
            """,
            (email_value, email_value, lead_key_value, lead_key_value),
        ).fetchone()
        return row is not None


def save_lead(
    email: str,
    company: str,
    contact_name: str,
    company_url: str,
    contact_title: str,
    enrichment: dict,
    subject: str,
    draft: str,
    score: float,
    icp: str = "",
    status: str = "drafted",
) -> bool:
    init_db()
    email_value = _clean(email) or None
    lead_key = build_lead_key(
        company=company,
        company_url=company_url,
        contact_name=contact_name,
        contact_title=contact_title,
        email=email_value or "",
    )
    if not lead_key.strip("|"):
        return False
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO leads
            (email, lead_key, company, company_url, contact_name, enrichment_json, email_subject, email_body, score, status, created_at, icp_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email_value,
                lead_key,
                company,
                company_url,
                contact_name,
                json.dumps(enrichment, ensure_ascii=True),
                subject,
                draft,
                float(score or 0),
                status,
                _now_iso(),
                icp,
            ),
        )
        return cur.rowcount > 0


def get_all_leads(limit: int = 100) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, lead_key, company, company_url, contact_name, enrichment_json, email_subject, email_body,
                   score, status, created_at, icp_used
            FROM leads
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        item = dict(row)
        raw = item.get("enrichment_json") or "{}"
        try:
            item["enrichment"] = json.loads(raw)
        except json.JSONDecodeError:
            item["enrichment"] = {}
        out.append(item)
    return out


def get_stats() -> dict:
    init_db()
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        avg = conn.execute("SELECT AVG(score) FROM leads WHERE score > 0").fetchone()[0]
        seen = conn.execute("SELECT COUNT(*) FROM prospects_seen").fetchone()[0]

    avg_score = round(float(avg or 0), 1)
    return {
        "total_leads": int(total),
        "prospects_seen": int(seen),
        "avg_score": avg_score,
        "message": (
            f"Prospector memory: {int(total)} qualified leads saved, "
            f"{int(seen)} prospects seen."
        ),
    }
