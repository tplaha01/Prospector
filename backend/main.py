"""Prospector FastAPI entrypoint with SSE endpoint."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent import run_prospector
from memory import get_all_leads, get_stats, init_db
from models import ProspectRequest

# Always load the backend-local .env, even when uvicorn is launched from another cwd.
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)
init_db()

app = FastAPI(title="Prospector API", version="1.0.0")

default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://prospector-six.vercel.app",
]
env_origins = [
    origin.strip()
    for origin in os.getenv("PROSPECTOR_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
allowed_origins = list(dict.fromkeys(default_origins + env_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health() -> dict:
    return {"status": "Prospector running", "version": "1.0.0"}


@app.get("/stats")
def stats() -> dict:
    return get_stats()


@app.get("/leads")
def leads() -> dict:
    return {"leads": get_all_leads(), "stats": get_stats()}


@app.post("/prospect")
async def prospect(req: ProspectRequest) -> StreamingResponse:
    async def stream() -> AsyncGenerator[str, None]:
        try:
            async for event in run_prospector(
                icp=req.icp,
                sender_info=req.sender_info,
                goal=req.goal,
                max_leads=req.max_leads,
            ):
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENVIRONMENT", "").lower() != "production",
    )
