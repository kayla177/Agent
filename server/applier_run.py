"""Drive the assisted-apply graph on the browser's own thread, and keep its report.

`server/runner.py` is the generic driver for the other six agents: it consumes
`graph.astream(...)` on the event loop and lets LangGraph run the sync nodes in
the loop's default executor. That is fine for every agent whose state is strings
and dicts, and it is **not** fine for this one. The applier's state carries a live
Playwright context created in `fetch_form` and used again in `fill`, and
Playwright's sync API is thread-affine down to a greenlet switch — so two nodes
landing on two different pool workers is not slow or racy, it is a hard error.
`agents/job_applier/session.py` explains the mechanism in full.

So the applier gets its own driver, on `session`'s single thread, and the generic
one is left completely alone — no spec flags, no shared-executor surgery, no
change to the path the other six agents already run on.

What is deliberately IDENTICAL to `server/runner.py`
===================================================
The run's bookkeeping, because `RunStream` and `GET /runs/{id}/events` must not
be able to tell the difference: a `runs` row via `db.create_run`, a `node_events`
row per node via `db.add_node_event`, the same `{"type": "node_start" | ...}`
events published to the same `runner.manager`, and `db.finish_run` at the end.
The SSE endpoint replays from SQLite and then attaches to the manager's queue, so
both halves are needed — persisting only would leave a live subscriber hanging
after the events it already replayed.

Publishing crosses a thread boundary, which the generic driver never has to do:
`manager.publish` calls `asyncio.Queue.put_nowait`, which is not thread-safe. So
every publish goes through `loop.call_soon_threadsafe`, with the loop captured by
the (async) endpoint that started the run.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module never touches a page — it hands `session` a callable and reads the
graph's own output. `test_the_driver_obeys_the_one_rule` runs the same AST guard
the whole `agents/job_applier/` package is held to over this file.
"""

from __future__ import annotations

import asyncio
import dataclasses
import traceback
from collections import OrderedDict
from typing import Any, Callable, Optional

from agents.job_applier import session
from server import db, runner

#: How many finished runs' reports stay in memory. The UI fetches one, once,
#: immediately after its run ends; the rest of the window is only there so a
#: refresh mid-read still finds it. Reports are a few KB of plain dataclasses,
#: and they are ALSO on disk as the run's rendered `output_message` — so this is
#: a convenience cache and losing it (a server restart) costs the checklist, not
#: the record.
_REPORT_CACHE_SIZE = 8

_REPORTS: "OrderedDict[int, Any]" = OrderedDict()

#: Test seam. Overridden by the suite so nothing here can ever open a browser:
#: the real value imports Playwright-backed nodes the moment it is called.
_graph_factory: Optional[Callable[[], Any]] = None


def _build_graph() -> Any:
    if _graph_factory is not None:
        return _graph_factory()
    from agents.job_applier.graph import build_job_applier_graph  # noqa: PLC0415

    return build_job_applier_graph(send=False)


def _release(state: dict) -> bool:
    from agents.job_applier.graph import release_browser  # noqa: PLC0415

    return release_browser(state)


def _remember_report(run_id: int, report: Any) -> None:
    _REPORTS[run_id] = report
    while len(_REPORTS) > _REPORT_CACHE_SIZE:
        _REPORTS.popitem(last=False)


def report_payload(run_id: int) -> dict | None:
    """The finished run's `HandoffReport` as JSON, or None if it is not held.

    The report is handed over as **data**, not as the rendered text: the UI groups
    and orders the items itself, and a UI that had to parse `render_text()` would
    break the first time a heading was reworded. `dataclasses.asdict` covers the
    stored fields; the four computed ones are added explicitly, because a
    consumer must not have to re-derive "how many of these need me" or reword the
    "nothing was submitted" headline for itself.
    """
    report = _REPORTS.get(int(run_id))
    if report is None:
        return None
    payload = dataclasses.asdict(report)
    # `asdict` preserves the container type, so `items` comes back as a TUPLE.
    # JSON has no tuples and a consumer typed against an array should not have to
    # care, so it is normalised here rather than left to the serializer.
    payload["items"] = [dataclasses.asdict(item) for item in report.items]
    payload["headline"] = report.headline()
    payload["instruction"] = report.instruction()
    payload["summary_line"] = report.summary_line()
    payload["counts"] = report.counts()
    payload["needs_you"] = len(report.needs_you)
    payload["total"] = report.total
    return payload


