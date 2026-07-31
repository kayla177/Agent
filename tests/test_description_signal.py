"""The JD text both fit-scoring paths read: stored whole, sliced head+tail, repaired.

Measured 2026-07-25 against the live corpus: EVERY one of the 551 stored
descriptions was exactly 1200 chars long (p50 = p90 = max = 1200), i.e. the old
`ats._DESC_MAX` cap, not natural variation. The first ~1200 chars of a real JD
are company boilerplate, so the words the scorers depend on had been cut off —
"qualification" survived in 1% of rows, "requirement" 4%, "bachelor" 2%,
"python" 4%, "react" 0%. Consequences: `scoring.score_baseline` could not match
a single tech keyword (so "RTL Intern" tied with "Software Engineering Intern"
at the top of sort-by-fit), and `rank._prompt` asked llama3.1:8b to judge
undergrad eligibility from a marketing paragraph.

Three properties are locked down here, one per part of the fix:

* `ats._strip_html` keeps the JD whole, capped only by a sanity ceiling.
* `rank._desc_slice` spends its prompt budget on the OPENING *and the ENDING*,
  because the qualifications sit at the bottom. Head-only is the bug.
* `store.refresh_descriptions` repairs rows already in the store from the text
  the current run already downloaded (`state["raw"] + state["filtered"]`).
  Without it the 551 truncated rows stay truncated forever: `dedupe` drops
  already-seen ids before `notify` persists — structurally the same bug as the
  frozen `last_seen`.
"""

from __future__ import annotations

import json

import config
from agents.job_scraper import ats
from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes.notify import make_notify_node
from agents.job_scraper.nodes.rank import _DESC_HEAD, _DESC_TAIL, _desc_slice, _prompt


# --------------------------------------------------------------------------
# 1. Storage: the full JD is kept.
# --------------------------------------------------------------------------

def test_strip_html_no_longer_truncates_at_1200():
    """A realistic ~5k-char JD must survive whole, requirements included."""
    body = "<p>At Acme, we are passionate about data teams.</p>" + ("<li>filler</li>" * 400)
    raw = body + "<h3>Qualifications</h3><li>Experience with Python and React</li>"
    out = ats._strip_html(raw)

    assert len(out) > 1200, "the old 1200-char cap must be gone"
    assert "Qualifications" in out, "the qualifications heading must survive"
    assert "Python" in out, "the tech terms the keyword baseline matches must survive"


def test_strip_html_caps_at_the_sanity_ceiling():
    """One pathological board still cannot bloat the database."""
    out = ats._strip_html("<p>" + ("x" * 60_000) + "</p>")
    assert len(out) == ats._DESC_MAX
    assert ats._DESC_MAX == 20_000, "the ceiling is load-bearing; changing it is a decision"


# --------------------------------------------------------------------------
# 2. Prompt slicing: head AND tail.
# --------------------------------------------------------------------------

def test_desc_slice_returns_both_ends_within_budget():
    head_marker = "ROLE: Software Engineering Intern at Acme."
    tail_marker = "Qualifications: pursuing a Bachelor's; experience with Python and React."
    middle = " company boilerplate." * 500
    desc = head_marker + middle + tail_marker

    out = _desc_slice(desc)

    assert head_marker[:20] in out, "the opening identifies the role and must be kept"
    assert tail_marker in out, "the ENDING carries the qualifications — head-only was the bug"
    assert len(out) <= _DESC_HEAD + _DESC_TAIL + 16, "must stay inside its char budget"
    assert "..." in out, "an elided middle must be marked, not silently joined"


def test_desc_slice_returns_a_short_description_whole_without_duplication():
    desc = "Short JD. Qualifications: Bachelor's, Python."
    assert _desc_slice(desc) == desc
    # A naive head + tail concatenation would repeat the overlap.
    assert _desc_slice(desc).count("Qualifications") == 1
    assert _desc_slice("") == ""
    assert _desc_slice(None) == ""


def test_desc_slice_boundary_is_exactly_head_plus_tail():
    """At the threshold the text is returned whole; one char over, it elides."""
    exact = "a" * (_DESC_HEAD + _DESC_TAIL)
    assert _desc_slice(exact) == exact
    over = "H" + "m" * (_DESC_HEAD + _DESC_TAIL - 1) + "T"
    out = _desc_slice(over)
    assert out.startswith("H") and out.endswith("T") and out != over


