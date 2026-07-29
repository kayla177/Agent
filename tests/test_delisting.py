"""last_seen accuracy + ghost (delisted / stale) detection, in BOTH directions.

The audit found 393 rows with a frozen last_seen: `dedupe` drops already-seen
postings before `notify` persists, so a posting that is STILL listed never gets
re-stamped. Delisting is then detected directly rather than inferred from age —
if a board was fetched completely and successfully and a stored posting was not
in the response, it is gone.

Two properties matter just as much as the flagging itself and are covered here:

* `ghost` is RE-DERIVED, never written once. Nothing used to be able to set it
  back to False for a converged row, so a single false-positive delisting dimmed
  a real opportunity permanently, and a row that aged past JOB_MAX_AGE_DAYS was
  never flagged at all.
* Board trust is per `(company, ats)` and is withheld from any source that
  raised, came back empty, or came back exactly at its own page cap (Workday and
  SmartRecruiters request one page and never paginate). Without the last of
  those, every stored posting past page 1 looks absent from a "healthy" board.
"""

from __future__ import annotations

import datetime as dt
import json

import config
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


def test_set_status_never_bumps_last_seen(temp_db):
    """`set_status` is the writer behind dismiss / apply / viewed-on-expand /
    Restore. `last_seen` means "observed in a live scrape" and is the whole basis
    of delisting detection, so an ordinary UI click must not be able to forge it.

    Live proof of the contradiction this removes: Cloudflare:greenhouse:8044395
    held last_seen = 2026-07-28 AND ghost = 1 with reason "delisted (not on
    Cloudflare's board)" — claiming it was seen on a board in the same run that
    concluded it was absent.
    """
    stale = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    jobstore.replace_record({
        "id": "a", "company": "Acme", "ats": "greenhouse", "title": "SWE Intern",
        "status": "new", "first_seen": stale, "last_seen": stale,
    })
    for status in ("viewed", "applied", "dismissed", "new"):
        jobstore.set_status("a", status)
        with jobstore.store_db.connect() as conn:
            row = conn.execute("SELECT status, last_seen, data FROM jobs WHERE id = 'a'").fetchone()
        assert row["status"] == status
        assert row["last_seen"] == stale, f"{status}: column must not move"
        assert json.loads(row["data"])["last_seen"] == stale, f"{status}: blob must not move"


def test_ghost_reason_is_mirrored_into_its_own_column(temp_db):
    """ghost_reason used to live ONLY in the `data` blob, so it was not in the
    Prisma model, not in JOB_SELECT and not renderable — every ghost showed as
    "stale" no matter why it was flagged."""
    jobstore.replace_record({
        "id": "a", "company": "Acme", "ats": "greenhouse", "status": "new",
        "ghost": True, "ghost_reason": "delisted (not on Acme's board)",
    })
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT ghost, ghost_reason FROM jobs WHERE id = 'a'").fetchone()
    assert row["ghost"] == 1
    assert row["ghost_reason"] == "delisted (not on Acme's board)"

    # A record with no reason at all must mirror as '' (the column is NOT NULL).
    jobstore.replace_record({"id": "b", "company": "Acme", "status": "new"})
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT ghost_reason FROM jobs WHERE id = 'b'").fetchone()
    assert row["ghost_reason"] == ""


def test_fetch_reports_observed_ids_and_healthy_boards(monkeypatch):
    """Trust is per `(company, ats)` BOARD, not per company.

    Every way of losing trust (an exception, an empty response, a truncated
    page) is a property of ONE board, so a failing Lever board must not blind
    the sweep to a healthy Greenhouse board for the same company — while a
    stored row from the failing board is still protected, because its own pair
    never enters `fetched_ok`.
    """
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
    assert out["fetched_ok"] == {("Acme", "greenhouse"), ("Beta", "greenhouse")}
    assert ("Beta", "lever") not in out["fetched_ok"], "a raising board is never trusted"
    assert len(out["warnings"]) == 1


