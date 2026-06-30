"""SQLite access layer for the web control center.

Plain stdlib ``sqlite3`` — single-user, no migrations framework. WAL mode is set
in schema.sql so readers (SSE replay, history) don't block the writer (a run).
Connections are short-lived and opened per call with ``check_same_thread=False``
so the async event loop and the LangGraph worker threads can both use them.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

import config

DB_PATH = config.PROJECT_ROOT / "data" / "control_center.db"
_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables/indexes if absent. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def create_run(agent_key: str, send: bool) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (agent_key, status, send, started_at) "
            "VALUES (?, 'running', ?, ?)",
            (agent_key, 1 if send else 0, _utcnow()),
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int, status: str, output_message: Optional[str], error: Optional[str]
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, output_message = ?, "
            "error = ? WHERE id = ?",
            (status, _utcnow(), output_message, error, run_id),
        )


def get_run(run_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(agent_key: Optional[str] = None, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        if agent_key:
            rows = conn.execute(
                "SELECT * FROM runs WHERE agent_key = ? ORDER BY id DESC LIMIT ?",
                (agent_key, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def latest_run(agent_key: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE agent_key = ? ORDER BY id DESC LIMIT 1",
            (agent_key,),
        ).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------
# Node events
# --------------------------------------------------------------------------
def add_node_event(
    run_id: int, node: str, status: str, payload: Optional[dict] = None
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO node_events (run_id, node, status, ts, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, node, status, _utcnow(), json.dumps(payload or {})),
        )
        return int(cur.lastrowid)


def get_node_events(run_id: int, after_id: int = 0) -> list[dict]:
    """All node events for a run, oldest first, optionally after a given id."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM node_events WHERE run_id = ? AND id > ? ORDER BY id",
            (run_id, after_id),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out
