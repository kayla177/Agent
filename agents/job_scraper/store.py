"""JSON record-store for job postings (the dedupe memory + web-view source).

Stored at agents/job_scraper/data/jobs.json as a JSON object keyed by posting id:

    {"jobs": {"<global id>": {<enriched posting>, "first_seen": ISO,
                              "last_seen": ISO, "status": str}, ...}}

`status` is one of new | viewed | applied | dismissed. The record store is the
single source of truth for both dedupe ("have we seen this id?") and the web
jobs view. The dir/file are created lazily and reads degrade gracefully (a
missing or corrupt file is treated as "nothing seen yet") so a bad file never
crashes a run.

Back-compat: `load_seen()` / `add_seen()` keep their old signatures (used by the
notify node and any external caller) but now read/write the record store. A
legacy `seen.json` (ids-only) is migrated in on first read as status "viewed".
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

# .../agents/job_scraper/data/  (relative to this file, not cwd)
_DATA_DIR = Path(__file__).resolve().parent / "data"
_STORE = _DATA_DIR / "jobs.json"
_LEGACY = _DATA_DIR / "seen.json"

STATUSES = ("new", "viewed", "applied", "dismissed")


def _today() -> str:
    return dt.date.today().isoformat()


def _read_raw() -> dict:
    """Read the record store, migrating a legacy seen.json if present."""
    try:
        with _STORE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        jobs = data.get("jobs")
        if isinstance(jobs, dict):
            return jobs
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    # No record store yet — migrate legacy ids-only seen.json if it exists.
    try:
        with _LEGACY.open("r", encoding="utf-8") as fh:
            legacy = json.load(fh)
        return {
            pid: {"id": pid, "status": "viewed", "first_seen": "", "last_seen": ""}
            for pid in legacy.get("ids", [])
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_raw(jobs: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"jobs": jobs}, fh, indent=2, ensure_ascii=False)
    tmp.replace(_STORE)  # atomic-ish swap so an interrupted write can't corrupt


def load_records() -> dict[str, dict]:
    """Return the full {id: record} map (empty if none / unreadable)."""
    return _read_raw()


def upsert_records(postings: list[dict], *, status: str = "new") -> None:
    """Merge postings into the store, stamping first_seen/last_seen.

    Existing records keep their `status` and `first_seen`; new ones get `status`
    (default "new"). Enrichment fields are refreshed on every pass.
    """
    if not postings:
        return
    jobs = _read_raw()
    today = _today()
    for p in postings:
        pid = p.get("id")
        if not pid:
            continue
        existing = jobs.get(pid, {})
        record = {**existing, **p}
        record["first_seen"] = existing.get("first_seen") or today
        record["last_seen"] = today
        record["status"] = existing.get("status") or status
        jobs[pid] = record
    _write_raw(jobs)


def set_status(pid: str, status: str) -> dict | None:
    """Update one record's status; returns the record or None if absent."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    jobs = _read_raw()
    record = jobs.get(pid)
    if record is None:
        return None
    record["status"] = status
    record["last_seen"] = _today()
    jobs[pid] = record
    _write_raw(jobs)
    return record


# --- Back-compat shims (ids-only view over the record store) -----------------


def load_seen() -> set[str]:
    """Return the set of posting ids recorded on previous runs."""
    return set(_read_raw().keys())


def add_seen(ids: list[str]) -> None:
    """Record bare ids as seen (kept for callers that only have ids).

    Prefer `upsert_records` when full posting dicts are available.
    """
    upsert_records([{"id": pid} for pid in ids])