def test_fetch_capped_result_is_not_trusted(monkeypatch):
    """`fetch_workday` asks for one page of 20 and NEVER paginates; likewise
    `fetch_smartrecruiters` at 100. A result exactly at the cap is therefore
    probably just page 1, so every stored posting past it looks absent from a
    "healthy" board — mass false delisting. Such a source must be treated
    exactly like a failure for delisting purposes (and still contribute its
    postings, which are real)."""
    from agents.job_scraper.ats import PAGE_CAPS
    from agents.job_scraper.nodes import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Big", "ats": "workday", "token": "big/wd5/Careers"},
        {"company": "Small", "ats": "workday", "token": "small/wd5/Careers"},
    ])

    cap = PAGE_CAPS["workday"]

    def fake_fetch(source):
        n = cap if source["company"] == "Big" else cap - 1
        return [{"id": f"{source['company']}:workday:{i}", "company": source["company"]}
                for i in range(n)]

    monkeypatch.setattr(fetch_mod, "fetch_source", fake_fetch)
    out = fetch_node({})

    assert ("Big", "workday") not in out["fetched_ok"], \
        "a result exactly at the page cap may be truncated and must not be trusted"
    assert ("Small", "workday") in out["fetched_ok"], \
        "a short page IS the whole board and stays trustworthy"
    # The postings themselves are still collected — this guard only withholds
    # delisting trust, it never discards data.
    assert len(out["observed_ids"]) == cap + (cap - 1)
    assert any("may be truncated" in w for w in out["warnings"])


def test_fetch_untruncated_ats_has_no_cap(monkeypatch):
    """Only the two non-paginating adapters carry a cap. A greenhouse board
    returning exactly 20 postings is complete and must stay trusted."""
    from agents.job_scraper.ats import PAGE_CAPS
    from agents.job_scraper.nodes import fetch as fetch_mod

    assert set(PAGE_CAPS) == {"workday", "smartrecruiters"}
    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Acme", "ats": "greenhouse", "token": "acme"},
    ])
    monkeypatch.setattr(fetch_mod, "fetch_source", lambda s: [
        {"id": f"Acme:greenhouse:{i}", "company": "Acme"} for i in range(20)
    ])
    assert fetch_node({})["fetched_ok"] == {("Acme", "greenhouse")}


def test_delisting_flags_only_unobserved_rows_from_healthy_boards(temp_db):
    rows = [
        # still on the board -> not delisted
        {"id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
         "posted_at": "2026-07-01", "_rescored": True},
        # board read fine, posting absent -> DELISTED
        {"id": "Acme:greenhouse:2", "company": "Acme", "ats": "greenhouse",
         "posted_at": "2026-07-01", "_rescored": True},
        # board failed this run -> must NOT be called delisted
        {"id": "Beta:lever:9", "company": "Beta", "ats": "lever",
         "posted_at": "2026-07-01", "_rescored": True},
        # SAME company as a healthy board, but a different (untrusted) board.
        {"id": "Acme:lever:7", "company": "Acme", "ats": "lever",
         "posted_at": "2026-07-01", "_rescored": True},
    ]
    out = freshness_node({
        "new": rows,
        "observed_ids": {"Acme:greenhouse:1"},
        "fetched_ok": {("Acme", "greenhouse")},
    })["new"]
    by = {p["id"]: p for p in out}

    assert by["Acme:greenhouse:1"]["ghost"] is False
    assert by["Acme:greenhouse:2"]["ghost"] is True
    assert "delisted" in by["Acme:greenhouse:2"]["ghost_reason"]
    assert by["Beta:lever:9"]["ghost"] is False, "a failed fetch must never imply delisting"
    assert by["Acme:lever:7"]["ghost"] is False, \
        "a healthy greenhouse board says nothing about Acme's lever board"