def start(job_id: str, resume_path: str, loop: asyncio.AbstractEventLoop) -> int:
    """Create the run row and queue the run on the session thread. Returns run_id.

    Returns as soon as the work is queued — opening a browser and filling a form
    takes a minute or more, and the caller follows it over SSE like every other
    agent run.
    """
    run_id = db.create_run("job_applier", False)
    session.run_soon(_drive, run_id, job_id, resume_path, loop)
    return run_id


def _publish(loop: asyncio.AbstractEventLoop, run_id: int, event: dict | None) -> None:
    """Hand an event to the loop's subscribers from the session thread.

    Best-effort by design: a closed loop (server shutting down) must not turn into
    a failed run. The event is already in SQLite, which is what a reconnecting
    client replays from.
    """
    try:
        loop.call_soon_threadsafe(runner.manager.publish, run_id, event)
    except RuntimeError:
        pass


def _drive(
    run_id: int, job_id: str, resume_path: str, loop: asyncio.AbstractEventLoop
) -> None:
    """Run the whole graph here, on the session thread. Never raises.

    Mirrors `runner._drive`'s event handling exactly (see the module docstring),
    with three differences that are the reason this function exists:

      * it uses the SYNC `graph.stream`, so every node runs on this thread;
      * it closes any window still held from an earlier run BEFORE opening
        another, so there is never a moment with two half-filled applications on
        screen;
      * it decides what happens to the browser afterwards — handed to the human
        on success, closed on every failure. That is the same split `graph.py`
        documents, and `release_browser` is the function it exports for exactly
        this caller.
    """
    # Inline (already on the session thread): a previous window is closed before
    # a new one opens, never after.
    session.close()

    final_state: dict[str, Any] = {}
    try:
        graph = _build_graph()
        payload: dict[str, Any] = {"job_id": job_id}
        if resume_path:
            payload["resume_path"] = resume_path
        for mode, data in graph.stream(payload, stream_mode=["updates", "tasks"]):
            if mode == "tasks":
                name = data.get("name", "?")
                if "result" in data:
                    err = data.get("error")
                    status = "error" if err else "finish"
                    extra = {"error": str(err)} if err else {}
                    db_id = db.add_node_event(run_id, name, status, extra)
                    _publish(loop, run_id, {
                        "type": "node_finish", "node": name,
                        "error": str(err) if err else None, "db_id": db_id,
                    })
                else:
                    db_id = db.add_node_event(run_id, name, "start")
                    _publish(loop, run_id, {
                        "type": "node_start", "node": name, "db_id": db_id,
                    })
            elif mode == "updates":
                for _node, delta in data.items():
                    if isinstance(delta, dict):
                        final_state.update(delta)

        message = str(final_state.get("message") or "")
        _remember_report(run_id, final_state.get("report"))
        db.finish_run(run_id, "success", message, None)
        _publish(loop, run_id, {"type": "run_finished", "message": message})
    except Exception as exc:  # noqa: BLE001
        # A raise here means the DRIVER broke, not that a node failed — the graph
        # turns a node's exception into a handoff report. Either way the browser
        # must not be left on screen with nobody holding it.
        db.finish_run(run_id, "error", None, traceback.format_exc())
        _publish(loop, run_id, {"type": "run_error", "error": str(exc)})
    finally:
        # The sentinel MUST be published, whatever happened to the browser: an SSE
        # subscriber that never receives it waits on the queue forever, and the
        # user watches a finished run report nothing.
        try:
            _hand_over_or_close(run_id, job_id, final_state)
        except Exception:  # noqa: BLE001
            pass
        _publish(loop, run_id, None)  # sentinel: subscribers close


def _hand_over_or_close(run_id: int, job_id: str, final_state: dict) -> None:
    """Park the window for the human, or close it. Never raises.

    "Is there a window on screen" is answered the same way `handoff_node` answers
    it — a context in the state AND no error — rather than by the presence of the
    handle alone: the graph's own steps null the handle out on every failing path,
    and a run that errored after opening a browser has already had it closed.
    """
    context = final_state.get("browser")
    if context is not None and not final_state.get("error"):
        session.hand_over(session.LiveSession(
            job_id=job_id,
            run_id=run_id,
            form_url=str(final_state.get("form_url") or ""),
            page=final_state.get("page"),
            browser=context,
            opened_at=session.now(),
        ))
        return
    try:
        _release(final_state)
    except Exception:  # noqa: BLE001
        pass
