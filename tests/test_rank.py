"""rank_node / filter_node behavior.

The critical guarantee: a null or malformed LLM reply must never wipe out the
deterministic baseline score. That is the regression that left all 523 rows at
fit_score=NULL.
"""

from __future__ import annotations

import config
import profile_store
from agents.job_scraper.nodes import rank as rank_mod
from agents.job_scraper.nodes.filter import filter_node
from agents.job_scraper.nodes.rank import rank_node
from agents.job_scraper.scoring import is_baseline_reason

_POSTINGS = [
    {"id": "a", "title": "Software Engineer Intern", "description": "Python and React.", "location": "Austin, TX"},
    {"id": "b", "title": "Data Intern", "description": "SQL dashboards.", "location": "London"},
]


def test_baseline_survives_total_llm_failure(monkeypatch):
    """Model unavailable -> every posting still has a score."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(rank_mod, "llm", boom)
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert len(out) == 2
    assert all(p["fit_score"] is not None for p in out)


def test_baseline_survives_null_llm_scores(monkeypatch):
    """Model replies but scores are null -> baseline is kept, not overwritten."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":true,"score":null},{"i":1,"eligible":true,"score":null}]',
    )
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert all(p["fit_score"] is not None for p in out)
    assert all(p["fit_reason"] for p in out)


def test_llm_score_overrides_baseline(monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":true,"score":91,"reason":"excellent match"},'
                        '{"i":1,"eligible":true,"score":42,"reason":"weak"}]',
    )
    out = {p["id"]: p for p in rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]}
    assert out["a"]["fit_score"] == 91
    assert out["a"]["fit_reason"] == "excellent match"


def test_ineligible_still_dropped(monkeypatch):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":false,"score":90},{"i":1,"eligible":true,"score":50}]',
    )
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert [p["id"] for p in out] == ["b"]


def test_no_profile_and_llm_failure_still_reads_as_baseline(monkeypatch):
    """Regression: no profile configured (the current default state) + the model
    unavailable used to break the `is_baseline_reason` persistence contract, since
    the exception text was appended straight onto "no profile keywords set",
    defeating the exact-match check and stranding the row as "already refined"
    for the backfill node. Neutralize BOTH profile sources so the empty-profile
    path is deterministic rather than depending on the live DB being blank.
    """
    monkeypatch.setattr(config, "JOB_PROFILE", "")
    monkeypatch.setattr(profile_store, "fit_profile_text", lambda: "")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(rank_mod, "llm", boom)
    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert len(out) == 2
    for p in out:
        assert is_baseline_reason(p["fit_reason"]) is True
        assert "ollama down" not in p["fit_reason"]


def test_is_baseline_reason_excludes_legacy_fallback_strings():
    """rank.py's own legacy fallback strings ("unranked", "no profile set") must
    keep classifying as NOT baseline — they are not written by scoring.py."""
    assert is_baseline_reason("unranked") is False
    assert is_baseline_reason("no profile set") is False


