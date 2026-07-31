"""The undergrad-eligibility screen TAGS a posting; it must never drop one.

`rank_node` used to do this:

    new = [p for p in new if p.get("_rescored") or p.get("eligible", True)]

which discarded the posting before `notify` persisted anything. So an ineligible
role never entered the database at all: it was never visible, could not be
audited or overridden, and was re-fetched and re-dropped on every subsequent run.

That would be defensible if the gate were accurate. It is not. Measured
2026-07-30 across two independent samples of real postings that `filter_node`
passes, with unambiguously undergrad-eligible titles, 33-44% came back
`eligible=False` — casualties included "Software Engineering Intern", "Data
Science Intern", "Software Engineering Intern - Embedded Platforms" and
"Robotics Intern, Deployment".

The fix follows the `country` precedent exactly: store everything, hide it in the
UI, keep it auditable and reversible. Withheld from the digest, hidden behind a
board toggle, never deleted.

The load-bearing invariants, in order of how badly each one broke:
  1. rows out of `rank_node` == rows in (nothing is discarded, ever);
  2. every failure path defaults to ELIGIBLE — never over-hide;
  3. the tag reaches BOTH the mirrored column and the `data` blob;
  4. the digest withholds it and says how many.
"""

from __future__ import annotations

import json

import config
import store_db
from agents.job_scraper import store as jobstore
from agents.job_scraper.nodes import rank as rank_mod
from agents.job_scraper.nodes.notify import make_notify_node
from agents.job_scraper.nodes.rank import rank_node

_POSTINGS = [
    {"id": "a", "title": "Software Engineering Intern", "company": "Acme",
     "description": "Python and React.", "location": "Austin, TX"},
    {"id": "b", "title": "Data Intern", "company": "Zeta",
     "description": "SQL dashboards.", "location": "Toronto, ON"},
    {"id": "c", "title": "ML Intern", "company": "Nova",
     "description": "PyTorch.", "location": "Remote"},
]


def _rank(monkeypatch, reply, postings=None):
    monkeypatch.setattr(config, "JOB_PROFILE", "Python React SQL")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(rank_mod, "llm", lambda *a, **k: reply)
    return rank_node({"new": [dict(p) for p in (postings or _POSTINGS)]})["new"]


# --------------------------------------------------------------------------
# 1. The invariant that broke: nothing is discarded.
# --------------------------------------------------------------------------

def test_rank_node_never_changes_the_row_count(monkeypatch):
    """The regression guard. Rows out must equal rows in, whatever the model says."""
    reply = ('[{"i":0,"eligible":false,"score":90},'
             '{"i":1,"eligible":false,"score":80},'
             '{"i":2,"eligible":false,"score":70}]')
    out = _rank(monkeypatch, reply)
    assert len(out) == len(_POSTINGS), "rank_node must not drop rows"
    assert sorted(p["id"] for p in out) == ["a", "b", "c"]
    assert all(p["eligible"] is False for p in out), "all three were tagged, none removed"


def test_every_ineligible_row_carries_a_reason(monkeypatch):
    out = _rank(monkeypatch, '[{"i":0,"eligible":false,"reason":"requires PhD"}]')
    by_id = {p["id"]: p for p in out}
    assert by_id["a"]["eligible_reason"] == "requires PhD"
    # A model that says false without a reason must still produce a usable badge.
    out = _rank(monkeypatch, '[{"i":0,"eligible":false}]')
    assert {p["id"]: p for p in out}["a"]["eligible_reason"] == \
        "screened out: not undergrad-eligible"


# --------------------------------------------------------------------------
# 2. Never over-hide: every failure path leaves the row eligible.
# --------------------------------------------------------------------------

