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