def test_delisting_never_applies_to_freshly_scraped_postings(temp_db):
    """A brand-new posting is by definition observed; it must never be flagged."""
    out = freshness_node({
        "new": [{"id": "Acme:greenhouse:3", "company": "Acme", "ats": "greenhouse",
                 "posted_at": "2026-07-20"}],
        "observed_ids": {"Acme:greenhouse:3"},
        "fetched_ok": {("Acme", "greenhouse")},
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


# --- Fix-loop round: a board returning [] without raising must not look
# healthy, and a fully-processed row must still be reachable for delisting. ---


def test_fetch_empty_result_does_not_mark_company_trustworthy(monkeypatch):
    """An ATS provider schema change (or a moved board) can make an adapter
    return [] without raising at all — e.g. Greenhouse's adapter is
    `resp.json().get("jobs", [])`. If that counted as "healthy", every stored
    posting for the company would look absent from a "healthy" board and get
    mass-flagged delisted in one run."""
    from agents.job_scraper.nodes import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Acme", "ats": "greenhouse", "token": "acme"},
    ])
    monkeypatch.setattr(fetch_mod, "fetch_source", lambda source: [])  # no raise, just empty

    out = fetch_node({})
    assert out["fetched_ok"] == set(), "an empty, non-raising result must not count as healthy"
    assert out["observed_ids"] == set()
    assert out["warnings"] == [], "an empty result is not itself an error"


def test_fetch_one_empty_board_does_not_taint_the_other(temp_db, monkeypatch):
    """A company on TWO boards where one silently returns [] (schema drift, a
    moved token) and the other returns postings: the EMPTY board must never be
    trusted, because every stored posting of that board would look absent from
    a "healthy" board and get mass-flagged delisted in one run. Trust is keyed
    per `(company, ats)`, so the productive board can still be trusted without
    ever putting the empty board's rows at risk — which is the property that
    actually matters (an earlier per-company version got this wrong in the
    opposite direction: `ok.add(company)` from the productive board papered
    over the empty one)."""
    from agents.job_scraper.nodes import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_sources", lambda: [
        {"company": "Duo", "ats": "greenhouse", "token": "duo-gh"},
        {"company": "Duo", "ats": "lever", "token": "duo-lever"},
    ])

    def fake_fetch(source):
        if source["ats"] == "greenhouse":
            return []  # schema drift / empty board: succeeds, returns nothing
        return [{"id": "Duo:lever:1", "company": "Duo"}]

    monkeypatch.setattr(fetch_mod, "fetch_source", fake_fetch)
    out = fetch_node({})

    assert ("Duo", "greenhouse") not in out["fetched_ok"], \
        "an empty, non-raising board must never count as healthy"
    assert out["fetched_ok"] == {("Duo", "lever")}
    assert out["observed_ids"] == {"Duo:lever:1"}

    # A stored row from the empty board must never be flagged delisted.
    jobstore.replace_record({
        "id": "Duo:greenhouse:7", "company": "Duo", "ats": "greenhouse",
        "posted_at": _yesterday(), "status": "new",
    })
    fresh_out = freshness_node({
        "new": [{"id": "Duo:greenhouse:7", "company": "Duo", "ats": "greenhouse",
                 "posted_at": _yesterday(), "_rescored": True}],
        "observed_ids": out["observed_ids"],
        "fetched_ok": out["fetched_ok"],
    })["new"]
    assert fresh_out[0]["ghost"] is False

    counts = jobstore.sweep_ghosts(out["observed_ids"], out["fetched_ok"])
    assert counts["delisted"] == 0
    assert jobstore.load_records()["Duo:greenhouse:7"].get("ghost") is not True


