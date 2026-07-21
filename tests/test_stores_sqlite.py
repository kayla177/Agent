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
