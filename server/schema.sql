-- Web control-center persistence. Single-user, local. Plain sqlite3 (no ORM).
-- WAL mode lets the SSE/read path read while a run writes.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

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
