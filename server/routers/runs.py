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

from server import db, runner
from server.markdown import render_markdown

router = APIRouter()

_THROTTLE_SECONDS = 60

#: Agents this endpoint refuses, and where to go instead.
#:
#: `job_applier` holds a live Playwright context across two graph nodes, and
#: Playwright's sync API is thread-affine at the greenlet level. The driver
#: behind this endpoint (`runner.start_run` → `graph.astream`) runs sync nodes in
#: the event loop's default executor — a POOL — so `fetch_form` opening the
#: browser on one worker and `fill` typing into it from another is a
#: `greenlet.error`, not a race. `agents/job_applier/session.py` documents the
#: mechanism; `server/applier_run.py` is the driver that respects it.
#:
#: Two further reasons this is a refusal rather than a documented convention:
#: nothing on this path registers the run in `session._SESSIONS`, so
#: `/data/jobs/assisted-apply/close` could never close the window it left open;
#: and a JSON body on this endpoint is passed straight through as the graph's
#: initial state, which for this agent includes `form_url` — a URL handed to a
#: browser carrying her live ATS cookies. `fetch_form.override_refusal` now
#: bounds where that can point, and this refusal means the request does not get
#: that far. (Unreachable from the UI either way: `job_applier` is not in
#: `web-next/src/lib/agents.ts`'s `DASHBOARD_KEYS`.)
_WRONG_ENTRY_POINT: dict[str, str] = {
    "job_applier": (
        "assisted apply cannot be started through the generic run endpoint: it "
        "holds a live browser across two nodes and Playwright's sync API is "
        "thread-affine, so the pooled graph driver behind this route cannot run "
        "it, and a run started here is never registered as a closeable browser "
        "session. Use POST /data/jobs/assisted-apply instead."
    ),
}


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
async def trigger_run(agent_key: str, request: Request, send: str = "0", force: str = "0"):
    do_send = send in ("1", "true", "on")

    # Refused BEFORE the body is read and before any run row is created, so a
    # rejected request leaves no trace in `runs` and its body is never looked at.
    wrong = _WRONG_ENTRY_POINT.get(agent_key)
    if wrong is not None:
        return JSONResponse({"error": wrong}, status_code=409)

    # Optional JSON body: {"input": {...}} seeds the graph's initial state (e.g.
    # {"job_id": ...} for the per-job resume generator). Parsed defensively so
    # the common bodyless callers (the scheduled-agent buttons) are unaffected.
    agent_input: dict | None = None
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("input"), dict):
            agent_input = body["input"]
    except Exception:
        agent_input = None

    # Parameterized runs are always fresh — never collapse two distinct inputs
    # (e.g. resumes for different jobs) onto one reused run.
    if agent_input is None and force not in ("1", "true", "on"):
        reuse = _recent_active_run(agent_key)
        if reuse is not None:
            return JSONResponse({"run_id": reuse, "reused": True})
    run_id = runner.start_run(agent_key, do_send, agent_input)
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
            if not run:
                yield _sse("failed", {"error": f"run {run_id} not found"})
                return
            if run["status"] != "running":
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