def test_sweep_flags_a_fully_processed_row_never_reinjected_by_backfill(temp_db):
    """The gap the fix-loop review found: freshness_node only ever sees rows
    that pass through the pipeline this run (fresh finds, or rows backfill
    re-injects because they still lack a country/score/refinement). A row
    that is fully processed (country set, real score, non-baseline reason) is
    never re-selected by backfill, so it can never reach freshness_node again
    — and those are exactly the high-fit rows someone would apply to.
    sweep_ghosts must flag such a row directly from the store."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "country": "US", "fit_score": 91,
        "fit_reason": "matched: python, react",
    })
    # observed_ids is non-empty (some OTHER posting was seen this run) but
    # does not include this id — an empty observed_ids is deliberately a
    # global no-op guard (second layer of protection), tested separately.
    counts = jobstore.sweep_ghosts(
        observed_ids={"Acme:greenhouse:99"}, fetched_ok={("Acme", "greenhouse")}
    )
    assert counts["delisted"] == 1
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is True
    assert "delisted" in rec["ghost_reason"]
    # Mirrored column too, not just the blob — the UI reads the column.
    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT ghost, ghost_reason FROM jobs WHERE id = 'Acme:greenhouse:1'"
        ).fetchone()
    assert row["ghost"] == 1
    assert row["ghost_reason"].startswith("delisted (")


def test_sweep_clears_a_delisting_flag_when_the_board_shows_it_again(temp_db):
    """THE write-once bug: nothing in the codebase could ever set ghost back to
    False for a converged row. `freshness_node` is the only other writer of
    ghost=False and it only sees rows backfill re-injects, which a converged row
    never is — so one false-positive delisting dimmed a real opportunity with a
    warning badge FOREVER. Seeing the posting on its own healthy board again is
    direct evidence to the contrary and must undo the flag."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "country": "US", "fit_score": 91,
        "fit_reason": "matched: python, react", "posted_at": _yesterday(),
        "ghost": True, "ghost_reason": "delisted (not on Acme's board)",
    })
    counts = jobstore.sweep_ghosts(
        observed_ids={"Acme:greenhouse:1"}, fetched_ok={("Acme", "greenhouse")}
    )
    assert counts["relisted"] == 1
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is False
    assert rec["ghost_reason"] == ""
    with jobstore.store_db.connect() as conn:
        row = conn.execute(
            "SELECT ghost, ghost_reason FROM jobs WHERE id = 'Acme:greenhouse:1'"
        ).fetchone()
    assert row["ghost"] == 0
    assert row["ghost_reason"] == ""


def test_sweep_clear_pass_only_undoes_delisting_flags(temp_db, monkeypatch):
    """The clear pass must ONLY undo a `delisted (...)` call. A `stale`,
    `deadline passed` or `delisted by source` flag came from a different rule
    and being visible on a board says nothing about it — clearing those would
    silently unflag genuinely dead postings."""
    monkeypatch.setattr(config, "JOB_MAX_AGE_DAYS", 60)
    old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    jobstore.replace_record({
        "id": "Acme:greenhouse:stale", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": old,
        "ghost": True, "ghost_reason": "stale (400d old)",
    })
    jobstore.replace_record({
        "id": "Acme:greenhouse:unlisted", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(), "listed": False,
        "ghost": True, "ghost_reason": "delisted by source",
    })
    jobstore.replace_record({
        "id": "Acme:greenhouse:deadline", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(), "deadline": "2020-01-01",
        "ghost": True, "ghost_reason": "deadline passed (2020-01-01)",
    })
    observed = {"Acme:greenhouse:stale", "Acme:greenhouse:unlisted", "Acme:greenhouse:deadline"}
    counts = jobstore.sweep_ghosts(observed, {("Acme", "greenhouse")})

    assert counts["relisted"] == 0, "no delisting call existed to undo"
    recs = jobstore.load_records()
    for pid in observed:
        assert recs[pid]["ghost"] is True, pid
    assert recs["Acme:greenhouse:unlisted"]["ghost_reason"] == "delisted by source"
    assert recs["Acme:greenhouse:deadline"]["ghost_reason"] == "deadline passed (2020-01-01)"


def test_sweep_flags_a_converged_row_that_aged_into_staleness(temp_db, monkeypatch):
    """The other half of the write-once bug: the plan claimed `ghost` gets
    recomputed as rows age past JOB_MAX_AGE_DAYS, but backfill never re-selects
    a converged row so nothing recomputed it. That reproduced the audited
    symptom of a row reading "🕒 90d ago" with NO stale badge, because JobRow
    computes age client-side while ghost was frozen in the DB."""
    monkeypatch.setattr(config, "JOB_MAX_AGE_DAYS", 60)
    posted = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "country": "US", "fit_score": 91,
        "fit_reason": "matched: python, react", "posted_at": posted,
        "ghost": False, "ghost_reason": "",
    })
    # No board evidence at all this run — staleness needs none.
    counts = jobstore.sweep_ghosts(set(), set())
    assert counts["stale"] == 1
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is True
    assert rec["ghost_reason"] == "stale (90d old)"


