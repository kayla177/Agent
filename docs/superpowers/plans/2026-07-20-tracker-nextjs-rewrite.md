# Job Application Tracker + Next.js Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the daily-agents web app from JSON flat files to SQLite, rebuild all 5 tabs in Next.js/TypeScript (with an enhanced applications tracker), keep Python LangGraph agents behind a slim FastAPI, and add Gmail auto-detection of application status changes.

**Architecture:** Next.js (:3000) owns all UI + CRUD API routes, reading/writing a shared SQLite file (`data/control_center.db`) via Prisma. A slimmed FastAPI (:8001) keeps only agent-run orchestration + SSE. Python agent stores are swapped to SQLite behind their existing function signatures, so the change is non-breaking and can land before any UI work.

**Tech Stack:** Python 3.14 / FastAPI / LangGraph (existing); stdlib `sqlite3` (Python data layer); Next.js 15 App Router + TypeScript + Tailwind + Prisma (new UI); Gmail MCP (auto-detection).

## Global Constraints

- **Single SQLite file, Python owns the schema.** `data/control_center.db` is shared. Python's `CREATE TABLE IF NOT EXISTS` DDL is the single source of truth. Prisma **introspects** it (`prisma db pull`) and never runs `prisma migrate` — this avoids Prisma's migration engine conflicting with the Python-created `runs`/`node_events` tables.
- **Stores keep their public API.** `agents/application_tracker/store.py` and `agents/job_scraper/store.py` must expose the exact same function names/signatures they do today (plus one new optional `auto_detected` param). Callers (graph nodes, web routes) must not need changes.
- **Reads degrade gracefully.** A missing table returns "empty" (never raises), mirroring the current missing-file behavior.
- **Tests use no pytest.** Follow the existing pattern: plain `def test_*()` functions with a `check(name, cond)` helper, run via `uv run python tests/<file>.py`, exit non-zero on failure.
- **Agents never import `web`.** The shared DB helper lives at the project root (`store_db.py`) so both `agents/*` and `web/*` can import it.
- **SQLite ≥ 3.35 features allowed** (`RETURNING`, `ON CONFLICT ... DO UPDATE`) — system sqlite is 3.43.

---

# Plan decomposition (roadmap)

This effort is split into six plans; each ships working, testable software. **This document fully details Plan 1.** Plans 2–6 are scoped here and will each get their own detailed plan doc once the preceding plan lands (API contracts firm up as we go).

| # | Plan | Ships | Depends on |
|---|------|-------|-----------|
| **1** | **Data-layer migration (JSON → SQLite)** | Non-breaking backend swap; existing Jinja app runs on SQLite | — |
| 2 | Next.js scaffold + Prisma introspection | `web-next/` app boots, typed DB client, nav + planet theme | 1 |
| 3 | Applications tracker tab + CRUD API | Full tracker UI: chart, stats, log form, table | 2 |
| 4 | Jobs tab (React port of approved job-tab spec) | `/jobs` hub with fit list + inline expand | 2 |
| 5 | Dashboard + History + Settings + FastAPI slim-down | Remaining tabs; FastAPI reduced to agent-only on :8001; agent proxy + SSE | 3, 4 |
| 6 | Gmail auto-detection | `scan_gmail` agent node + Sync button + `✉` badges | 3, 5 |

Roadmap detail for Plans 2–6 is at the end of this document.

---

# PLAN 1 — Data-Layer Migration (JSON → SQLite)

**Outcome:** Both agent stores read/write SQLite instead of JSON, with identical public APIs. A one-time script migrates existing JSON data. The existing Jinja web app keeps working unchanged (now backed by SQLite). Fully covered by offline tests.

## File structure (Plan 1)

