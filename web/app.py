"""FastAPI agent service for the daily-agents control center.

Agent-only: triggers agent runs, streams their events (SSE), and reads/writes
the prefs overlay. The web UI is the Next.js app (web-next) — this service has
no Jinja, no static files, no page routes. The Next.js dev server proxies
/agents/*, /runs/*, and /prefs here (same-origin, so no CORS needed).

Launch::  uv run python -m web    # 127.0.0.1:8001
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402

import store_db  # noqa: E402
from web import db  # noqa: E402
from web.routers import prefs, runs  # noqa: E402

app = FastAPI(title="daily-agents agent service")

app.include_router(runs.router)
app.include_router(prefs.router)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    store_db.init_db()
    db.mark_stale_running_as_error()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