def test_sweep_clears_a_stale_flag_that_no_longer_holds(temp_db, monkeypatch):
    """Symmetry: raising JOB_MAX_AGE_DAYS (or a posting being re-dated by a
    fresh fetch) must un-flag a row, not leave a permanent warning badge."""
    monkeypatch.setattr(config, "JOB_MAX_AGE_DAYS", 365)
    posted = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": posted,
        "ghost": True, "ghost_reason": "stale (90d old)",
    })
    counts = jobstore.sweep_ghosts(set(), set())
    assert counts["unstale"] == 1
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is False
    assert rec["ghost_reason"] == ""


def test_sweep_never_overrules_a_delisting_without_board_evidence(temp_db, monkeypatch):
    """With no trusted board this run, a delisting call must be left exactly as
    it is — neither confirmed nor cleared, and never overwritten by the weaker
    age heuristic."""
    monkeypatch.setattr(config, "JOB_MAX_AGE_DAYS", 60)
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(),
        "ghost": True, "ghost_reason": "delisted (not on Acme's board)",
    })
    counts = jobstore.sweep_ghosts(set(), set())
    assert counts == {"delisted": 0, "relisted": 0, "stale": 0, "unstale": 0}
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is True
    assert rec["ghost_reason"] == "delisted (not on Acme's board)"


def test_sweep_is_idempotent(temp_db):
    """A second identical sweep must report zero changes — the counts are
    transitions, not "rows matching a condition", so a stable board never
    produces a stream of log lines about nothing happening."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(),
    })
    first = jobstore.sweep_ghosts({"Acme:greenhouse:99"}, {("Acme", "greenhouse")})
    assert first["delisted"] == 1
    second = jobstore.sweep_ghosts({"Acme:greenhouse:99"}, {("Acme", "greenhouse")})
    assert second == {"delisted": 0, "relisted": 0, "stale": 0, "unstale": 0}


def test_sweep_never_relabels_an_applied_row(temp_db):
    """A posting closing after you've already applied is normal, not a signal
    to relabel it — only new/viewed rows are eligible for the sweep."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:2", "company": "Acme", "ats": "greenhouse",
        "status": "applied", "country": "US", "fit_score": 91,
        "fit_reason": "matched: python, react",
    })
    counts = jobstore.sweep_ghosts(
        observed_ids={"Acme:greenhouse:99"}, fetched_ok={("Acme", "greenhouse")}
    )
    assert counts["delisted"] == 0
    rec = jobstore.load_records()["Acme:greenhouse:2"]
    assert rec.get("ghost") is not True
    assert rec["status"] == "applied"


def test_sweep_requires_a_trusted_board_before_flagging(temp_db):
    """Differential test on the trust guard, in BOTH directions: the SAME stored
    row and the SAME observed_ids must be flagged only when its own
    `(company, ats)` is trusted. Deleting the trust check flags it in the first
    call too — which is the mass-false-delisting failure mode."""
    row = {
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(),
    }
    jobstore.replace_record(dict(row))
    assert jobstore.sweep_ghosts({"unrelated"}, set())["delisted"] == 0, \
        "no trusted board -> nothing may be flagged"
    assert jobstore.sweep_ghosts({"unrelated"}, {("Acme", "lever")})["delisted"] == 0, \
        "a different board of the same company is not evidence"
    assert jobstore.sweep_ghosts({"unrelated"}, {("Beta", "greenhouse")})["delisted"] == 0, \
        "a different company on the same ATS is not evidence"
    assert jobstore.sweep_ghosts({"unrelated"}, {("Acme", "greenhouse")})["delisted"] == 1, \
        "its own board, read cleanly, without this id -> delisted"


