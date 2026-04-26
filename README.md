# ⛏ Prospector

**Autonomous B2B Lead Intelligence & Cold Outreach Agent**

> Give it an ICP. It finds real companies, enriches them with live signals, and writes cold emails a human would be proud to send — then remembers every contact forever so you never double-email anyone.

---

## The Problem

B2B outreach is broken in two ways at once.

**It's expensive.** Businesses pay outreach agencies $1,000–3,000/month to do research, find leads, and write emails. For most startups and SMBs, that's not a line item they can justify — so they skip outreach entirely, or they do it badly themselves.

**It's generic.** The tools that do exist — Apollo, Instantly, Hunter — spray templated emails at lists of contacts. Everyone gets the same five sentences with a `{{first_name}}` variable swapped in. Open rates crater. Reply rates are near zero. Recipients report it as spam. The whole pipeline is poisoned.

The root cause of both problems is the same: **personalization at scale has been impossible without human labor.** You can have cheap and generic, or expensive and personal. You cannot have both.

Prospector removes that constraint.

---

## What Prospector Does

Prospector is an autonomous agent that handles the full B2B prospecting pipeline — from ICP to inbox-ready email — without a human in the loop.

**1. Intelligent Company Discovery**
Given a natural-language ICP ("CTOs at Series A fintech startups, 10–50 employees, using Stripe or Plaid"), Prospector searches the live web for matching companies. It uses Serper (Google Search API) with DuckDuckGo as fallback, and filters out aggregator noise — LinkedIn, TechCrunch, Crunchbase, YC — to find real company homepages.

**2. Live Signal Enrichment**
For each candidate, Prospector scrapes the company's public website and extracts personalization signals: a recent product launch, a funding announcement, a hiring push, an AI-native positioning, a SaaS pricing model. These are not inferred from a stale database — they are pulled fresh from the page at the moment of the run.

**3. Hyper-Personalized Email Drafting**
Emails are written by Claude Sonnet (via TokenRouter) using a two-layer strategy adapted from PitchFlows v6:
- **8-bucket recipient classifier** maps the contact's title to one of eight personas (founder, CTO, sales lead, marketing, HR, investor, enterprise buyer, SMB owner), each with a different tone, hook style, and list of phrases to avoid
- **Signal-to-angle mapping** converts detected company signals into specific email angles (e.g. "funded" → post-funding execution pressure; "hiring" → operational load at growth stage)

The result: an email that could only have been written for that one person at that one company at this specific moment. Not a template. Not a fill-in-the-blank. A real email.

**4. Quality Gating**
Every draft is evaluated by Claude Haiku before it surfaces — scored 0–100 across personalization (25), clarity (25), open-rate potential (25), and CTA quality (25). Only emails scoring ≥ 70 are counted as qualified leads. The agent keeps working until it hits your requested quota.

**5. Persistent Memory**
Every qualified lead is saved to a local SQLite database keyed on email address. Prospector will never draft an email to the same address twice, across any number of future runs. The memory persists between sessions.

---

## Business Case

| What you'd pay an agency | What Prospector does |
|---|---|
| $1,500–3,000/month retainer | Runs for fractions of a cent per lead |
| 48–72 hour turnaround on lead lists | 2–4 minute run |
| Templated emails with light personalization | Emails written from live enrichment signals |
| No memory — resends to same contacts constantly | SQLite dedup — zero duplicate outreach |
| Human bottleneck — can't scale past 50 leads/day | Runs as many times as you need |

**Deploy Prospector on AgentHansa.** A business posts a lead gen task — ICP, sender info, goal. Prospector executes the full pipeline autonomously and delivers qualified leads with ready-to-send emails. The agent earns per task completed. It keeps working after the hackathon ends.

---

## Demo

