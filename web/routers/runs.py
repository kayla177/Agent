"""Run lifecycle routes: trigger an agent, stream its events, fetch output.

Trigger is a plain form POST that 303-redirects to the run detail page; live
updates are delivered over SSE (``GET /runs/{id}/events``) and consumed by a
small vanilla ``EventSource`` script. Because every event is persisted, the SSE
handler replays from SQLite for reloads / late subscribers, then attaches to the
live pub/sub queue for the rest of an in-flight run.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from web import db, runner
from web.templating import render_markdown, templates

router = APIRouter()

# Don't spawn a duplicate if the same agent was triggered within this window;
# redirect to the existing run instead (guards against double-clicks).
_THROTTLE_SECONDS = 60


def _node_line_html(node: str, status: str) -> str:
    return templates.env.get_template("partials/node_event.html").render(
        node=node, status=status
    )


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    """Format one Server-Sent Event frame (data is JSON, so no raw newlines)."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def _recent_active_run(agent_key: str) -> int | None:
    """Run id to reuse: a currently-running run, or a very recent one."""
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
async def trigger_run(agent_key: str, send: str = Form("0"), force: str = Form("0")):
    do_send = send in ("1", "true", "on")
    if force not in ("1", "true", "on"):
        reuse = _recent_active_run(agent_key)
        if reuse is not None:
            return RedirectResponse(url=f"/runs/{reuse}", status_code=303)
    run_id = runner.start_run(agent_key, do_send)
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.get("/runs/{run_id}/output")
def run_output(run_id: int, request: Request):
    run = db.get_run(run_id)
    if not run:
        return templates.TemplateResponse(
            request, "partials/run_output.html", {"message": "", "missing": True}
        )
    return templates.TemplateResponse(
        request, "partials/run_output.html", {"message": run["output_message"] or ""}
    )


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, request: Request):
    last_seen = 0
    hdr = request.headers.get("last-event-id")
    if hdr and hdr.isdigit():
        last_seen = int(hdr)

    async def gen():
        nonlocal last_seen
        queue = runner.manager.subscribe(run_id)  # buffer live events from now
        try:
            # 1) Replay persisted node events the client hasn't seen.
            for ev in db.get_node_events(run_id, after_id=last_seen):
                status = ev["status"]
                yield _sse(
                    "node",
                    {"node": ev["node"], "status": status,
                     "html": _node_line_html(ev["node"], status)},
                    event_id=ev["id"],
                )
                last_seen = max(last_seen, ev["id"])

            # 2) If the run already finished, emit the terminal frame and stop.
            run = db.get_run(run_id)
            if run and run["status"] != "running":
                yield _terminal_frame(run)
                return

            # 3) Otherwise attach to the live stream until the sentinel.
            while True:
                ev = await queue.get()
                if ev is None:  # sentinel: driver finished
                    break
                etype = ev.get("type")
                if etype in ("node_start", "node_finish"):
                    db_id = ev.get("db_id")
                    if db_id and db_id <= last_seen:
                        continue  # already replayed
                    status = "start" if etype == "node_start" else (
                        "error" if ev.get("error") else "finish"
                    )
                    yield _sse(
                        "node",
                        {"node": ev["node"], "status": status,
                         "html": _node_line_html(ev["node"], status)},
                        event_id=db_id,
                    )
                    if db_id:
                        last_seen = max(last_seen, db_id)
                elif etype == "run_finished":
                    yield _sse(
                        "done",
                        {"html": render_markdown(ev.get("message", "")), "status": "success"},
                    )
                elif etype == "run_error":
                    yield _sse(
                        "failed", {"error": ev.get("error", "unknown error")}
                    )
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
    return _sse(
        "done",
        {"html": render_markdown(run["output_message"] or ""), "status": run["status"]},
    )
