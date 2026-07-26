"""backfill node — pulls stored rows back through the pipeline tail.

dedupe drops every already-seen id BEFORE rank runs, so without this node the
523 existing rows could never be scored and their ghost flag never
re-evaluated. (`last_seen` is deliberately NOT touched by this node — see
store.upsert_records and backfill.py's module docstring; true delisting
detection is out of scope here.)

The tests at the bottom of this file (`test_*_converges_after_one_run`)
demonstrate the fixes from the Task 8 review: a row selected in "run 1"
because it needed work must NOT be reselected in "run 2" once that work is
persisted — for each of the three ways rows used to loop forever (a dismissed
row's permanent baseline reason, an LLM-refined row losing its score to the
baseline recompute, and a row dropped by rank/freshness before notify could
ever persist it).
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


# ---------------------------------------------------------------------------
# Convergence tests (Task 8 review): a row selected in "run 1" must NOT be
# reselected in "run 2" once whatever it was missing has been persisted. Each
# test chains backfill_node -> freshness_node -> rank_node -> a persistence
# step that strips `_`-prefixed keys exactly like notify.py does, then calls
# backfill_node again to prove convergence. No network: rank_mod.llm is
# either never reached (skip_llm path) or monkeypatched.
# ---------------------------------------------------------------------------

def _persist(postings):
    """Mirror notify.py's persistence step: strip transient tags, then upsert."""
    jobstore.upsert_records([{k: v for k, v in p.items() if not k.startswith("_")} for p in postings])


def test_dismissed_row_converges_after_one_run(temp_db, monkeypatch):
    """Regression: a dismissed row's fit_reason is always baseline (it is
    never LLM-refined), so selecting on `is_baseline_reason` alone reselected
    it every run forever, wasting a deterministic pass on the same 392 rows
    twice daily forever. One backfill+rank+persist pass must be enough."""
    from agents.job_scraper.nodes.freshness import freshness_node
    from agents.job_scraper.nodes.rank import rank_node

    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    _seed([{"id": "d", "status": "dismissed", "title": "SWE Intern",
            "location": "Austin, TX", "fit_score": None, "country": ""}])

    run1 = backfill_node({"new": [], "backfill": True})["new"]
    assert [p["id"] for p in run1] == ["d"], "row must be selected in run 1"
    run1 = freshness_node({"new": run1})["new"]
    run1 = rank_node({"new": run1})["new"]
    _persist(run1)

    stored = jobstore.load_records()["d"]
    assert stored["country"] == "US"
    assert stored["fit_score"] is not None

    run2 = backfill_node({"new": [], "backfill": True})["new"]
    assert run2 == [], "dismissed row must NOT be reselected once scored + countried"


def test_llm_refined_row_not_clobbered_or_reselected_after_one_run(temp_db, monkeypatch):
    """Regression: a row selected by backfill ONLY because `country` was
    blank (its score was already LLM-refined) had that real score overwritten
    by the unconditional baseline recompute in rank.py's `_skip_llm` branch —
    and because that recompute always writes a baseline reason, the row would
    then look unrefined again and be reselected forever."""
    from agents.job_scraper.nodes.freshness import freshness_node
    from agents.job_scraper.nodes.rank import rank_node
    from agents.job_scraper.nodes import rank as rank_mod

    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")

    def boom(*a, **k):
        raise AssertionError("llm() must not be called: this row only needed a country")

    monkeypatch.setattr(rank_mod, "llm", boom)

    _seed([{"id": "r", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
            "fit_score": 92, "fit_reason": "strong python + react match", "country": ""}])

    run1 = backfill_node({"new": [], "backfill": True})["new"]
    assert [p["id"] for p in run1] == ["r"]
    assert run1[0]["_skip_llm"] is True, "already refined -> no LLM needed, only country was missing"

    run1 = freshness_node({"new": run1})["new"]
    run1 = rank_node({"new": run1})["new"]
    assert run1[0]["fit_score"] == 92, "the real LLM-refined score must survive backfill + rank"
    assert run1[0]["fit_reason"] == "strong python + react match"
    _persist(run1)

    stored = jobstore.load_records()["r"]
    assert stored["country"] == "US"
    assert stored["fit_score"] == 92
    assert stored["fit_reason"] == "strong python + react match"

    run2 = backfill_node({"new": [], "backfill": True})["new"]
    assert run2 == [], "row must not be reselected — country filled, score was already refined"


def test_rescored_row_dropped_by_rank_still_converges(temp_db, monkeypatch):
    """Regression: a `_rescored` row that the model judges ineligible (or
    that scores below JOB_MIN_FIT) used to be dropped by rank_node before
    notify could ever persist its refreshed country/fit_score — so backfill
    reselected the very same row every run, forever, burning an LLM_CAP slot
    on it each time. rank.py's `_rescored` exemption must let it through so
    one pass is actually enough."""
    from agents.job_scraper.nodes.freshness import freshness_node
    from agents.job_scraper.nodes.rank import rank_node
    from agents.job_scraper.nodes import rank as rank_mod

    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":false,"score":10,"reason":"not a fit"}]',
    )

    _seed([{"id": "r", "status": "new", "title": "SWE Intern", "location": "Austin, TX",
            "fit_score": None, "country": ""}])

    run1 = backfill_node({"new": [], "backfill": True})["new"]
    assert [p["id"] for p in run1] == ["r"]
    run1 = freshness_node({"new": run1})["new"]
    run1 = rank_node({"new": run1})["new"]
    assert [p["id"] for p in run1] == ["r"], \
        "a _rescored row judged ineligible must still survive rank_node so it can be persisted"
    _persist(run1)

    stored = jobstore.load_records()["r"]
    assert stored["country"] == "US"
    assert stored["fit_score"] == 10

    run2 = backfill_node({"new": [], "backfill": True})["new"]
    assert run2 == [], "row must not be reselected once it has both a country and a score"
