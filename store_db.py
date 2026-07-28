"""Shared SQLite access for data/control_center.db.

Single-user, local, plain stdlib sqlite3. Lives at the project root so both the
agent stores (agents/*) and the server layer can import it without agents
depending on the server package. `init_db()` applies the canonical `schema.sql`
(the single source of truth for ALL tables — applications, jobs, runs,
node_events, experience_docs, resumes); server/db.py and the agent stores all
delegate table creation here.

WAL mode lets Next.js/Prisma read while a Python run writes. Connections are
short-lived and opened per call with check_same_thread=False so LangGraph worker
threads and the async server process can share the one file.
"""

from __future__ import annotations

import sqlite3

import config

DB_PATH = config.DB_PATH
SCHEMA_FILE = config.PROJECT_ROOT / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create every table/index if absent, from the canonical schema.sql.
    Safe to call repeatedly (CREATE TABLE IF NOT EXISTS).

    `_migrate` runs FIRST: it adds columns to tables that already exist, and
    schema.sql may declare an INDEX over a newly-added column. `CREATE INDEX IF
    NOT EXISTS` only guards the index name, not the column, so running the script
    first would raise "no such column" on a pre-existing table.
    """
    with connect() as conn:
        _migrate(conn)
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations for pre-existing DBs.

    `CREATE TABLE IF NOT EXISTS` in schema.sql covers fresh databases and new
    tables, but never adds a column to a table that already exists. Each guard
    here is idempotent (checked against PRAGMA table_info), so this is safe to
    run on every init. Keep in sync with schema.sql; the drift check builds a
    fresh DB from schema.sql alone, so these ALTERs must match its columns.

    `_migrate` now runs BEFORE schema.sql's CREATE TABLE IF NOT EXISTS, so on a
    fresh DB none of these tables exist yet — every check below must tolerate a
    missing table (PRAGMA table_info returns no rows, giving an empty `cols`)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(applications)")}
    if cols and "resume_job_id" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN resume_job_id TEXT")
    # Pins which cached résumé PDF was actually sent with an application.
    if cols and "resume_pdf_key" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN resume_pdf_key TEXT")
    # Links an application back to the jobs row it was filed for (NULL on
    # legacy rows created before this link existed).
    if cols and "job_id" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN job_id TEXT")

    # LaTeX résumé export: their .tex template on the master, tailored .tex per job.
    master_cols = {r[1] for r in conn.execute("PRAGMA table_info(master_resume)")}
    if master_cols and "latex" not in master_cols:
        conn.execute("ALTER TABLE master_resume ADD COLUMN latex TEXT NOT NULL DEFAULT ''")
    resume_cols = {r[1] for r in conn.execute("PRAGMA table_info(resumes)")}
    if resume_cols and "latex" not in resume_cols:
        conn.execute("ALTER TABLE resumes ADD COLUMN latex TEXT NOT NULL DEFAULT ''")

    # Country classification for the jobs board's US/Canada filter.
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if job_cols and "country" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN country TEXT NOT NULL DEFAULT ''")
