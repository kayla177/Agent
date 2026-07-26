"""Offline SQLite-store tests (no network, no pytest).

Run:  .venv/bin/python -m pytest tests/test_stores_sqlite.py

Points the shared store at a throwaway DB, then exercises the application and
job stores' public API + round-trip fidelity (the scraper pipeline relies on
every enriched field surviving a store round-trip).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def test_applications(temp_db) -> None:
    assert appstore.load_all() == []
    a = appstore.add_application("Stripe", "SWE Intern", url="https://s", notes="ref")
    assert a["id"] == 1
    assert len(appstore.load_all()) == 1
    assert a["auto_detected"] is False
    u = appstore.update_status(1, "interview", auto_detected=True)
    assert u["status"] == "interview"
    assert u["auto_detected"] is True
    assert appstore.update_status(1, "offer")["auto_detected"] is False
    assert appstore.update_status(999, "offer") is None
    assert _raises(lambda: appstore.add_application("A", "B", status="nope"))
    assert appstore.delete_application(1) is True
    assert appstore.delete_application(1) is False
    assert appstore.load_all() == []


def test_jobs_roundtrip(temp_db) -> None:
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
    assert got["status"] == "new"
    assert bool(got["first_seen"])
    assert got["age_days"] == 5
    assert got["canonical_location"] == "San Francisco, CA"
    assert got["dup_of"] is None
    assert got["fit_score"] == 92.0
    assert got["also_on"] == ["lever"]
    assert "workday:stripe:123" in jobstore.load_seen()
    assert jobstore.set_status("workday:stripe:123", "applied")["status"] == "applied"
    assert jobstore.load_records()["workday:stripe:123"]["status"] == "applied"
    assert jobstore.set_status("nope", "applied") is None
    jobstore.upsert_records([{**rec, "title": "Senior SWE"}])
    got2 = jobstore.load_records()["workday:stripe:123"]
    assert got2["status"] == "applied"
    assert got2["title"] == "Senior SWE"
    # first_seen + status are preserved across upsert (seed a known PAST date via
    # replace_record, then upsert the same id — a regression that re-stamped
    # first_seen would turn it into today and fail this check).
    jobstore.replace_record({"id": "seed:1", "title": "Old", "status": "viewed",
                             "first_seen": "2026-01-15"})
    jobstore.upsert_records([{"id": "seed:1", "title": "New"}])
    seeded = jobstore.load_records()["seed:1"]
    assert seeded["first_seen"] == "2026-01-15"
    assert seeded["status"] == "viewed"
    assert seeded["title"] == "New"


def test_replace_record(temp_db) -> None:
    jobstore.replace_record({"id": "j1", "company": "Figma", "title": "FE",
                             "status": "dismissed", "first_seen": "2026-06-01"})
    got = jobstore.load_records()["j1"]
    assert got["status"] == "dismissed"
    assert got["first_seen"] == "2026-06-01"
