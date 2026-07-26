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
        "country": rec.get("country", ""),
        "first_seen": rec.get("first_seen", ""),
        "last_seen": rec.get("last_seen", ""),
    }


_INSERT = (
    "INSERT INTO jobs (id, company, title, location, url, status, ats, posted_at, "
    "remote, compensation, department, description, fit_score, fit_reason, ghost, "
    "also_on, country, first_seen, last_seen, data) VALUES (:id, :company, :title, :location, "
    ":url, :status, :ats, :posted_at, :remote, :compensation, :department, "
    ":description, :fit_score, :fit_reason, :ghost, :also_on, :country, :first_seen, "
    ":last_seen, :data) ON CONFLICT(id) DO UPDATE SET "
    "company=excluded.company, title=excluded.title, location=excluded.location, "
    "url=excluded.url, status=excluded.status, ats=excluded.ats, "
    "posted_at=excluded.posted_at, remote=excluded.remote, "
    "compensation=excluded.compensation, department=excluded.department, "
    "description=excluded.description, fit_score=excluded.fit_score, "
    "fit_reason=excluded.fit_reason, ghost=excluded.ghost, also_on=excluded.also_on, "
    "country=excluded.country, "
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
            # last_seen means "observed in a live scrape". A genuinely fetched
            # posting never carries its own `last_seen` (fetch/filter/dedupe
            # never set it), so it defaults to today. A backfilled row DOES
            # carry its own `last_seen` (loaded straight from storage by
            # backfill_node) and must NOT be bumped here — it was reprocessed
            # for scoring/country, not re-observed by this scrape.
            record["last_seen"] = p.get("last_seen") or today
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


def touch_last_seen(ids: list[str] | set[str]) -> int:
    """Stamp `last_seen` = today on postings observed in this scrape.

    Writes through `_write`, so the mirrored column and the `data` blob stay in
    sync (the dual-write rule). Ids absent from the table are skipped. Nothing
    else on the record is altered — notably `first_seen` and `status`.

    This is what makes `last_seen` mean "observed on a board", which is the
    prerequisite for detecting a delisted posting: `dedupe` removes already-seen
    postings before `notify` persists, so without this they would never be
    re-stamped.
    """
    ids = [i for i in ids if i]
    if not ids:
        return 0
    store_db.init_db()
    today = _today()
    touched = 0
    with store_db.connect() as conn:
        for pid in ids:
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            try:
                record = json.loads(row["data"]) if row["data"] else {}
            except json.JSONDecodeError:
                record = {}
            record["id"] = pid
            record["last_seen"] = today
            _write(conn, record)
            touched += 1
    return touched