[Watch 60-second demo](#) ← add link before submission

---

## Judging Rubric Alignment

| Dimension | Weight | How Prospector Wins |
|---|---|---|
| **Business Value** | 30% | Replaces a $1,500–3,000/month agency retainer. Addresses a real pain every B2B founder knows. |
| **Output Quality** | 30% | 8-bucket classifier + live signal enrichment = emails scored 70–90/100 by Claude Haiku. Quality-gated — low-scoring drafts never surface. |
| **Innovation** | 20% | Real-time enrichment pipeline, auto-enrich heuristic when search loops, model fallback chain (Sonnet → Haiku → gpt-4o-mini), SQLite cross-session memory. |
| **Long-term Potential** | 20% | Deployable on AgentHansa. Earns per task. Memory compounds — gets smarter about which ICPs produce quality leads over time. |

---

## How It Works

```
User inputs ICP + sender info
        │
        ▼
┌─────────────────────────────────┐
│         Agent Loop              │  ← claude-sonnet-4-20250514
│                                 │     via TokenRouter
│  1. web_search(icp_query)       │
│  2. enrich_contact(company_url) │
│  3. generate_email(contact)     │
│  4. score_email(subject, body)  │
│                                 │
│  if score >= 70 → save to DB    │
│  repeat until max_leads hit     │
└─────────────────────────────────┘
        │
        ▼
  SQLite memory (prospector_memory.db)
  SSE stream → React UI (live agent stream + lead cards)
```

Every tool call and thought is streamed to the frontend in real time via Server-Sent Events.

---

## Architecture

| Layer | Technology |
|---|---|
| Agent LLM | `claude-sonnet-4-20250514` via TokenRouter |
| Scorer LLM | `claude-haiku-4-5` via TokenRouter |
| API fallback chain | Sonnet → Haiku → gpt-4o-mini |
| Backend | FastAPI + Server-Sent Events |
| Frontend | React + Vite |
| Memory | SQLite (`backend/prospector_memory.db`) |
| Web search | Serper.dev + DuckDuckGo fallback |
| Enrichment | BeautifulSoup4 (live page scraping) |

---

## Project Layout

```
prospector/
├── backend/
│   ├── main.py              # FastAPI app + SSE /prospect endpoint
│   ├── agent.py             # Agentic loop: search → enrich → draft → score
│   ├── memory.py            # SQLite: leads, prospects_seen, sessions
│   ├── models.py            # Pydantic request schema
│   └── tools/
│       ├── search.py        # Serper + DuckDuckGo fallback
│       ├── enrich.py        # Live website scraper + signal detector
│       ├── email_gen.py     # 8-bucket classifier + relevance-mapped drafting
│       └── email_score.py   # 4-dimension evaluator via Haiku
└── frontend/
    └── src/
        ├── App.jsx           # UI container + SSE stream handler
        ├── App.css           # Dark, monospace, minimal
        └── components/
            ├── AgentStream.jsx  # Live thought + tool call feed
            └── LeadCard.jsx     # Expandable lead card with email preview
```

---

## Run Locally

**Backend**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env    # add your keys
uvicorn main:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev             # opens at localhost:5173
```

---

## Environment Variables

```bash
TOKENROUTER_API_KEY=your_key        # required — get from tokenrouter.com
TOKENROUTER_BASE_URL=https://api.tokenrouter.com/v1
SERPER_API_KEY=your_key             # optional — falls back to DuckDuckGo
PROSPECTOR_AGENT_MODEL=claude-sonnet-4-20250514  # optional override
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/stats` | Memory stats: total leads, avg score, prospects seen |
| GET | `/leads` | All saved leads from SQLite |
| POST | `/prospect` | SSE stream — runs the full agent pipeline |

**POST `/prospect` body:**
```json
{
  "icp": "CTOs at Series A fintech startups, 10-50 employees",
  "sender_info": "PitchFlows — AI cold email agent, 200+ founder users",
  "goal": "Book a 15-minute discovery call",
  "max_leads": 3
}
```

**SSE event types streamed back:**
- `thought` — agent reasoning text
- `tool_call` — tool name + args
- `tool_result` — tool output
- `lead` — lead found (fires again after scoring with updated score)
- `done` — completion with metrics
- `error` — failure message

---

## Built By

**Tarandeep Plaha**
MS Computer Engineering · Arizona State University
Founder, PitchFlows (pitchflows.online)

*Built for AI Agent Economy Hackathon · April 25, 2026*
*Hosted by AgentHansa × FluxA × TokenRouter × BotLearn*
