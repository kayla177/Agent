"""last_seen accuracy + delisting detection.

The audit found 393 rows with a frozen last_seen: `dedupe` drops already-seen
postings before `notify` persists, so a posting that is STILL listed never gets
re-stamped. Delisting is then detected directly rather than inferred from age —
if a board was fetched successfully and a stored posting was not in the
response, it is gone.
"""

from __future__ import annotations

import datetime as dt

from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.fetch import fetch_node
from agents.job_scraper.nodes.freshness import freshness_node


def _yesterday() -> str:
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def test_touch_last_seen_updates_column_and_blob(temp_db):
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
        "status": "new", "first_seen": _yesterday(), "last_seen": _yesterday(),
    })
    n = jobstore.touch_last_seen(["Acme:greenhouse:1"])
    assert n == 1

    today = dt.date.today().isoformat()
    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT last_seen, data FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)
        ).fetchone()
    import json
    assert row["last_seen"] == today, "mirrored column must be stamped"
    assert json.loads(row["data"])["last_seen"] == today, "blob must be stamped too"


def test_touch_last_seen_ignores_unknown_ids(temp_db):
    assert jobstore.touch_last_seen(["nope"]) == 0
    assert jobstore.touch_last_seen([]) == 0


def test_touch_last_seen_preserves_everything_else(temp_db):
    jobstore.replace_record({
        "id": "a", "company": "Acme", "title": "SWE Intern", "status": "applied",
        "country": "US", "fit_score": 91, "fit_reason": "great match",
        "first_seen": "2026-01-01", "last_seen": _yesterday(),
    })
    jobstore.touch_last_seen(["a"])
    rec = jobstore.load_records()["a"]
    assert rec["status"] == "applied"
    assert rec["country"] == "US"
    assert rec["fit_score"] == 91
    assert rec["first_seen"] == "2026-01-01", "first_seen must never move"


def test_fetch_reports_observed_ids_and_healthy_sources(monkeypatch):
    """A company counts as fetched_ok only if EVERY one of its sources succeeded."""
    from agents.job_scraper.nodes import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Acme", "ats": "greenhouse", "token": "acme"},
        {"company": "Beta", "ats": "greenhouse", "token": "beta"},
        {"company": "Beta", "ats": "lever", "token": "beta"},
    ])

    def fake_fetch(source):
        if source["ats"] == "lever":
            raise RuntimeError("bad token")
        return [{"id": f"{source['company']}:{source['ats']}:1", "company": source["company"]}]

    monkeypatch.setattr(fetch_mod, "fetch_source", fake_fetch)
    out = fetch_node({})

    assert out["observed_ids"] == {"Acme:greenhouse:1", "Beta:greenhouse:1"}
    assert out["fetched_ok"] == {"Acme"}, "Beta had a failing source, so it is not trustworthy"
    assert len(out["warnings"]) == 1


def test_delisting_flags_only_unobserved_rows_from_healthy_boards(temp_db):
    rows = [
        # still on the board -> not delisted
        {"id": "Acme:greenhouse:1", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board read fine, posting absent -> DELISTED
        {"id": "Acme:greenhouse:2", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board failed this run -> must NOT be called delisted
        {"id": "Beta:lever:9", "company": "Beta", "posted_at": "2026-07-01", "_rescored": True},
    ]
    out = freshness_node({
        "new": rows,
        "observed_ids": {"Acme:greenhouse:1"},
        "fetched_ok": {"Acme"},
    })["new"]
    by = {p["id"]: p for p in out}

    assert by["Acme:greenhouse:1"]["ghost"] is False
    assert by["Acme:greenhouse:2"]["ghost"] is True
    assert "delisted" in by["Acme:greenhouse:2"]["ghost_reason"]
    assert by["Beta:lever:9"]["ghost"] is False, "a failed fetch must never imply delisting"


def test_delisting_never_applies_to_freshly_scraped_postings(temp_db):
    """A brand-new posting is by definition observed; it must never be flagged."""
    out = freshness_node({
        "new": [{"id": "Acme:greenhouse:3", "company": "Acme", "posted_at": "2026-07-20"}],
        "observed_ids": {"Acme:greenhouse:3"},
        "fetched_ok": {"Acme"},
    })["new"]
    assert out[0]["ghost"] is False


def test_freshness_without_the_new_state_keys_is_unchanged(temp_db):
    """Backwards safety: absent observed_ids/fetched_ok, nothing is called delisted."""
    out = freshness_node({"new": [
        {"id": "x", "company": "Acme", "posted_at": "2026-07-20", "_rescored": True},
    ]})["new"]
    assert out[0]["ghost"] is False


def test_upsert_preserves_an_explicit_last_seen(temp_db):
    """backfill must not be able to bump last_seen. Without this guard, a
    re-injected row that this scrape never observed gets today's date, which
    destroys the only signal that detects a delisted posting."""
    stale = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    jobstore.upsert_records([{
        "id": "a", "company": "Acme", "title": "SWE Intern", "last_seen": stale,
    }])
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT last_seen FROM jobs WHERE id = 'a'").fetchone()
    assert row["last_seen"] == stale

    # A posting genuinely observed this run still gets stamped, via touch_last_seen.
    jobstore.touch_last_seen(["a"])
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT last_seen FROM jobs WHERE id = 'a'").fetchone()
    assert row["last_seen"] == dt.date.today().isoformat()
