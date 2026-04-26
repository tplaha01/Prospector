# Prospector

Prospector is an autonomous B2B lead intelligence and cold outreach agent built from PitchFlows patterns.

## What it does

1. Searches for ICP-matching companies via Serper with DuckDuckGo fallback.
2. Enriches company pages for concrete personalization signals.
3. Drafts personalized outbound email with an 8-bucket recipient strategy.
4. Scores each draft on personalization, clarity, open rate potential, and CTA quality.
5. Stores qualified leads in SQLite and prevents duplicate outreach across sessions.

## Architecture

- Backend: FastAPI + Server-Sent Events stream
- LLM calls: TokenRouter OpenAI-compatible API
  - claude-sonnet-4-20250514 for drafting
  - claude-haiku-4-5 for scoring
- Frontend: React + Vite
- Memory: SQLite (`prospector/backend/prospector_memory.db`)

## Project layout

- `backend/main.py`: API and streaming endpoint
- `backend/agent.py`: agentic loop and tool orchestration
- `backend/memory.py`: SQLite dedup + lead history
- `backend/tools/search.py`: search tool
- `backend/tools/enrich.py`: enrichment tool
- `backend/tools/email_gen.py`: adapted PitchFlows-style generator
- `backend/tools/email_score.py`: adapted scoring evaluator
- `frontend/src/App.jsx`: UI container and stream handling
- `frontend/src/components/AgentStream.jsx`: live reasoning/tool feed
- `frontend/src/components/LeadCard.jsx`: lead rendering and expansion

## Environment

Create `backend/.env` from `.env.example`:

```
TOKENROUTER_API_KEY=your_key_here
TOKENROUTER_BASE_URL=https://api.tokenrouter.com/v1
SERPER_API_KEY=optional_for_web_search
```

## Run locally

Backend:

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173`.

## API endpoints

- `GET /` health check
- `GET /stats` memory stats
- `GET /leads` saved leads
- `POST /prospect` SSE stream of events

## Notes

- The frontend consumes SSE via `fetch` + `ReadableStream` to support POST streaming.
- The backend enforces a max iteration limit in the agent loop for safety.
- Tool functions are exception-safe and return error dictionaries on failures.
