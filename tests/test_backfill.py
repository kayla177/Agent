"""backfill node — pulls stored rows back through the pipeline tail.

dedupe drops every already-seen id BEFORE rank runs, so without this node the
523 existing rows could never be scored, their ghost flag never re-evaluated,
and last_seen never refreshed.
"""

from __future__ import annotations

import pytest

import config
import profile_store
from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.backfill import backfill_node


def _seed(records):
    for rec in records:
        jobstore.replace_record(rec)


@pytest.fixture
def with_profile(monkeypatch):
    """A profile must exist for the LLM refinement pass to be queued at all, so
    any test asserting on refinement has to set one explicitly."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    return "Python React SQL"


def test_selects_rows_missing_score_or_country(temp_db):
    _seed([
        {"id": "1", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": None, "country": ""},
        {"id": "2", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": 80, "fit_reason": "great match", "country": "US"},
    ])
    out = backfill_node({"new": []})["new"]
    assert [p["id"] for p in out] == ["1"]
    assert out[0]["_rescored"] is True


def test_fills_country_deterministically(temp_db):
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Dublin, Ireland", "fit_score": None, "country": ""}])
    out = backfill_node({"new": []})["new"]
    assert out[0]["country"] == "OTHER"


def test_dismissed_rows_get_country_but_not_llm_refinement(temp_db, with_profile):
    """No inference is ever spent on a job already rejected. A profile is set so
    this test fails for the RIGHT reason — without one, every row is skipped
    anyway and the assertion would pass vacuously."""
    _seed([{"id": "d", "status": "dismissed", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""},
           {"id": "n", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = {p["id"]: p for p in backfill_node({"new": [], "backfill": True})["new"]}
    assert out["d"]["country"] == "US"
    assert out["d"]["_skip_llm"] is True, "dismissed row must not be refined"
    assert out["n"].get("_skip_llm") is not True, "new row SHOULD be refined"


def test_respects_the_llm_cap(temp_db, monkeypatch, with_profile):
    from agents.job_scraper.nodes import backfill as bf
    monkeypatch.setattr(bf, "LLM_CAP", 2)
    _seed([
        {"id": str(i), "status": "new", "title": "SWE Intern",
         "location": "Austin, TX", "fit_score": None, "country": ""}
        for i in range(5)
    ])
    out = backfill_node({"new": [], "backfill": True})["new"]
    refinable = [p for p in out if not p.get("_skip_llm")]
    assert len(refinable) == 2, "cap must bound the expensive pass"
    assert len(out) == 5, "the cheap deterministic pass still covers everything"


def test_no_llm_refinement_without_the_flag(temp_db, monkeypatch):
    """The interactive 'run scraper' button must stay fast: the cheap pass still
    runs, but nothing is queued for the ~6.5s/posting model call."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": []})["new"]
    assert out[0]["_skip_llm"] is True
    assert out[0]["country"] == "US", "the cheap deterministic pass still runs"


def test_no_llm_refinement_without_a_profile(temp_db, monkeypatch):
    """With no profile the rank prompt can only return null scores, and a null
    score leaves the baseline reason in place — so these rows stay selectable and
    would be re-queued on every scheduled run forever. Skip the expensive pass.
    Both profile sources must be neutralised, not just config."""
    monkeypatch.setattr(config, "JOB_PROFILE", "")
    monkeypatch.setattr(profile_store, "fit_profile_text", lambda: "")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": [], "backfill": True})["new"]
    assert out[0]["_skip_llm"] is True, "no profile -> no inference"
    assert out[0]["country"] == "US", "the cheap deterministic pass still runs"


def test_llm_refinement_runs_when_a_profile_exists(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    _seed([{"id": "1", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    out = backfill_node({"new": [], "backfill": True})["new"]
    assert out[0].get("_skip_llm") is not True, "a profile exists, so refine it"


def test_preserves_incoming_new_postings(temp_db):
    _seed([{"id": "stored", "status": "new", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])
    incoming = [{"id": "fresh", "title": "New Grad SWE", "location": "Toronto, Ontario"}]
    out = backfill_node({"new": incoming})["new"]
    ids = {p["id"] for p in out}
    assert ids == {"fresh", "stored"}
    assert next(p for p in out if p["id"] == "fresh").get("_rescored") is not True