- Create: `store_db.py` — shared SQLite connection + schema for `applications` and `jobs` tables. Project-root module both agents and web import.
- Modify: `agents/application_tracker/store.py` — swap JSON → SQLite; add `auto_detected`.
- Modify: `agents/job_scraper/store.py` — swap JSON → SQLite; preserve full-record round-trip via a `data` JSON column + mirrored query columns; add `replace_record`.
- Create: `scripts/migrate_json_to_sqlite.py` — one-time idempotent import.
- Create: `tests/test_stores_sqlite.py` — new offline tests for both stores.
- Modify: `tests/test_job_scraper.py` — repoint `_use_temp_store()` at a temp DB; drop the obsolete JSON-only `test_store_roundtrip`.
- Modify: `web/app.py` — call `store_db.init_db()` on startup so the tables exist for the running app.

---

## Task 1: Shared SQLite layer (`store_db.py`)

**Files:**
- Create: `store_db.py`
- Test: (covered by Task 5's `tests/test_stores_sqlite.py`)

**Interfaces:**
- Produces:
  - `store_db.DB_PATH: pathlib.Path` — the DB file (tests reassign this).
  - `store_db.connect() -> sqlite3.Connection` — short-lived, `row_factory=Row`, `check_same_thread=False`.
  - `store_db.init_db() -> None` — idempotent `CREATE TABLE IF NOT EXISTS` for `applications` + `jobs`.

- [ ] **Step 1: Write `store_db.py`**

```python
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
```

- [ ] **Step 2: Smoke-test the module manually**

Run: `uv run python -c "import store_db, tempfile, pathlib; store_db.DB_PATH=pathlib.Path(tempfile.mkdtemp())/'t.db'; store_db.init_db(); print([r[0] for r in store_db.connect().execute(\"SELECT name FROM sqlite_master WHERE type='table'\")])"`
Expected: `['applications', 'jobs']`

- [ ] **Step 3: Commit**

```bash
git add store_db.py
git commit -m "feat: shared SQLite layer for applications + jobs tables"
```

---

## Task 2: Application-tracker store → SQLite

**Files:**
- Modify: `agents/application_tracker/store.py` (full rewrite; keep the module docstring intent)
- Test: `tests/test_stores_sqlite.py` (Task 5)

**Interfaces:**
- Consumes: `store_db.connect`, `store_db.init_db` (Task 1).
- Produces (unchanged names; one new optional param on `update_status`):
  - `STATUSES = ("applied", "interview", "offer", "accepted", "rejected")`
  - `load_all() -> list[dict]`
  - `add_application(company, role, url="", notes="", status="applied") -> dict`
  - `update_status(app_id, status, auto_detected=False) -> dict | None`
  - `delete_application(app_id) -> bool`
  - `days_since(date_str) -> int`
  - Every returned dict has key `auto_detected: bool`.

- [ ] **Step 1: Rewrite `agents/application_tracker/store.py`**

```python
"""SQLite store for job applications (the tracker's memory).

Backed by the `applications` table in data/control_center.db (see store_db).
The public API is unchanged from the previous JSON version so the graph nodes
and web routes keep working; a new optional `auto_detected` flag on
update_status records status changes inferred from Gmail. Reads degrade
gracefully (a missing table -> "no applications yet") so a run never crashes.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import store_db

STATUSES = ("applied", "interview", "offer", "accepted", "rejected")


def _today() -> str:
    return dt.date.today().isoformat()


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["auto_detected"] = bool(d.get("auto_detected", 0))
    return d


def load_all() -> list[dict]:
    """Return every application (empty list if none / table missing)."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT * FROM applications ORDER BY id").fetchall()
        return [_row(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def add_application(
    company: str, role: str, url: str = "", notes: str = "", status: str = "applied"
) -> dict:
    """Create and persist a new application; returns the stored record."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(company, role, url, status, applied_date, updated_date, notes, auto_detected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0) RETURNING *",
            (company.strip(), role.strip(), url.strip(), status, today, today, notes.strip()),
        )
        row = cur.fetchone()
    return _row(row)


def update_status(app_id: int, status: str, auto_detected: bool = False) -> dict | None:
    """Set status; returns the updated record or None if absent.

    A manual update (auto_detected=False) clears the auto flag so a Gmail-set
    status a user later overrides no longer shows as auto-detected.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE applications SET status = ?, updated_date = ?, auto_detected = ? "
            "WHERE id = ? RETURNING *",
            (status, _today(), 1 if auto_detected else 0, int(app_id)),
        )
        row = cur.fetchone()
    return _row(row) if row else None


def delete_application(app_id: int) -> bool:
    """Remove an application by id; returns True if one was removed."""
    store_db.init_db()
    with store_db.connect() as conn:
        cur = conn.execute("DELETE FROM applications WHERE id = ?", (int(app_id),))
    return cur.rowcount > 0


def days_since(date_str: str) -> int:
    """Whole days between `date_str` (ISO) and today; 0 if unparseable."""
    try:
        d = dt.date.fromisoformat(date_str)
        return (dt.date.today() - d).days
    except (ValueError, TypeError):
        return 0
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `uv run python -c "from agents.application_tracker import store; print(store.STATUSES)"`
Expected: `('applied', 'interview', 'offer', 'accepted', 'rejected')`

- [ ] **Step 3: Commit**

```bash
git add agents/application_tracker/store.py
git commit -m "feat: back application-tracker store with SQLite + auto_detected flag"
```

---

## Task 3: Job-scraper store → SQLite (round-trip preserving)

**Files:**
- Modify: `agents/job_scraper/store.py` (full rewrite)
- Test: `tests/test_stores_sqlite.py` (Task 5) + `tests/test_job_scraper.py` (Task 6)

**Interfaces:**
- Consumes: `store_db.connect`, `store_db.init_db` (Task 1).
- Produces (unchanged names + one new function):
  - `STATUSES = ("new", "viewed", "applied", "dismissed")`
  - `load_records() -> dict[str, dict]` — full enriched record per id (round-trips every field).
  - `upsert_records(postings, *, status="new") -> None` — merge; new rows forced to `status`, existing keep status + first_seen.
  - `set_status(pid, status) -> dict | None`
  - `load_seen() -> set[str]`
  - `add_seen(ids) -> None`
  - `replace_record(rec) -> None` — **new**: insert/replace verbatim (status + timestamps taken as-is), used by the migration to preserve historical state.

**Design note:** the pipeline (dedupe/freshness/rank) round-trips arbitrary enrichment fields (`age_days`, `ghost_reason`, `canonical_location`, `dup_of`, `updated_at`, `deadline`, …). The full record is stored as JSON in the `data` column (source of truth on read); the mirrored columns exist only so the web UI can filter/sort in SQL.

- [ ] **Step 1: Rewrite `agents/job_scraper/store.py`**

```python
"""SQLite record-store for job postings (dedupe memory + web view source).

Backed by the `jobs` table in data/control_center.db (see store_db). Each row
keeps a `data` JSON blob with the FULL enriched posting (so the scraper pipeline
round-trips every field it depends on) plus mirrored columns (status, company,
fit_score, ...) that let the web UI filter/sort in SQL. The public API matches
the previous JSON version. Reads degrade gracefully (missing table -> "nothing
seen yet"). `status` is one of new | viewed | applied | dismissed.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import store_db

STATUSES = ("new", "viewed", "applied", "dismissed")


def _today() -> str:
    return dt.date.today().isoformat()


def _mirror(rec: dict) -> dict:
    """Values for the mirrored (queryable) columns, derived from a full record."""
    remote = rec.get("remote")
    return {
        "company": rec.get("company", ""),
        "title": rec.get("title", ""),
        "location": rec.get("location", ""),
        "url": rec.get("url", ""),
        "status": rec.get("status", "new"),
        "ats": rec.get("ats", ""),
        "posted_at": rec.get("posted_at"),
        "remote": None if remote is None else (1 if remote else 0),
        "compensation": rec.get("compensation"),
        "department": rec.get("department"),
        "description": rec.get("description"),
        "fit_score": rec.get("fit_score"),
        "fit_reason": rec.get("fit_reason"),
        "ghost": 1 if rec.get("ghost") else 0,
        "also_on": json.dumps(rec.get("also_on", [])),
        "first_seen": rec.get("first_seen", ""),
        "last_seen": rec.get("last_seen", ""),
    }


_INSERT = (
    "INSERT INTO jobs (id, company, title, location, url, status, ats, posted_at, "
    "remote, compensation, department, description, fit_score, fit_reason, ghost, "
    "also_on, first_seen, last_seen, data) VALUES (:id, :company, :title, :location, "
    ":url, :status, :ats, :posted_at, :remote, :compensation, :department, "
    ":description, :fit_score, :fit_reason, :ghost, :also_on, :first_seen, "
    ":last_seen, :data) ON CONFLICT(id) DO UPDATE SET "
    "company=excluded.company, title=excluded.title, location=excluded.location, "
    "url=excluded.url, status=excluded.status, ats=excluded.ats, "
    "posted_at=excluded.posted_at, remote=excluded.remote, "
    "compensation=excluded.compensation, department=excluded.department, "
    "description=excluded.description, fit_score=excluded.fit_score, "
    "fit_reason=excluded.fit_reason, ghost=excluded.ghost, also_on=excluded.also_on, "
    "first_seen=excluded.first_seen, last_seen=excluded.last_seen, data=excluded.data"
)


def _write(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        _INSERT,
        {"id": record["id"], **_mirror(record),
         "data": json.dumps(record, ensure_ascii=False)},
    )


def load_records() -> dict[str, dict]:
    """Return the full {id: record} map (empty if none / table missing)."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT id, data FROM jobs").fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        try:
            rec = json.loads(r["data"]) if r["data"] else {}
        except json.JSONDecodeError:
            rec = {}
        rec["id"] = r["id"]
        out[r["id"]] = rec
    return out


