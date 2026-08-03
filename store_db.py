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
    # When a real submission was VERIFIED on an ATS confirmation page (Phase B
    # Task 9). Nullable with NO default and no backfill, deliberately: every
    # existing row was recorded optimistically and none of them has been
    # verified, so stamping any of them here would be inventing evidence. NULL
    # reads as "unverified", which is exactly true of all of them.
    if cols and "confirmed_at" not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN confirmed_at TEXT")

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
    # WHY a posting is flagged (delisted / stale / deadline). Previously written
    # only into the `data` blob, so the UI rendered every ghost as "stale".
    if job_cols and "ghost_reason" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN ghost_reason TEXT NOT NULL DEFAULT ''")
        # One-time seed from the blob, which HAS carried this value all along.
        # Without it, every already-flagged row would render as a bare "stale"
        # until something happened to rewrite it, so the mirrored column would
        # contradict the blob for an unbounded time. `json_valid` keeps a single
        # corrupt blob from aborting init_db (which runs on every server start
        # and every agent run), and the whole thing is inside the add-column
        # branch, so it can never run twice.
        try:
            conn.execute(
                "UPDATE jobs SET ghost_reason = "
                "COALESCE(json_extract(data, '$.ghost_reason'), '') "
                "WHERE json_valid(data)"
            )
        except sqlite3.OperationalError as exc:  # no JSON1 support
            print(f"⚠️ could not seed jobs.ghost_reason from the data blob: {exc}")

    # Undergrad-eligibility screen. `rank_node` used to DROP an ineligible
    # posting outright, before notify persisted anything — so it never entered
    # the database, was never visible, and was re-fetched and re-dropped every
    # run. Measured: 33-44% of postings with plainly eligible titles ("Software
    # Engineering Intern", "Data Science Intern") were dropped this way. Now the
    # judgement is stored and the UI hides it by default, matching how `country`
    # already works: auditable and reversible.
    #
    # DEFAULT 1 on both, so adding these columns can never hide an existing row.
    # There is deliberately NO seed from the `data` blob here (unlike
    # ghost_reason): `eligible` HAS been written into the blob by rank_node all
    # along, but only ever as True — every False was dropped before it could be
    # persisted. Seeding would therefore copy 551 meaningless `true`s, and any
    # blob value of False would be a row that predates this and is better
    # re-judged on the next run than resurrected as hidden.
    if job_cols and "eligible" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN eligible INTEGER NOT NULL DEFAULT 1")
    if job_cols and "eligible_reason" not in job_cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN eligible_reason TEXT NOT NULL DEFAULT ''"
        )