def test_model_failure_leaves_every_row_eligible(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    monkeypatch.setattr(rank_mod, "llm", boom)

    out = rank_node({"new": [dict(p) for p in _POSTINGS]})["new"]
    assert len(out) == 3
    assert all(p["eligible"] is True for p in out), "a transport error must never hide a role"
    assert all(p["eligible_reason"] == "" for p in out)


def test_unparseable_reply_leaves_every_row_eligible(monkeypatch):
    out = _rank(monkeypatch, "I'm sorry, I can't help with that.")
    assert len(out) == 3
    assert all(p["eligible"] is True for p in out)
    assert all(p["eligible_reason"] == "" for p in out)


def test_a_missing_index_leaves_that_row_eligible(monkeypatch):
    """The model answered about index 0 only; 1 and 2 must not be hidden."""
    out = _rank(monkeypatch, '[{"i":0,"eligible":false,"reason":"senior"}]')
    by_id = {p["id"]: p for p in out}
    assert by_id["a"]["eligible"] is False
    assert by_id["b"]["eligible"] is True and by_id["b"]["eligible_reason"] == ""
    assert by_id["c"]["eligible"] is True and by_id["c"]["eligible_reason"] == ""


def test_baseline_only_rows_default_to_eligible(monkeypatch):
    """`_skip_llm` rows never face the screen, so they must never be hidden."""
    monkeypatch.setattr(config, "JOB_PROFILE", "Python")
    monkeypatch.setattr(config, "JOB_MIN_FIT", 0)
    out = rank_node({"new": [{**_POSTINGS[0], "_skip_llm": True}]})["new"]
    assert out[0]["eligible"] is True
    assert out[0]["eligible_reason"] == ""


# --------------------------------------------------------------------------
# 3. Persistence: the tag reaches BOTH the column and the blob.
# --------------------------------------------------------------------------

def test_ineligible_posting_is_persisted_not_dropped(temp_db, monkeypatch):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    make_notify_node(send=False)({"new": [
        {"id": "screened", "title": "Software Engineering Intern", "company": "Acme",
         "location": "Austin, TX", "country": "US", "fit_score": 88,
         "fit_reason": "matched: python", "eligible": False,
         "eligible_reason": "requires PhD"},
    ]})

    rec = jobstore.load_records()["screened"]
    assert rec["eligible"] is False, "the judgement must survive into the store"
    assert rec["eligible_reason"] == "requires PhD"
    assert rec["fit_score"] == 88, "a screened-out row keeps its score, for later review"


def test_eligibility_lands_in_both_the_column_and_the_blob(temp_db, monkeypatch):
    """The dual-write rule: mirrored columns and the `data` blob must agree."""
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    make_notify_node(send=False)({"new": [
        {"id": "no", "title": "PhD Research Intern", "company": "Acme", "country": "US",
         "fit_score": 50, "fit_reason": "matched: none",
         "eligible": False, "eligible_reason": "requires PhD"},
        {"id": "yes", "title": "SWE Intern", "company": "Zeta", "country": "CA",
         "fit_score": 70, "fit_reason": "matched: sql", "eligible": True},
    ]})

    with store_db.connect() as conn:
        rows = {r["id"]: r for r in conn.execute(
            "SELECT id, eligible, eligible_reason, data FROM jobs").fetchall()}

    assert rows["no"]["eligible"] == 0, "mirrored column uses the repo's 0/1 convention"
    assert rows["no"]["eligible_reason"] == "requires PhD"
    assert json.loads(rows["no"]["data"])["eligible"] is False, "blob must agree"
    assert json.loads(rows["no"]["data"])["eligible_reason"] == "requires PhD"

    assert rows["yes"]["eligible"] == 1
    assert rows["yes"]["eligible_reason"] == ""


def test_a_record_with_no_eligible_key_is_stored_as_eligible(temp_db):
    """Default-open. A hand-built or migrated row must never be hidden by omission."""
    jobstore.replace_record({"id": "legacy", "company": "Acme", "title": "SWE Intern"})
    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT eligible, eligible_reason FROM jobs WHERE id = 'legacy'").fetchone()
    assert row["eligible"] == 1
    assert row["eligible_reason"] == ""


def test_eligibility_survives_a_refresh_descriptions_rewrite(temp_db):
    """`refresh_descriptions` rewrites the whole row through `_write`."""
    jobstore.replace_record({
        "id": "x", "company": "Acme", "title": "SWE Intern", "status": "new",
        "description": "short", "eligible": False, "eligible_reason": "requires PhD",
    })
    jobstore.refresh_descriptions([{"id": "x", "description": "much longer " * 50}])

    rec = jobstore.load_records()["x"]
    assert rec["eligible"] is False, "an unrelated write must not silently un-hide a row"
    assert rec["eligible_reason"] == "requires PhD"
    with store_db.connect() as conn:
        assert conn.execute("SELECT eligible FROM jobs WHERE id='x'").fetchone()[0] == 0


# --------------------------------------------------------------------------
# 4. The digest withholds it, and says how many.
# --------------------------------------------------------------------------

def test_digest_withholds_ineligible_rows_and_logs_the_count(
    temp_db, monkeypatch, capsys
):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    out = make_notify_node(send=False)({"new": [
        {"id": "no1", "title": "PhD Research Intern", "company": "GradOnly",
         "country": "US", "fit_score": 95, "fit_reason": "matched: python",
         "eligible": False, "eligible_reason": "requires PhD"},
        {"id": "no2", "title": "Senior Intern", "company": "AlsoGradOnly",
         "country": "US", "fit_score": 90, "fit_reason": "matched: python",
         "eligible": False, "eligible_reason": "5+ years required"},
        {"id": "yes", "title": "SWE Intern", "company": "Eligible",
         "country": "US", "fit_score": 60, "fit_reason": "matched: sql"},
    ]})

    assert "GradOnly" not in out["message"], "a screened-out role must not be announced"
    assert "AlsoGradOnly" not in out["message"]
    assert "Eligible" in out["message"], "an eligible role is still announced"

    logged = capsys.readouterr().out
    assert "withheld 2 posting(s) from the digest as not undergrad-eligible" in logged, \
        "a bounded coverage change must never land silently"

    # ...and all three are in the store regardless of the digest.
    assert set(jobstore.load_records()) == {"no1", "no2", "yes"}


def test_digest_says_nothing_when_nothing_was_withheld(temp_db, monkeypatch, capsys):
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    make_notify_node(send=False)({"new": [
        {"id": "yes", "title": "SWE Intern", "company": "Acme", "country": "US",
         "fit_score": 60, "fit_reason": "matched: sql"},
    ]})
    assert "withheld" not in capsys.readouterr().out, \
        "a zero count must not produce a log line (same rule as the sweep counts)"


def test_a_row_with_no_eligible_key_is_still_announced(temp_db, monkeypatch):
    """Default-open in the digest too."""
    monkeypatch.setattr(config, "JOB_COUNTRIES", ["US", "CA"])
    out = make_notify_node(send=False)({"new": [
        {"id": "a", "title": "SWE Intern", "company": "Acme", "country": "US",
         "fit_score": 60, "fit_reason": "matched: sql"},  # no "eligible" key at all
    ]})
    assert "Acme" in out["message"]


# --------------------------------------------------------------------------
# 5. Schema / migration.
# --------------------------------------------------------------------------

def test_schema_defaults_both_columns_to_visible(temp_db):
    """DEFAULT 1 is the safety property: adding these columns hides nothing."""
    with store_db.connect() as conn:
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(jobs)")}
    assert cols["eligible"]["dflt_value"] == "1"
    assert cols["eligible"]["notnull"] == 1
    assert cols["eligible_reason"]["notnull"] == 1


def test_migration_adds_the_columns_to_a_preexisting_jobs_table(tmp_path, monkeypatch):
    """`_migrate` runs BEFORE executescript, and must be idempotent.

    Simulates the real live DB: a `jobs` table that predates these columns, with
    a row in it. The row must survive and must come out VISIBLE.
    """
    db = tmp_path / "old.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    # The live table as it stood BEFORE these columns: every other column present
    # (schema.sql indexes `company` and `country`, so a minimal stub would fail
    # for a reason that has nothing to do with this migration).
    with store_db.connect() as conn:
        conn.execute(
            "CREATE TABLE jobs ("
            "id TEXT NOT NULL PRIMARY KEY, company TEXT NOT NULL DEFAULT '', "
            "title TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', "
            "url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', "
            "ats TEXT NOT NULL DEFAULT '', posted_at TEXT, remote INTEGER, "
            "compensation TEXT, department TEXT, description TEXT, fit_score REAL, "
            "fit_reason TEXT, ghost INTEGER NOT NULL DEFAULT 0, "
            "ghost_reason TEXT NOT NULL DEFAULT '', also_on TEXT NOT NULL DEFAULT '[]', "
            "country TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL DEFAULT '', "
            "last_seen TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute("INSERT INTO jobs (id, data) VALUES ('old', '{\"id\":\"old\"}')")

    store_db.init_db()
    store_db.init_db()  # idempotent

    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT eligible, eligible_reason FROM jobs WHERE id = 'old'").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert row["eligible"] == 1, "a pre-existing row must never be hidden by the migration"
    assert row["eligible_reason"] == ""