def test_prompt_shows_the_model_the_qualifications(monkeypatch):
    """End-to-end on the real prompt builder, not just the slicer."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    tail = "Minimum qualifications: currently pursuing a Bachelor's in CS. Python, React."
    batch = [{
        "title": "Software Engineering Intern", "company": "Acme", "location": "Austin, TX",
        "description": "At Acme we love data." + (" boilerplate." * 400) + tail,
    }]
    out = _prompt("Python React SQL", batch)
    assert "Software Engineering Intern" in out
    assert "Minimum qualifications" in out, "requirements must reach the model"
    assert "Bachelor's" in out, "the eligibility signal must reach the model"


# --------------------------------------------------------------------------
# 3. Repairing the rows already in the store.
# --------------------------------------------------------------------------

def _seed(**over) -> dict:
    rec = {
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "title": "SWE Intern", "status": "applied", "country": "US",
        "fit_score": 91, "fit_reason": "strong python + react match",
        "first_seen": "2026-01-01", "last_seen": "2026-07-01",
        "description": "At Acme, we are passionate about data teams.",
    }
    rec.update(over)
    jobstore.replace_record(rec)
    return rec


def test_refresh_descriptions_updates_a_shorter_stored_row(temp_db):
    _seed()
    full = "At Acme, we are passionate about data teams." + (" x" * 2000) + " Qualifications: Python."

    assert jobstore.refresh_descriptions(
        [{"id": "Acme:greenhouse:1", "description": full}]
    ) == 1

    assert jobstore.load_records()["Acme:greenhouse:1"]["description"] == full


def test_refresh_descriptions_writes_both_the_column_and_the_blob(temp_db):
    """The dual-write rule: the mirrored column and the `data` blob must agree."""
    _seed()
    full = "long " * 500

    jobstore.refresh_descriptions([{"id": "Acme:greenhouse:1", "description": full}])

    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT description, data FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)
        ).fetchone()
    assert row["description"] == full, "mirrored column must be updated"
    assert json.loads(row["data"])["description"] == full, "data blob must be updated too"


def test_refresh_descriptions_never_shortens_a_row(temp_db):
    """A board briefly serving a stub must not be able to clobber good text."""
    good = "the full job description with Qualifications: Python. " * 40
    _seed(description=good)

    assert jobstore.refresh_descriptions(
        [{"id": "Acme:greenhouse:1", "description": "At Acme, we are passionate"}]
    ) == 0
    assert jobstore.load_records()["Acme:greenhouse:1"]["description"] == good

    # Equal length is not longer either — no pointless rewrite.
    assert jobstore.refresh_descriptions(
        [{"id": "Acme:greenhouse:1", "description": good}]
    ) == 0


def test_refresh_descriptions_ignores_ids_absent_from_the_store(temp_db):
    """A genuinely new posting is `upsert_records`' job, not this one."""
    assert jobstore.refresh_descriptions([{"id": "nope", "description": "x" * 500}]) == 0
    assert jobstore.load_records() == {}


def test_refresh_descriptions_is_a_no_op_on_empty_input(temp_db):
    _seed()
    assert jobstore.refresh_descriptions([]) == 0
    assert jobstore.refresh_descriptions([{"id": "Acme:greenhouse:1"}]) == 0
    assert jobstore.refresh_descriptions([{"description": "x" * 500}]) == 0
    assert jobstore.load_records()["Acme:greenhouse:1"]["description"] == \
        "At Acme, we are passionate about data teams."


def test_refresh_descriptions_touches_nothing_but_the_description(temp_db):
    _seed()
    jobstore.refresh_descriptions(
        [{"id": "Acme:greenhouse:1", "description": "long " * 500,
          # A pipeline tag riding along must not be able to reach the store.
          "_rescored": True, "status": "new", "fit_score": 3,
          "country": "OTHER", "first_seen": "2020-01-01", "last_seen": "2020-01-01"}]
    )

    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["status"] == "applied", "status must never be rewritten from a fetched posting"
    assert rec["first_seen"] == "2026-01-01", "first_seen must never move"
    assert rec["last_seen"] == "2026-07-01", "only touch_last_seen may write last_seen"
    assert rec["country"] == "US"
    assert rec["fit_score"] == 91
    assert "_rescored" not in rec

    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", ("Acme:greenhouse:1",)
        ).fetchone()
    assert row["status"] == "applied"
    assert row["last_seen"] == "2026-07-01"
    assert "_rescored" not in json.loads(row["data"])


def test_refresh_descriptions_collapses_duplicate_ids_to_the_longest(temp_db):
    """`raw` and `filtered` overlap, so the same id arrives more than once."""
    _seed()
    short, long = "medium length text here", "the genuinely full JD " * 50

    assert jobstore.refresh_descriptions([
        {"id": "Acme:greenhouse:1", "description": short},
        {"id": "Acme:greenhouse:1", "description": long},
        {"id": "Acme:greenhouse:1", "description": short},
    ]) == 1, "one write per id, not one per duplicate"
    assert jobstore.load_records()["Acme:greenhouse:1"]["description"] == long


# --------------------------------------------------------------------------
# 4. notify wiring.
# --------------------------------------------------------------------------

def test_notify_repairs_and_logs_the_refreshed_count(temp_db, monkeypatch, capsys):
    """`raw`/`filtered` are still in state at notify time — no re-fetch pass."""
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    _seed(id="old", status="new")
    full = "At Acme, we are passionate about data teams." + (" x" * 900) + " Qualifications: Python."

    notify = make_notify_node(send=False)
    notify({
        "new": [],  # dedupe already dropped this already-seen posting
        "raw": [{"id": "old", "description": full}, {"id": "unknown", "description": full}],
        "filtered": [{"id": "old", "description": full}],
    })

    out = capsys.readouterr().out
    assert "refreshed the description on 1 stored posting(s)" in out, \
        "a bounded repair pass must report itself, like touch_last_seen / sweep_ghosts"
    assert jobstore.load_records()["old"]["description"] == full


def test_notify_repair_runs_after_upsert_so_a_backlog_row_is_not_reclobbered(
    temp_db, monkeypatch
):
    """A `_rescored` row carries the OLD short description out of the store.

    `upsert_records` merges the posting OVER the stored record, so if the repair
    ran first its longer text would be overwritten by the backfilled stub.
    """
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    stale = "At Acme, we are passionate about data teams."
    _seed(id="old", status="new")
    full = stale + (" x" * 900) + " Qualifications: Python."

    notify = make_notify_node(send=False)
    notify({
        "new": [{"id": "old", "company": "Acme", "title": "SWE Intern", "country": "US",
                 "fit_score": 91, "description": stale, "_rescored": True}],
        "raw": [{"id": "old", "description": full}],
        "filtered": [{"id": "old", "description": full}],
    })

    assert jobstore.load_records()["old"]["description"] == full
