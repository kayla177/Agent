"""Shared SQLite access for the app-data tables (applications, jobs).

Single-user, local, plain stdlib sqlite3 — companion to web/db.py, which owns
the run-history tables (runs, node_events). Lives at the project root so both
the agent stores (agents/*) and the web layer can import it without agents
depending on the web package.

WAL mode lets Next.js/Prisma read while a Python run writes. Connections are
short-lived and opened per call with check_same_thread=False so LangGraph worker
threads and the async web process can share the one file.
"""

from __future__ import annotations

import sqlite3

import config

DB_PATH = config.PROJECT_ROOT / "data" / "control_center.db"

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company       TEXT    NOT NULL,
    role          TEXT    NOT NULL,
    url           TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'applied',
    applied_date  TEXT    NOT NULL,
    updated_date  TEXT    NOT NULL,
    notes         TEXT    NOT NULL DEFAULT '',
    auto_detected INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT    PRIMARY KEY,
    company      TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    location     TEXT    NOT NULL DEFAULT '',
    url          TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'new',
    ats          TEXT    NOT NULL DEFAULT '',
    posted_at    TEXT,
    remote       INTEGER,
    compensation TEXT,
    department   TEXT,
    description  TEXT,
    fit_score    REAL,
    fit_reason   TEXT,
    ghost        INTEGER NOT NULL DEFAULT 0,
    also_on      TEXT    NOT NULL DEFAULT '[]',
    first_seen   TEXT    NOT NULL DEFAULT '',
    last_seen    TEXT    NOT NULL DEFAULT '',
    data         TEXT    NOT NULL DEFAULT '{}'   -- full enriched posting (round-trip source of truth)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the application-data tables if absent. Safe to call repeatedly."""
    with connect() as conn:
        conn.executescript(SCHEMA)
