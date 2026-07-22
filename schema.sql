-- Canonical schema for data/control_center.db — the SINGLE source of truth for
-- every table. Applied by store_db.init_db() (which server/db.py and the agent
-- stores all delegate to). The Prisma models in web-next/prisma/schema.prisma
-- mirror this file and are drift-checked against it (see web-next db:check).
--
-- Single-user, local, plain sqlite3 (no migration framework). WAL mode lets
-- Next.js/Prisma read while a Python run writes.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Application tracker
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- Job scraper  (mirrored columns + full enriched posting in `data`)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT    NOT NULL PRIMARY KEY,
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

-- ---------------------------------------------------------------------------
-- Run history (control center)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key      TEXT    NOT NULL,
    status         TEXT    NOT NULL,            -- running | success | error
    send           INTEGER NOT NULL DEFAULT 0,  -- 1 if Discord delivery was on
    started_at     TEXT    NOT NULL,            -- ISO8601 UTC
    finished_at    TEXT,
    output_message TEXT,                         -- final state[output_key]
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_agent_started ON runs(agent_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS node_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node    TEXT    NOT NULL,
    status  TEXT    NOT NULL,                    -- start | update | finish | error
    ts      TEXT    NOT NULL,                    -- ISO8601 UTC
    payload TEXT                                  -- JSON: {keys:[...]} or {error:"..."}
);
CREATE INDEX IF NOT EXISTS idx_node_events_run ON node_events(run_id, id);

-- ---------------------------------------------------------------------------
-- Resume generator (experience pool + generated resumes)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS experience_docs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    filename  TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT 'resume',   -- resume | project
    text      TEXT NOT NULL DEFAULT '',
    added_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resumes (
    job_id      TEXT PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    markdown    TEXT NOT NULL DEFAULT '',
    keywords    TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | final
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);
