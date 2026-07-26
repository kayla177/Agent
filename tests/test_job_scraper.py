"""Offline tests for the job-scraper pipeline (no network, no pytest needed).

Run with:  .venv/bin/python -m pytest tests/test_job_scraper.py

Covers the pure logic that the pipeline depends on: adapter field helpers,
title/location matching, freshness/ghost flagging, cross-source dedup, LLM-reply
parsing, and the record-store round-trip (migration + status preservation).
Network adapters (fetch_*) are exercised separately by the CLI dry run.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agents.job_scraper import ats, matching, store
from agents.job_scraper.nodes.dedupe import dedupe_node
from agents.job_scraper.nodes.freshness import freshness_node
from agents.job_scraper.nodes.rank import _parse



def test_ats_helpers() -> None:
    assert ats._strip_html("<p>A&amp;B<br>C</p>") == "A&B\nC", "strip_html removes tags + unescapes"
    assert ats._strip_html(None) == "", "strip_html empty -> ''"
    assert ats._to_iso_date(1778622524938) == "2026-05-12", "iso from epoch-ms (Lever)"
    assert ats._to_iso_date("2026-06-26T04:29:23.224+00:00") == "2026-06-26", "iso from ISO string w/ tz"
    assert ats._to_iso_date("2026-04-14T07:57:07.974Z") == "2026-04-14", "iso from Z suffix"
    assert ats._to_iso_date("not-a-date") == "", "iso junk -> ''"
    assert ats._has_remote("Remote - Canada") is True, "has_remote positive"
    assert ats._has_remote("San Francisco") is None, "has_remote uninformative -> None"
    assert ats._fmt_lever_comp({"min": 50, "max": 90, "currency": "USD"}) == "USD 50–90", "lever comp formatting"
    assert ats._fmt_ashby_comp({"compensationTierSummary": "$120k"}) == "$120k", "ashby comp summary"


def test_matching() -> None:
    assert matching.age_days((dt.date.today() - dt.timedelta(days=5)).isoformat()) == 5, "age_days computes"
    assert matching.age_days("nope") is None, "age_days junk -> None"
    assert matching.canonical_location("Manhattan") == "New York, NY", "canonical NYC alias"
    assert matching.canonical_location("Remote - US") == "Remote", "canonical remote collapses"
    assert matching.canonical_location("Waterloo, ON") == "Waterloo, ON", "canonical passthrough"
    assert matching.canonical_location("—") == "", "canonical dash -> ''"


def test_relevance() -> None:
    from agents.job_scraper.matching import is_excluded, is_target_role
    from agents.job_scraper.nodes.filter import filter_node

    assert is_target_role("Software Engineer Intern"), "intern is target"
    assert is_target_role("New Grad Software Engineer"), "new grad is target"
    assert is_excluded("Senior Software Engineer Intern"), "senior excluded"
    assert is_excluded("Machine Learning Intern, PhD"), "phd excluded"
    assert is_excluded("Data Science Intern (Master's)"), "master's excluded"
    assert is_excluded("Staff Engineer, New Grad"), "staff excluded"
    assert not is_excluded("Software Engineer Intern"), "plain intern not excluded"
    assert not is_excluded("New Grad SWE"), "new grad not excluded"

    out = filter_node({"raw": [
        {"title": "Software Engineer Intern"},          # keep
        {"title": "Senior Software Engineer"},          # drop (not early-career)
        {"title": "ML Research Intern — PhD required"},  # drop (excluded: phd)
        {"title": "New Grad Software Engineer"},        # keep
        {"title": "Staff Data Scientist"},              # drop (not early-career)
    ]})["filtered"]
    titles = {p["title"] for p in out}
    assert titles == {"Software Engineer Intern", "New Grad Software Engineer"}, "filter keeps only undergrad-eligible early-career"


def test_rank_eligibility() -> None:
    parsed = _parse('[{"i":0,"eligible":false,"score":80,"reason":"phd"},{"i":1,"eligible":true,"score":70}]', 2)
    assert parsed[0]["eligible"] is False, "eligible=false parsed"
    assert parsed[1]["eligible"] is True, "eligible=true parsed"
    assert _parse('[{"i":0,"score":50}]', 1)[0]["eligible"] is True, "missing eligible defaults true"
    assert _parse('[{"i":0,"eligible":true,"score":null}]', 1)[0]["score"] is None, "null score -> None"


def test_freshness() -> None:
    old = (dt.date.today() - dt.timedelta(days=config.JOB_MAX_AGE_DAYS + 10)).isoformat()
    fresh = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    out = freshness_node({"new": [
        {"id": "1", "posted_at": old},
        {"id": "2", "posted_at": fresh},
        {"id": "3", "posted_at": fresh, "deadline": past},
        {"id": "4", "posted_at": fresh, "listed": False},
    ]})["new"]
    by = {p["id"]: p for p in out}
    assert by["1"]["ghost"] is True and "stale" in by["1"]["ghost_reason"], "stale flagged ghost"
    assert by["2"]["ghost"] is False, "fresh not ghost"
    assert by["3"]["ghost"] is True and "deadline" in by["3"]["ghost_reason"], "past deadline flagged"
    assert by["4"]["ghost"] is True and "delist" in by["4"]["ghost_reason"], "delisted flagged"
    assert by["2"]["age_days"] == 3, "age_days attached"


def test_rescored_ghost_survives_hard_drop(monkeypatch) -> None:
    """Regression (Task 8 review): with JOB_DROP_GHOSTS=True, a `_rescored`
    backlog row flagged as stale used to be dropped here before notify could
    persist its refreshed country/fit_score — so backfill reselected the same
    row every run, forever. `_rescored` rows are already in the DB either way,
    so this hard-drop (meant for NEW postings) must exempt them. Uses `assert`
    (not the module's `check()` helper, which never fails the test) because
    this invariant must actually be enforced.
    """
    monkeypatch.setattr(config, "JOB_DROP_GHOSTS", True)
    old = (dt.date.today() - dt.timedelta(days=config.JOB_MAX_AGE_DAYS + 10)).isoformat()
    out = freshness_node({"new": [
        {"id": "r", "posted_at": old, "_rescored": True},
    ]})["new"]
    assert [p["id"] for p in out] == ["r"], "rescored ghost row must NOT be dropped"
    assert out[0]["ghost"] is True, "ghost must still be correctly flagged"


def test_dedupe_cross_source(temp_db) -> None:
    filtered = [
        {"id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern", "location": "NYC", "ats": "greenhouse"},
        {"id": "Acme:lever:9", "company": "Acme", "title": "SWE  Intern", "location": "Manhattan", "ats": "lever"},
        {"id": "Acme:greenhouse:2", "company": "Acme", "title": "ML Intern", "location": "Remote", "ats": "greenhouse"},
    ]
    new = dedupe_node({"filtered": filtered})["new"]
    assert len(new) == 2
    survivor = next(p for p in new if p["title"].startswith("SWE"))
    assert "lever" in survivor.get("also_on", [])


def test_rank_parse() -> None:
    reply = 'Sure!\n[{"i":0,"score":90,"reason":"great"},{"i":1,"score":150,"reason":"clamp"}]'
    parsed = _parse(reply, 2)
    assert set(parsed) == {0, 1}, "parses two items"
    assert parsed[1]["score"] == 100, "clamps score to 100"
    assert _parse("no json here", 3) == {}, "bad json -> {}"
    assert 5 not in _parse('[{"i":5,"score":10}]', 2), "drops out-of-range index"
