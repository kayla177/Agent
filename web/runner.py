"""Drive an agent graph, streaming node events live while persisting them.

A run is launched as an ``asyncio.Task`` owned by the module-level
:data:`manager`. The driver consumes ``graph.astream(stream_mode=["updates",
"tasks"])`` and, for every event, BOTH persists it to SQLite (durable history)
and publishes it to any live subscribers (the SSE endpoint). Because every event
is written to the DB, a late subscriber or a page reload replays from SQLite —
the live queue is purely an optimization.

LangGraph runs the sync agent nodes (their blocking httpx/litellm calls) in its
own worker threads under ``astream``, so this coroutine never blocks the event
loop. Keep the nodes sync.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, Optional

from agents.registry import AgentSpec, get_spec
from web import db


class RunManager:
    """Per-run pub/sub so multiple SSE clients can follow the same live run."""

    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = {}
        self._active: set[int] = set()  # run_ids with a driver currently running

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        subs = self._subs.get(run_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(run_id, None)

    def is_live(self, run_id: int) -> bool:
        return run_id in self._active

    def publish(self, run_id: int, event: Optional[dict]) -> None:
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(event)


manager = RunManager()


async def _drive(run_id: int, spec: AgentSpec, send: bool) -> None:
    manager._active.add(run_id)
    final_state: dict[str, Any] = {}
    try:
        graph = spec.build_graph(send=send)
        async for mode, data in graph.astream({}, stream_mode=["updates", "tasks"]):
            if mode == "tasks":
                name = data.get("name", "?")
                if "result" in data:  # task finished
                    err = data.get("error")
                    status = "error" if err else "finish"
                    payload = {"error": str(err)} if err else {}
                    db_id = db.add_node_event(run_id, name, status, payload)
                    manager.publish(
                        run_id,
                        {
                            "type": "node_finish",
                            "node": name,
                            "error": str(err) if err else None,
                            "db_id": db_id,
                        },
                    )
                else:  # task started
                    db_id = db.add_node_event(run_id, name, "start")
                    manager.publish(
                        run_id, {"type": "node_start", "node": name, "db_id": db_id}
                    )
            elif mode == "updates":
                for _node, delta in data.items():
                    if isinstance(delta, dict):
                        final_state.update(delta)

        message = final_state.get(spec.output_key, "") or ""
        db.finish_run(run_id, "success", message, None)
        manager.publish(run_id, {"type": "run_finished", "message": message})
    except Exception as exc:
        tb = traceback.format_exc()
        db.finish_run(run_id, "error", None, tb)
        manager.publish(run_id, {"type": "run_error", "error": str(exc)})
    finally:
        manager._active.discard(run_id)
        manager.publish(run_id, None)  # sentinel: tells subscribers to close


def start_run(agent_key: str, send: bool) -> int:
    """Create a run row and launch its driver task. Returns the run id."""
    spec = get_spec(agent_key)  # raises KeyError on unknown agent
    run_id = db.create_run(agent_key, send)
    asyncio.create_task(_drive(run_id, spec, send))
    return run_id