def test_sweep_never_flags_when_nothing_was_observed(temp_db):
    """The `not observed_ids` half of the guard, made to matter: a trusted board
    plus an EMPTY observed_ids would otherwise conclude that every one of its
    postings vanished. Removing the guard flags this row."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(),
    })
    counts = jobstore.sweep_ghosts(set(), {("Acme", "greenhouse")})
    assert counts["delisted"] == 0
    assert jobstore.load_records()["Acme:greenhouse:1"].get("ghost") is not True


def test_sweep_ignores_rows_with_a_blank_company_or_ats(temp_db):
    """A row that cannot be matched back to a board must fall to the safe side."""
    jobstore.replace_record({
        "id": "noats", "company": "Acme", "ats": "", "status": "new",
        "posted_at": _yesterday(),
    })
    jobstore.replace_record({
        "id": "nocompany", "company": "", "ats": "greenhouse", "status": "new",
        "posted_at": _yesterday(),
    })
    counts = jobstore.sweep_ghosts(
        {"unrelated"}, {("Acme", ""), ("", "greenhouse"), ("Acme", "greenhouse")}
    )
    assert counts["delisted"] == 0
    recs = jobstore.load_records()
    assert recs["noats"].get("ghost") is not True
    assert recs["nocompany"].get("ghost") is not True


def test_sweep_is_a_no_op_with_nothing_to_change(temp_db):
    jobstore.replace_record({
        "id": "a", "company": "Acme", "ats": "greenhouse", "status": "new",
        "posted_at": _yesterday(),
    })
    zero = {"delisted": 0, "relisted": 0, "stale": 0, "unstale": 0}
    assert jobstore.sweep_ghosts(set(), set()) == zero
    assert jobstore.sweep_ghosts({"a"}, set()) == zero
    assert jobstore.sweep_ghosts(set(), {("Acme", "greenhouse")}) == zero


def test_notify_logs_each_sweep_outcome_separately(temp_db, capsys, monkeypatch):
    """The plan requires bounded/destructive coverage to be logged. "flagged 3"
    and "cleared 3" are very different events, so a single net number would hide
    both — each outcome gets its own line with its own real count."""
    from agents.job_scraper.nodes.notify import make_notify_node

    monkeypatch.setattr(config, "JOB_MAX_AGE_DAYS", 60)
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
        "status": "new", "country": "US", "fit_score": 91,
        "fit_reason": "matched: python, react", "posted_at": _yesterday(),
    })
    jobstore.replace_record({
        "id": "Acme:greenhouse:2", "company": "Acme", "ats": "greenhouse",
        "status": "viewed", "country": "US", "fit_score": 80,
        "fit_reason": "matched: sql", "posted_at": _yesterday(),
    })
    # A third, applied row must not be counted.
    jobstore.replace_record({
        "id": "Acme:greenhouse:3", "company": "Acme", "ats": "greenhouse",
        "status": "applied", "country": "US", "fit_score": 70,
        "fit_reason": "matched: sql", "posted_at": _yesterday(),
    })
    # A fourth row that is visible on the board but wrongly flagged delisted.
    jobstore.replace_record({
        "id": "Acme:greenhouse:4", "company": "Acme", "ats": "greenhouse",
        "status": "new", "posted_at": _yesterday(),
        "ghost": True, "ghost_reason": "delisted (not on Acme's board)",
    })

    notify = make_notify_node(send=False)
    notify({
        "new": [],
        "observed_ids": {"Acme:greenhouse:4"},
        "fetched_ok": {("Acme", "greenhouse")},
    })
    out = capsys.readouterr().out
    assert "swept 2 stored posting(s) as delisted" in out
    assert "cleared the delisted flag on 1 posting(s)" in out


def test_freshness_never_flags_a_row_with_blank_id_company_or_ats():
    """A missing/blank id, company or ats must fall to the SAFE side (not
    flagged), never the unsafe side."""
    out = freshness_node({
        "new": [
            {"id": "", "company": "Acme", "ats": "greenhouse", "posted_at": _yesterday()},
            {"id": "x", "company": "", "ats": "greenhouse", "posted_at": _yesterday()},
            {"id": "y", "company": "Acme", "ats": "", "posted_at": _yesterday()},
        ],
        "observed_ids": set(),
        "fetched_ok": {("Acme", "greenhouse"), ("Acme", ""), ("", "greenhouse")},
    })["new"]
    assert all(p["ghost"] is False for p in out)
