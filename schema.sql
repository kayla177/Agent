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
    auto_detected INTEGER NOT NULL DEFAULT 0,
    resume_job_id TEXT             -- which generated resume was used to apply (-> resumes.job_id); NULL if none
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
    job_id      TEXT NOT NULL PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    markdown    TEXT NOT NULL DEFAULT '',
    latex       TEXT NOT NULL DEFAULT '',        -- tailored LaTeX (their template); '' until generated
    keywords    TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | final
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

-- The one canonical "master" resume the tailored drafts start from. Single row
-- (id is always 1, enforced by the store). `latex` holds the user's real .tex
-- résumé (their template) — the source of truth for format + content; `markdown`
-- is legacy/optional grounding text.
CREATE TABLE IF NOT EXISTS master_resume (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    markdown    TEXT NOT NULL DEFAULT '',
    latex       TEXT NOT NULL DEFAULT '',        -- the user's .tex résumé (template + content)
    keywords    TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
    updated_at  TEXT NOT NULL DEFAULT ''
);

-- Version history: a snapshot of a resume's Markdown is appended here before it
-- is overwritten (on regenerate or manual save), so past drafts are never lost.
CREATE TABLE IF NOT EXISTS resume_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    markdown    TEXT NOT NULL DEFAULT '',
    keywords    TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
    status      TEXT NOT NULL DEFAULT 'draft',   -- status at snapshot time
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_resume_versions_job ON resume_versions(job_id);

-- ---------------------------------------------------------------------------
-- Stock digest — persisted analysis snapshots (one row per symbol per run, plus
-- a single '__market__' row holding the overview). Lets the /stocks desk serve
-- the last LLM-written analysis without re-running the model on every page load.
-- `data` holds the full beginner report (summary, explained signals, risks,
-- catalysts, learn note) or, for '__market__', {overview, read}.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_analysis (
    symbol   TEXT    NOT NULL,
    run_at   TEXT    NOT NULL,               -- ISO8601 UTC; all rows of one run share this
    verdict  TEXT    NOT NULL DEFAULT '',    -- bullish | neutral | bearish
    score    INTEGER,                         -- -2..+2 (bearish..bullish)
    signal   TEXT    NOT NULL DEFAULT '',    -- buy | sell | hold (transparent heuristic)
    price    REAL,
    pct      REAL,
    data     TEXT    NOT NULL DEFAULT '{}',  -- full report JSON
    PRIMARY KEY (run_at, symbol)
);
CREATE INDEX IF NOT EXISTS idx_stock_analysis_run ON stock_analysis(run_at DESC);
