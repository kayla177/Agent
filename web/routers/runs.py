"""Run lifecycle: trigger an agent (JSON run_id), stream its events over SSE.

Data-only SSE — node events carry {node, status}; the terminal `done` event
carries server-rendered markdown HTML; `failed` carries the error. The Next.js
client renders node lines and injects the done HTML. Every event is persisted,
so a reload/late subscriber replays from SQLite then attaches to the live queue.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from web import db, runner
from web.markdown import render_markdown

router = APIRouter()

_THROTTLE_SECONDS = 60


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def _recent_active_run(agent_key: str) -> int | None:
    last = db.latest_run(agent_key)
    if not last:
        return None
    if last["status"] == "running":
        return int(last["id"])
    try:
        started = dt.datetime.fromisoformat(last["started_at"])
        age = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
    except ValueError:
        return None
    return int(last["id"]) if age < _THROTTLE_SECONDS else None


@router.post("/agents/{agent_key}/run")
async def trigger_run(agent_key: str, send: str = "0", force: str = "0"):
    do_send = send in ("1", "true", "on")
    if force not in ("1", "true", "on"):
        reuse = _recent_active_run(agent_key)
        if reuse is not None:
            return JSONResponse({"run_id": reuse, "reused": True})
    run_id = runner.start_run(agent_key, do_send)
    return JSONResponse({"run_id": run_id})


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, request: Request):
    last_seen = 0
    hdr = request.headers.get("last-event-id")
    if hdr and hdr.isdigit():
        last_seen = int(hdr)

    async def gen():
        nonlocal last_seen
        queue = runner.manager.subscribe(run_id)
        try:
            for ev in db.get_node_events(run_id, after_id=last_seen):
                yield _sse("node", {"node": ev["node"], "status": ev["status"]}, event_id=ev["id"])
                last_seen = max(last_seen, ev["id"])

            run = db.get_run(run_id)
            if run and run["status"] != "running":
                yield _terminal_frame(run)
                return

            while True:
                ev = await queue.get()
                if ev is None:
                    break
                etype = ev.get("type")
                if etype in ("node_start", "node_finish"):
                    db_id = ev.get("db_id")
                    if db_id and db_id <= last_seen:
                        continue
                    status = "start" if etype == "node_start" else ("error" if ev.get("error") else "finish")
                    yield _sse("node", {"node": ev["node"], "status": status}, event_id=db_id)
                    if db_id:
                        last_seen = max(last_seen, db_id)
                elif etype == "run_finished":
                    yield _sse("done", {"html": render_markdown(ev.get("message", "")), "status": "success"})
                elif etype == "run_error":
                    yield _sse("failed", {"error": ev.get("error", "unknown error")})
        finally:
            runner.manager.unsubscribe(run_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _terminal_frame(run: dict) -> str:
    if run["status"] == "error":
        return _sse("failed", {"error": run["error"] or "run failed"})
    return _sse("done", {"html": render_markdown(run["output_message"] or ""), "status": run["status"]})
