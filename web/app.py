"""FastAPI application for the daily-agents web control center.

Local-only, single-user. Launch with::

    uv run uvicorn web.app:app --host 127.0.0.1 --port 8000
    # or
    uv run python -m web

Imports the existing ``config`` / ``agents`` / ``shell`` packages; it never
touches the CLI/launchd path. PROJECT_ROOT is put on sys.path first (mirroring
the run scripts) so ``import config`` resolves no matter how uvicorn is started.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from web import db  # noqa: E402
from web.routers import applications, charts, pages, resume, runs, settings  # noqa: E402

app = FastAPI(title="daily-agents control center")

_STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

app.include_router(pages.router)
app.include_router(runs.router)
app.include_router(settings.router)
app.include_router(applications.router)
app.include_router(charts.router)
app.include_router(resume.router)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
