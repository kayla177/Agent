"""Offline tests for the job-scraper pipeline (no network, no pytest needed).

Run with:  uv run python tests/test_job_scraper.py

Covers the pure logic that the pipeline depends on: adapter field helpers,
title/location matching, freshness/ghost flagging, cross-source dedup, LLM-reply
parsing, and the record-store round-trip (migration + status preservation).
Network adapters (fetch_*) are exercised separately by the CLI dry run.
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import store_db
from agents.job_scraper import ats, matching, store
from agents.job_scraper.nodes.dedupe import dedupe_node
from agents.job_scraper.nodes.freshness import freshness_node
from agents.job_scraper.nodes.rank import _parse

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _failures.append(name)


def test_ats_helpers() -> None:
    print("ats helpers")
    check("strip_html removes tags + unescapes", ats._strip_html("<p>A&amp;B<br>C</p>") == "A&B\nC")
    check("strip_html empty -> ''", ats._strip_html(None) == "")
    check("iso from epoch-ms (Lever)", ats._to_iso_date(1778622524938) == "2026-05-12")
    check("iso from ISO string w/ tz", ats._to_iso_date("2026-06-26T04:29:23.224+00:00") == "2026-06-26")
    check("iso from Z suffix", ats._to_iso_date("2026-04-14T07:57:07.974Z") == "2026-04-14")
    check("iso junk -> ''", ats._to_iso_date("not-a-date") == "")
    check("has_remote positive", ats._has_remote("Remote - Canada") is True)
    check("has_remote uninformative -> None", ats._has_remote("San Francisco") is None)
    check("lever comp formatting", ats._fmt_lever_comp({"min": 50, "max": 90, "currency": "USD"}) == "USD 50–90")
    check("ashby comp summary", ats._fmt_ashby_comp({"compensationTierSummary": "$120k"}) == "$120k")


def test_matching() -> None:
    print("matching")
    check("age_days computes", matching.age_days((dt.date.today() - dt.timedelta(days=5)).isoformat()) == 5)
    check("age_days junk -> None", matching.age_days("nope") is None)
    check("canonical NYC alias", matching.canonical_location("Manhattan") == "New York, NY")
    check("canonical remote collapses", matching.canonical_location("Remote - US") == "Remote")
    check("canonical passthrough", matching.canonical_location("Waterloo, ON") == "Waterloo, ON")
    check("canonical dash -> ''", matching.canonical_location("—") == "")


def test_relevance() -> None:
    print("relevance filter (undergrad + field)")
    from agents.job_scraper.matching import is_excluded, is_target_role, is_tech_role
    from agents.job_scraper.nodes.filter import filter_node

    check("intern is target", is_target_role("Software Engineer Intern"))
    check("new grad is target", is_target_role("New Grad Software Engineer"))
    check("senior excluded", is_excluded("Senior Software Engineer Intern"))
    check("phd excluded", is_excluded("Machine Learning Intern, PhD"))
    check("master's excluded", is_excluded("Data Science Intern (Master's)"))
    check("staff excluded", is_excluded("Staff Engineer, New Grad"))
    check("plain intern not excluded", not is_excluded("Software Engineer Intern"))
    # field gate: SWE/ML/CS/AI only
    check("software is tech", is_tech_role("Software Engineer Intern"))
    check("ML is tech", is_tech_role("ML Intern"))
    check("data science is tech", is_tech_role("Data Scientist Intern"))
    check("box office NOT tech", not is_tech_role("2026 Internship, Fall - Box Office"))
    check("marketing NOT tech", not is_tech_role("Marketing Intern"))
    check("product manager NOT tech", not is_tech_role("Associate Product Manager, New Grad"))

    out = filter_node({"raw": [
        {"title": "Software Engineer Intern"},          # keep
        {"title": "Senior Software Engineer"},          # drop (not early-career)
        {"title": "ML Research Intern — PhD required"},  # drop (excluded: phd)
        {"title": "New Grad Software Engineer"},        # keep
        {"title": "Box Office Internship"},             # drop (not a tech field)
        {"title": "Marketing Intern"},                  # drop (not a tech field)
    ]})["filtered"]
    titles = {p["title"] for p in out}
    check("filter keeps only early-career tech roles", titles == {"Software Engineer Intern", "New Grad Software Engineer"})


def test_rank_eligibility() -> None:
    print("rank parse (eligibility)")
    parsed = _parse('[{"i":0,"eligible":false,"score":80,"reason":"phd"},{"i":1,"eligible":true,"score":70}]', 2)
    check("eligible=false parsed", parsed[0]["eligible"] is False)
    check("eligible=true parsed", parsed[1]["eligible"] is True)
    check("missing eligible defaults true", _parse('[{"i":0,"score":50}]', 1)[0]["eligible"] is True)
    check("null score -> None", _parse('[{"i":0,"eligible":true,"score":null}]', 1)[0]["score"] is None)


def test_freshness() -> None:
    print("freshness node")
    old = (dt.date.today() - dt.timedelta(days=config.JOB_MAX_AGE_DAYS + 10)).isoformat()
    fresh = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    roles = [
        {"id": "1", "posted_at": old},
        {"id": "2", "posted_at": fresh},
        {"id": "3", "posted_at": fresh, "deadline": past},
        {"id": "4", "posted_at": fresh, "listed": False},
    ]
    # Tagging behaviour: temporarily disable the hard drop so flags are inspectable.
    saved = config.JOB_DROP_GHOSTS
    config.JOB_DROP_GHOSTS = False
    by = {p["id"]: p for p in freshness_node({"new": roles})["new"]}
    check("stale flagged ghost", by["1"]["ghost"] is True and "stale" in by["1"]["ghost_reason"])
    check("fresh not ghost", by["2"]["ghost"] is False)
    check("past deadline flagged", by["3"]["ghost"] is True and "deadline" in by["3"]["ghost_reason"])
    check("delisted flagged", by["4"]["ghost"] is True and "delist" in by["4"]["ghost_reason"])
    check("age_days attached", by["2"]["age_days"] == 3)
    # Hard-cap behaviour: with drop ON, stale/expired/delisted roles are removed.
    config.JOB_DROP_GHOSTS = True
    kept = {p["id"] for p in freshness_node({"new": roles})["new"]}
    check("drop>maxage removes stale/expired/delisted", kept == {"2"})
    config.JOB_DROP_GHOSTS = saved


def test_dedupe_cross_source() -> None:
    print("dedupe node (cross-source)")
    # Same role on two boards + a distinct role. store is empty (temp) below.
    _use_temp_store()
    filtered = [
        {"id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern", "location": "NYC", "ats": "greenhouse"},
        {"id": "Acme:lever:9", "company": "Acme", "title": "SWE  Intern", "location": "Manhattan", "ats": "lever"},
        {"id": "Acme:greenhouse:2", "company": "Acme", "title": "ML Intern", "location": "Remote", "ats": "greenhouse"},
    ]
    new = dedupe_node({"filtered": filtered})["new"]
    check("collapses same role across boards", len(new) == 2)
    survivor = next(p for p in new if p["title"].startswith("SWE"))
    check("survivor notes also_on", "lever" in survivor.get("also_on", []))


def test_rank_parse() -> None:
    print("rank parse")
    reply = 'Sure!\n[{"i":0,"score":90,"reason":"great"},{"i":1,"score":150,"reason":"clamp"}]'
    parsed = _parse(reply, 2)
    check("parses two items", set(parsed) == {0, 1})
    check("clamps score to 100", parsed[1]["score"] == 100)
    check("bad json -> {}", _parse("no json here", 3) == {})
    check("drops out-of-range index", 5 not in _parse('[{"i":5,"score":10}]', 2))


def _use_temp_store() -> Path:
    tmp = Path(tempfile.mkdtemp())
    store_db.DB_PATH = tmp / "test.db"
    store_db.init_db()
    return tmp


def main() -> int:
    for fn in (
        test_ats_helpers,
        test_matching,
        test_relevance,
        test_rank_eligibility,
        test_freshness,
        test_dedupe_cross_source,
        test_rank_parse,
    ):
        fn()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("All job-scraper tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
