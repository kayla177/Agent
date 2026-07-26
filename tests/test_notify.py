"""notify_node — persistence + announcement invariants.

Covers the Task 8 review findings:
  - transient pipeline tags (`_rescored`, `_skip_llm`) must never reach the
    store, in neither the mirrored columns nor the `data` JSON blob, after a
    real persistence round-trip;
  - `_rescored` backlog rows are persisted but never announced, while a
    genuinely new posting IS announced;
  - an empty `JOB_COUNTRIES` means "no filtering" (matches the JOB_SOURCES /
    STOCK_WATCHLIST convention), not "show nothing";
  - a missing/blank `country` is treated as UNKNOWN and kept, never dropped.
"""

from __future__ import annotations

import json

import config
import store_db
from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.notify import make_notify_node


def test_transient_tags_never_reach_the_store(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    notify = make_notify_node(send=False)
    notify({"new": [
        {"id": "r1", "title": "SWE Intern", "company": "Acme", "location": "Austin, TX",
         "country": "US", "fit_score": 80, "fit_reason": "matched: python",
         "_rescored": True, "_skip_llm": True},
    ]})

    with store_db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", ("r1",)).fetchone()
    assert row is not None
    assert not any(col.startswith("_") for col in row.keys()), \
        "no mirrored column may be named after a transient tag"

    blob = json.loads(row["data"])
    assert "_rescored" not in blob, "_rescored leaked into the persisted data blob"
    assert "_skip_llm" not in blob, "_skip_llm leaked into the persisted data blob"

    # Also check the higher-level accessor, since that's what every other
    # node/consumer actually reads.
    loaded = jobstore.load_records()["r1"]
    assert "_rescored" not in loaded
    assert "_skip_llm" not in loaded


def test_rescored_rows_persisted_but_not_announced_new_posting_is(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    notify = make_notify_node(send=False)
    out = notify({"new": [
        {"id": "old", "title": "Backlog Intern", "company": "Acme", "location": "Austin, TX",
         "country": "US", "fit_score": 80, "fit_reason": "matched: python", "_rescored": True},
        {"id": "fresh", "title": "New Grad SWE", "company": "Zeta", "location": "Toronto, ON",
         "country": "CA", "fit_score": 70, "fit_reason": "matched: sql"},
    ]})

    assert "Acme" not in out["message"], "a _rescored row must never be announced"
    assert "Zeta" in out["message"], "a genuinely new posting must be announced"

    # But both rows are persisted regardless of announcement.
    records = jobstore.load_records()
    assert records["old"]["fit_score"] == 80
    assert records["fresh"]["fit_score"] == 70


def test_empty_job_countries_means_no_filtering(temp_db, monkeypatch):
    """Regression (Task 8 review): clearing the JOB_COUNTRIES textarea on the
    Settings page used to silence the entire digest (only UNKNOWN survived).
    Every other list pref in this repo treats empty as "use the defaults" /
    "no filtering" (JOB_SOURCES, STOCK_WATCHLIST) — JOB_COUNTRIES must match."""
    monkeypatch.setattr(config, "JOB_COUNTRIES", [])
    notify = make_notify_node(send=False)
    out = notify({"new": [
        {"id": "a", "title": "SWE Intern", "company": "Acme", "location": "Dublin, Ireland",
         "country": "OTHER", "fit_score": 50, "fit_reason": "matched: none"},
    ]})
    assert "Acme" in out["message"], "an empty JOB_COUNTRIES must not filter anything out"


def test_missing_or_blank_country_kept_as_unknown(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    notify = make_notify_node(send=False)
    out = notify({"new": [
        {"id": "a", "title": "SWE Intern", "company": "Acme", "location": "?",
         "fit_score": 50, "fit_reason": "matched: none"},  # no "country" key at all
    ]})
    assert "Acme" in out["message"], "a missing country must read as UNKNOWN and never be dropped"