def upsert_records(postings: list[dict], *, status: str = "new") -> None:
    """Merge postings; new rows get `status`, existing keep status + first_seen."""
    if not postings:
        return
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        for p in postings:
            pid = p.get("id")
            if not pid:
                continue
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            existing = {}
            if row and row["data"]:
                try:
                    existing = json.loads(row["data"])
                except json.JSONDecodeError:
                    existing = {}
            record = {**existing, **p}
            record["id"] = pid
            record["first_seen"] = existing.get("first_seen") or today
            record["last_seen"] = today
            record["status"] = existing.get("status") or status
            _write(conn, record)


def set_status(pid: str, status: str) -> dict | None:
    """Update one record's status; returns the record or None if absent."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    with store_db.connect() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row["data"]) if row["data"] else {}
        except json.JSONDecodeError:
            record = {}
        record["id"] = pid
        record["status"] = status
        record["last_seen"] = _today()
        _write(conn, record)
    return record


def replace_record(rec: dict) -> None:
    """Insert/replace one record verbatim (status + timestamps taken as-is).

    Unlike upsert_records (which forces new rows to 'new' and stamps first_seen
    today), this preserves the record's own status/first_seen — used by the
    JSON->SQLite migration so historical state survives the move.
    """
    pid = rec.get("id")
    if not pid:
        return
    store_db.init_db()
    with store_db.connect() as conn:
        _write(conn, {**rec, "id": pid})


def load_seen() -> set[str]:
    """Return the set of posting ids recorded on previous runs."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT id FROM jobs").fetchall()
        return {r["id"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def add_seen(ids: list[str]) -> None:
    """Record bare ids as seen (prefer upsert_records with full dicts)."""
    upsert_records([{"id": pid} for pid in ids])
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from agents.job_scraper import store; print(store.STATUSES, hasattr(store, 'replace_record'))"`
Expected: `('new', 'viewed', 'applied', 'dismissed') True`

- [ ] **Step 3: Commit**

```bash
git add agents/job_scraper/store.py
git commit -m "feat: back job-scraper store with SQLite (data-blob round-trip + replace_record)"
```

---

## Task 4: One-time migration script

**Files:**
- Create: `scripts/migrate_json_to_sqlite.py`

**Interfaces:**
- Consumes: `store_db.init_db`, `store_db.connect`; `appstore.load_all`; `jobstore.replace_record` (Tasks 1–3).

- [ ] **Step 1: Write `scripts/migrate_json_to_sqlite.py`**

```python
"""One-time migration: JSON flat files -> SQLite (data/control_center.db).

Idempotent. Applications are matched by (company, role) so re-runs don't
duplicate them and keep their original ids; jobs are written verbatim (status +
timestamps preserved) and re-runs upsert by id.

Run:  uv run python scripts/migrate_json_to_sqlite.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import store_db
from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

_APPS_JSON = config.PROJECT_ROOT / "data" / "applications.json"
_JOBS_JSON = config.PROJECT_ROOT / "agents" / "job_scraper" / "data" / "jobs.json"


def _load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def migrate_applications() -> int:
    data = _load_json(_APPS_JSON)
    apps = data.get("applications", []) if isinstance(data, dict) else []
    seen = {(a["company"], a["role"]) for a in appstore.load_all()}
    added = 0
    for a in apps:
        key = (a.get("company", ""), a.get("role", ""))
        if key in seen:
            continue
        with store_db.connect() as conn:
            conn.execute(
                "INSERT INTO applications (id, company, role, url, status, "
                "applied_date, updated_date, notes, auto_detected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (a.get("id"), a.get("company", ""), a.get("role", ""),
                 a.get("url", ""), a.get("status", "applied"),
                 a.get("applied_date", ""), a.get("updated_date", ""),
                 a.get("notes", "")),
            )
        added += 1
    return added


def migrate_jobs() -> int:
    data = _load_json(_JOBS_JSON)
    jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
    for pid, rec in jobs.items():
        jobstore.replace_record({**rec, "id": pid})
    return len(jobs)


def main() -> None:
    store_db.init_db()
    n_apps = migrate_applications()
    n_jobs = migrate_jobs()
    print(f"migrated {n_apps} applications, {n_jobs} jobs into {store_db.DB_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run against a temp DB (don't touch the real one yet)**

Run: `uv run python -c "import store_db,tempfile,pathlib,runpy,sys; store_db.DB_PATH=pathlib.Path(tempfile.mkdtemp())/'m.db'; sys.argv=['m']; import scripts.migrate_json_to_sqlite as m; m.main()"`
Expected: prints `migrated 2 applications, N jobs into <tempdir>/m.db` (N = job count, may be 0 if no scraper run yet). No error.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_json_to_sqlite.py
git commit -m "feat: one-time JSON->SQLite migration script"
```

---

## Task 5: Offline tests for the SQLite stores

**Files:**
- Create: `tests/test_stores_sqlite.py`

**Interfaces:**
- Consumes: `store_db`, `appstore`, `jobstore` (Tasks 1–3).

- [ ] **Step 1: Write `tests/test_stores_sqlite.py`**

```python
"""Offline SQLite-store tests (no network, no pytest).

Run:  uv run python tests/test_stores_sqlite.py

Points the shared store at a throwaway DB, then exercises the application and
job stores' public API + round-trip fidelity (the scraper pipeline relies on
every enriched field surviving a store round-trip).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store_db
from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _failures.append(name)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def _fresh_db() -> None:
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()


def test_applications() -> None:
    print("application store")
    _fresh_db()
    check("empty at start", appstore.load_all() == [])
    a = appstore.add_application("Stripe", "SWE Intern", url="https://s", notes="ref")
    check("add returns id 1", a["id"] == 1)
    check("add persists", len(appstore.load_all()) == 1)
    check("auto_detected defaults False", a["auto_detected"] is False)
    u = appstore.update_status(1, "interview", auto_detected=True)
    check("status updated", u["status"] == "interview")
    check("auto_detected flag set", u["auto_detected"] is True)
    check("manual set clears auto", appstore.update_status(1, "offer")["auto_detected"] is False)
    check("update missing -> None", appstore.update_status(999, "offer") is None)
    check("bad status raises", _raises(lambda: appstore.add_application("A", "B", status="nope")))
    check("delete returns True", appstore.delete_application(1) is True)
    check("delete missing -> False", appstore.delete_application(1) is False)
    check("empty after delete", appstore.load_all() == [])


def test_jobs_roundtrip() -> None:
    print("job store round-trip")
    _fresh_db()
    rec = {
        "id": "workday:stripe:123", "company": "Stripe", "title": "SWE Intern",
        "location": "SF", "url": "https://x", "ats": "workday",
        "posted_at": "2026-07-01", "remote": True, "compensation": "USD 50-90",
        "department": "Eng", "description": "Build things", "fit_score": 92.0,
        "fit_reason": "great match", "ghost": False, "also_on": ["lever"],
        "age_days": 5, "ghost_reason": "", "canonical_location": "San Francisco, CA",
        "dup_of": None, "updated_at": "2026-07-02", "deadline": "2026-08-01",
    }
    jobstore.upsert_records([rec])
    got = jobstore.load_records()["workday:stripe:123"]
    check("status forced 'new' on insert", got["status"] == "new")
    check("first_seen stamped", bool(got["first_seen"]))
    check("enrichment age_days round-trips", got["age_days"] == 5)
    check("enrichment canonical_location round-trips", got["canonical_location"] == "San Francisco, CA")
    check("enrichment dup_of round-trips (None)", got["dup_of"] is None)
    check("fit_score round-trips", got["fit_score"] == 92.0)
    check("also_on round-trips", got["also_on"] == ["lever"])
    check("load_seen has id", "workday:stripe:123" in jobstore.load_seen())
    check("set_status persists", jobstore.set_status("workday:stripe:123", "applied")["status"] == "applied")
    check("set_status reload", jobstore.load_records()["workday:stripe:123"]["status"] == "applied")
    check("set_status missing -> None", jobstore.set_status("nope", "applied") is None)
    jobstore.upsert_records([{**rec, "title": "Senior SWE"}])
    got2 = jobstore.load_records()["workday:stripe:123"]
    check("re-upsert preserves status", got2["status"] == "applied")
    check("re-upsert refreshes fields", got2["title"] == "Senior SWE")


def test_replace_record() -> None:
    print("job store replace_record (migration path)")
    _fresh_db()
    jobstore.replace_record({"id": "j1", "company": "Figma", "title": "FE",
                             "status": "dismissed", "first_seen": "2026-06-01"})
    got = jobstore.load_records()["j1"]
    check("replace preserves status verbatim", got["status"] == "dismissed")
    check("replace preserves first_seen verbatim", got["first_seen"] == "2026-06-01")


def main() -> int:
    for fn in (test_applications, test_jobs_roundtrip, test_replace_record):
        fn()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("all store tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the tests**

Run: `uv run python tests/test_stores_sqlite.py`
Expected: all checks `✓`, final line `all store tests passed`, exit 0.

- [ ] **Step 3: Commit**

```bash
git add tests/test_stores_sqlite.py
git commit -m "test: offline SQLite store tests (round-trip + auto_detected)"
```

---

## Task 6: Update `tests/test_job_scraper.py` for the SQLite store

**Files:**
- Modify: `tests/test_job_scraper.py`

The store round-trip is now covered by `tests/test_stores_sqlite.py`. This task repoints the shared temp-store fixture (used by `test_dedupe_cross_source`) at a temp DB and removes the obsolete JSON-only `test_store_roundtrip`.

- [ ] **Step 1: Update imports**

At the top of the file, alongside `from agents.job_scraper import ats, matching, store`, add:

```python
import store_db
```

- [ ] **Step 2: Replace `_use_temp_store()`**

Replace the existing function:

```python
def _use_temp_store() -> Path:
    tmp = Path(tempfile.mkdtemp())
    store._DATA_DIR = tmp
    store._STORE = tmp / "jobs.json"
    store._LEGACY = tmp / "seen.json"
    return tmp
```

with:

```python
def _use_temp_store() -> Path:
    tmp = Path(tempfile.mkdtemp())
    store_db.DB_PATH = tmp / "test.db"
    store_db.init_db()
    return tmp
```

- [ ] **Step 3: Remove the obsolete `test_store_roundtrip`**

Delete the entire `def test_store_roundtrip() -> None:` function (it tests JSON-file behavior including the legacy `seen.json` migration, which no longer exists), and remove `test_store_roundtrip,` from the tuple in `main()`.

- [ ] **Step 4: Run the suite**

Run: `uv run python tests/test_job_scraper.py`
Expected: all remaining checks pass (ats, matching, freshness, dedupe cross-source, rank parse), exit 0.

- [ ] **Step 5: Commit**

```bash
git add tests/test_job_scraper.py
git commit -m "test: repoint job-scraper store fixture at temp SQLite; drop JSON round-trip test"
```

---

## Task 7: Initialize SQLite tables on web startup

**Files:**
- Modify: `web/app.py:40-43` (the `_startup` hook)

**Interfaces:**
- Consumes: `store_db.init_db` (Task 1).

- [ ] **Step 1: Import `store_db` in `web/app.py`**

Add to the imports block (with the other `# noqa: E402` imports):

```python
import store_db  # noqa: E402
```

- [ ] **Step 2: Call it in the startup hook**

Change:

```python
@app.on_event("startup")
def _startup() -> None:
    db.init_db()
```

to:

```python
@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    store_db.init_db()
```

- [ ] **Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: create applications/jobs tables on web startup"
```

---

## Task 8: Run the real migration + end-to-end verify

- [ ] **Step 1: Back up the current DB (safety)**

Run: `cp data/control_center.db data/control_center.db.bak 2>/dev/null || echo "no db yet"`

- [ ] **Step 2: Run the migration against the real DB**

Run: `uv run python scripts/migrate_json_to_sqlite.py`
Expected: `migrated 2 applications, N jobs into .../data/control_center.db`

- [ ] **Step 2b: Re-run to confirm idempotency**

Run: `uv run python scripts/migrate_json_to_sqlite.py`
Expected: `migrated 0 applications, N jobs into ...` (0 new applications the second time).

- [ ] **Step 3: Inspect the data**

Run: `sqlite3 data/control_center.db "SELECT id, company, role, status, auto_detected FROM applications;"`
Expected: two rows — Stripe / SWE Intern / interview / 0 and Figma / FE Intern / applied / 0.

- [ ] **Step 4: Verify the existing Jinja app still works on SQLite**

Run: `uv run python -m web` (starts on :8000), then in another shell:
`curl -s localhost:8000/applications | grep -c Stripe` → ≥ 1
`curl -s localhost:8000/jobs > /dev/null && echo ok` → `ok`
Stop the server.

- [ ] **Step 5: Full test sweep**

Run: `uv run python tests/test_stores_sqlite.py && uv run python tests/test_job_scraper.py`
Expected: both suites pass.

---

## Plan 1 verification summary

- `uv run python tests/test_stores_sqlite.py` → all pass
- `uv run python tests/test_job_scraper.py` → all pass
- `uv run python scripts/migrate_json_to_sqlite.py` → migrates + is idempotent on re-run
- `sqlite3 data/control_center.db "SELECT COUNT(*) FROM applications;"` → 2
- Existing web app at `:8000` serves `/applications` and `/jobs` from SQLite
- After Plan 1, the JSON files (`data/applications.json`, `agents/job_scraper/data/jobs.json`) are no longer read/written; leave them in place as a backup until Next.js is validated, then remove in Plan 5.

---

# Plans 2–6 — Roadmap (to be detailed in their own docs)

## Plan 2 — Next.js scaffold + Prisma introspection
- `npx create-next-app@latest web-next --typescript --tailwind --app --eslint` at repo root.
- Add Prisma; set `datasource db { provider = "sqlite"; url = "file:../data/control_center.db" }`.
- Generate the client by **introspection**, not migration: `npx prisma db pull` then `npx prisma generate`. (Requires Plan 1's tables to exist — run the migration first.) Document that schema changes always happen in `store_db.py` → re-run `db pull`.
- `src/lib/db.ts` — Prisma client singleton (guard against dev hot-reload dupes).
- `src/components/TopNav.tsx`, `src/app/layout.tsx` — nav (dashboard/jobs/applications/history/settings) + `data-planet` theme; port palette from `web/static/app.css`.
- Config `next.config.ts` to proxy `/agents/*` and `/runs/*` to FastAPI `:8001` (rewrites) — used in Plan 5.
- **Ships:** app boots at `:3000`, nav renders, DB reads work (e.g. a temporary page listing application count).

## Plan 3 — Applications tracker tab + CRUD API
- API routes (Next.js): `GET/POST /api/applications`, `PATCH /api/applications/[id]/status`, `DELETE /api/applications/[id]` — all via Prisma against `applications`.
- `src/app/applications/page.tsx` (server component fetches list) + client components for the log-form, inline status dropdown, delete.
- `PipelineChart.tsx` — donut by status (Recharts; follow the `dataviz` skill).
- Stats row: total · applied-this-week (`days_since`-equivalent in TS) · response-rate · interviews.
- `✉` badge rendering when `auto_detected` (data present now; populated in Plan 6).
- **Ships:** full tracker CRUD from the browser, no reload.

## Plan 4 — Jobs tab (React port)
- Implements `docs/superpowers/specs/2026-07-20-job-tab-ui-design.md` in React against `GET /api/jobs` (filter/sort params) + `POST /api/jobs/[id]/apply|dismiss` (apply also inserts an application via the tracker store equivalent).
- Ids contain `:`/`/` → pass as query param or body, not path segment (mirror current design).
- Components: `BestMatchHero`, `JobRow`, `JobDetail` (inline expand).
- **Ships:** the balanced-hub jobs UI with reload-free triage.

## Plan 5 — Dashboard + History + Settings + FastAPI slim-down
- Dashboard: planet `AgentCard`s; "Run" → `POST /api/agents/[key]/run` (proxy to FastAPI) → `RunStream` consumes SSE from `/runs/[id]/events`.
- History: list from `runs` (Prisma `$queryRaw`). Settings: read/write `data/prefs.json` (reuse `config.refresh()` semantics).
- **FastAPI slim-down** (now safe — Next.js fully replaces the UI): in `web/app.py` remove `pages`, `applications`, `jobs`, `settings`, `charts` routers, static mount, and Jinja; keep `runs`. Add CORS for `http://localhost:3000`. Change port to 8001 in `web/__main__.py`. Delete the Jinja templates + now-unused routers. Remove the backup JSON files.
- **Ships:** all five tabs live in Next.js; Python is agent-only.

## Plan 6 — Gmail auto-detection
- Add `scan_gmail` node to `agents/application_tracker/graph.py` reachable via a `gmail_sync` task (extend the registry/runner to pass a task, or add an `application_tracker_gmail` spec).
- Node uses the Gmail MCP: search `subject:(interview OR application OR offer OR "next steps" OR unfortunately) newer_than:30d`, extract company, fuzzy-match `applications.company`, infer status, call `store.update_status(id, status, auto_detected=True)`.
- `POST /api/gmail/sync` (Next.js) → proxy agent run; stream SSE progress into a toast; refresh table; `✉` badges show on touched rows.
- **Ships:** one-click Gmail status sync.

---

## Self-review notes (Plan 1)
- **Spec coverage:** Plan 1 covers the "organized backend / SQLite" goal and the `auto_detected` field the Gmail feature (Plan 6) needs. UI/Gmail goals are covered by Plans 2–6.
- **Type consistency:** store return dicts include `auto_detected: bool`; `update_status` signature `(app_id, status, auto_detected=False)` is used identically by the migration and (later) the Gmail node. `replace_record`/`upsert_records`/`_write` share the one `_INSERT` statement.
- **Round-trip risk retired:** `data` JSON column is the read source of truth, so no enrichment field is lost; mirrored columns exist only for SQL filtering.
- **Non-breaking:** stores keep identical public APIs, so the existing Jinja routes work untouched through Plan 4.