def test_filter_tags_country_and_keeps_everything(monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    out = filter_node({"raw": [
        {"title": "Software Engineer Intern", "location": "Austin, TX"},
        {"title": "SWE Intern", "location": "Dublin, Ireland"},
        {"title": "SWE Intern", "location": "2 Locations"},
        {"title": "Senior Engineer", "location": "Austin, TX"},   # dropped: not early-career
    ]})["filtered"]
    assert len(out) == 3, "location must NEVER drop a posting; only the role filter drops"
    assert {p["country"] for p in out} == {"US", "OTHER", "UNKNOWN"}


def test_skip_llm_row_never_reaches_the_model(monkeypatch):
    """Regression (Task 8 review): the `_skip_llm` split could be deleted
    entirely with the whole suite still green, sending all rows (523 on the
    live DB) to the model. A `_skip_llm` row's content must never appear in a
    prompt handed to `llm()`, while a refinable row's does."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    calls: list[str] = []

    def spy(role, prompt, **kwargs):
        calls.append(prompt)
        return '[{"i":0,"eligible":true,"score":91,"reason":"great"}]'

    monkeypatch.setattr(rank_mod, "llm", spy)
    out = {p["id"]: p for p in rank_node({"new": [
        {"id": "skip", "title": "Data Intern", "description": "SQL dashboards.",
         "location": "Austin, TX", "_skip_llm": True},
        {"id": "go", "title": "Software Engineer Intern", "description": "Python and React.",
         "location": "Austin, TX"},
    ]})["new"]}
    assert calls, "the refinable row must have triggered at least one model call"
    assert not any("Data Intern" in prompt for prompt in calls), \
        "a _skip_llm row must never be sent to the model"
    assert out["go"]["fit_score"] == 91, "the refinable row IS sent and refined"


def test_skip_llm_row_with_existing_refined_score_is_not_clobbered(monkeypatch):
    """Regression (Task 8 review): backfill can select a row for a reason that
    has nothing to do with its score — e.g. only `country` was blank, while
    the row already carries a real, LLM-refined fit_score/fit_reason. The old
    unconditional `score_baseline()` recompute in the `_skip_llm` branch
    clobbered that (measured: 92/"strong python + react match" ->
    35/"matched: none"), which also made the row's reason look baseline-only
    again — reselecting it forever. `llm()` is monkeypatched to raise so this
    also proves no network/model call happens for this row.
    """
    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)

    def boom(*a, **k):
        raise AssertionError("llm() must not be called for a _skip_llm-only batch")

    monkeypatch.setattr(rank_mod, "llm", boom)
    out = rank_node({"new": [
        {"id": "r", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": 92, "fit_reason": "strong python + react match", "_skip_llm": True},
    ]})["new"]
    assert out[0]["fit_score"] == 92, "a real, already-refined score must survive"
    assert out[0]["fit_reason"] == "strong python + react match"


def test_refined_score_with_empty_reason_gets_a_non_baseline_reason(monkeypatch):
    """Regression (Task 14 convergence gap): the model's prompt asks for a
    <=12 word reason but nothing guarantees one, and `_parse` normalizes a
    missing reason to "". If a usable score arrived with an empty reason, the
    OLD code's `if hit["reason"]:` guard left the baseline reason in place, so
    `is_baseline_reason()` stayed True and backfill would reselect (and
    re-clobber) the row forever. A refined score must always leave a
    non-baseline reason behind."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":true,"score":88,"reason":""}]',
    )
    out = rank_node({"new": [dict(_POSTINGS[0])]})["new"]
    assert out[0]["fit_score"] == 88
    assert is_baseline_reason(out[0]["fit_reason"]) is False

    # A second pass (as backfill would do) must not reselect this row: it is
    # no longer "lacks a score" nor "still baseline".
    from agents.job_scraper.nodes.backfill import _needs_llm_refinement
    assert _needs_llm_refinement(out[0]) is False


def test_rescored_row_survives_eligibility_and_min_fit_drops(monkeypatch):
    """Regression (Task 8 review): rank_node's eligible/JOB_MIN_FIT drops exist
    to decide which NEWLY discovered postings are worth keeping. A `_rescored`
    backlog row is already in the DB either way, so dropping it here discarded
    its freshly computed country/fit_score before notify could ever persist
    them — causing backfill to reselect (and, if refinable, re-bill an
    LLM_CAP slot for) the same row every run, forever."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 99)  # nothing baseline-scores this high
    monkeypatch.setattr(
        rank_mod, "llm",
        lambda *a, **k: '[{"i":0,"eligible":false,"score":10,"reason":"not a fit"}]',
    )
    out = rank_node({"new": [
        {"id": "r", "title": "SWE Intern", "location": "Austin, TX",
         "fit_score": None, "country": "US", "_rescored": True},
    ]})["new"]
    assert [p["id"] for p in out] == ["r"], \
        "a _rescored row must survive both the eligibility drop and the min-fit drop"
    assert out[0]["eligible"] is False
    assert out[0]["fit_score"] == 10
