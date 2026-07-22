"""FastAPI agent service for the daily-agents control center.

Agent-only: triggers agent runs, streams their events (SSE), and reads/writes
the prefs overlay. The web UI is the Next.js app (web-next) — this service has
no Jinja, no static files, no page routes. The Next.js dev server proxies
/agents/*, /runs/*, and /prefs here (same-origin, so no CORS needed).

Launch::  uv run python -m server    # 127.0.0.1:8001
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402

import store_db  # noqa: E402
from server import db  # noqa: E402
from server.routers import applications, jobs, prefs, resume, runs, stocks  # noqa: E402

app = FastAPI(title="daily-agents agent service")

app.include_router(runs.router)
app.include_router(prefs.router)
app.include_router(resume.router)
app.include_router(applications.router)
app.include_router(jobs.router)
app.include_router(stocks.router)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    store_db.init_db()
    db.mark_stale_running_as_error()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/agents")
def agents() -> dict:
    """UI metadata for every agent — the single source (registry) consumed by the
    frontend, replacing the hand-maintained web-next agents list."""
    from agents.registry import list_specs

    return {"agents": [spec.to_meta() for spec in list_specs()]}
