"""last_seen accuracy + delisting detection.

The audit found 393 rows with a frozen last_seen: `dedupe` drops already-seen
postings before `notify` persists, so a posting that is STILL listed never gets
re-stamped. Delisting is then detected directly rather than inferred from age —
if a board was fetched successfully and a stored posting was not in the
response, it is gone.
"""

from __future__ import annotations

import datetime as dt

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
    import json
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


def test_fetch_reports_observed_ids_and_healthy_sources(monkeypatch):
    """A company counts as fetched_ok only if EVERY one of its sources succeeded."""
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
    assert out["fetched_ok"] == {"Acme"}, "Beta had a failing source, so it is not trustworthy"
    assert len(out["warnings"]) == 1


def test_delisting_flags_only_unobserved_rows_from_healthy_boards(temp_db):
    rows = [
        # still on the board -> not delisted
        {"id": "Acme:greenhouse:1", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board read fine, posting absent -> DELISTED
        {"id": "Acme:greenhouse:2", "company": "Acme", "posted_at": "2026-07-01", "_rescored": True},
        # board failed this run -> must NOT be called delisted
        {"id": "Beta:lever:9", "company": "Beta", "posted_at": "2026-07-01", "_rescored": True},
    ]
    out = freshness_node({
        "new": rows,
        "observed_ids": {"Acme:greenhouse:1"},
        "fetched_ok": {"Acme"},
    })["new"]
    by = {p["id"]: p for p in out}

    assert by["Acme:greenhouse:1"]["ghost"] is False
    assert by["Acme:greenhouse:2"]["ghost"] is True
    assert "delisted" in by["Acme:greenhouse:2"]["ghost_reason"]
    assert by["Beta:lever:9"]["ghost"] is False, "a failed fetch must never imply delisting"


def test_delisting_never_applies_to_freshly_scraped_postings(temp_db):
    """A brand-new posting is by definition observed; it must never be flagged."""
    out = freshness_node({
        "new": [{"id": "Acme:greenhouse:3", "company": "Acme", "posted_at": "2026-07-20"}],
        "observed_ids": {"Acme:greenhouse:3"},
        "fetched_ok": {"Acme"},
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


def test_fetch_multi_board_company_untrustworthy_when_one_board_is_empty(temp_db, monkeypatch):
    """Fix-loop round 2: a company on TWO boards where one silently returns
    [] and the other returns postings must NOT land in fetched_ok — the old
    code tracked "returned postings" only as an addition to `ok`, so the
    productive board's `ok.add(company)` papered over the empty board ever
    having been untrustworthy. Probed pre-fix: Duo/greenhouse -> [] plus
    Duo/lever -> 1 posting yielded fetched_ok={'Duo'}, and a stored
    Duo:greenhouse:7 row got wrongly flagged delisted. Not reachable with the
    current one-source-per-company config, but reachable the moment a second
    board is added for an existing company via Settings."""
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

    assert out["fetched_ok"] == set(), \
        "one empty board must make the whole company untrustworthy, even with a productive second board"
    assert out["observed_ids"] == {"Duo:lever:1"}

    # A stored row from the empty board must never be flagged delisted, since
    # Duo never actually earned fetched_ok this run.
    jobstore.replace_record({
        "id": "Duo:greenhouse:7", "company": "Duo", "posted_at": "2026-07-01",
        "status": "new",
    })
    fresh_out = freshness_node({
        "new": [{"id": "Duo:greenhouse:7", "company": "Duo", "posted_at": "2026-07-01", "_rescored": True}],
        "observed_ids": out["observed_ids"],
        "fetched_ok": out["fetched_ok"],
    })["new"]
    assert fresh_out[0]["ghost"] is False

    swept = jobstore.sweep_delisted(out["observed_ids"], out["fetched_ok"])
    assert swept == 0
    assert jobstore.load_records()["Duo:greenhouse:7"].get("ghost") is not True


def test_sweep_flags_a_fully_processed_row_never_reinjected_by_backfill(temp_db):
    """The gap the fix-loop review found: freshness_node only ever sees rows
    that pass through the pipeline this run (fresh finds, or rows backfill
    re-injects because they still lack a country/score/refinement). A row
    that is fully processed (country set, real score, non-baseline reason) is
    never re-selected by backfill, so it can never reach freshness_node again
    — and those are exactly the high-fit rows someone would apply to.
    sweep_delisted must flag such a row directly from the store."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "status": "new",
        "country": "US", "fit_score": 91, "fit_reason": "matched: python, react",
    })
    # observed_ids is non-empty (some OTHER posting was seen this run) but
    # does not include this id — an empty observed_ids is deliberately a
    # global no-op guard (second layer of protection), tested separately.
    n = jobstore.sweep_delisted(observed_ids={"Acme:greenhouse:99"}, fetched_ok={"Acme"})
    assert n == 1
    rec = jobstore.load_records()["Acme:greenhouse:1"]
    assert rec["ghost"] is True
    assert "delisted" in rec["ghost_reason"]


def test_sweep_never_relabels_an_applied_row(temp_db):
    """A posting closing after you've already applied is normal, not a signal
    to relabel it — only new/viewed rows are eligible for the sweep."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:2", "company": "Acme", "status": "applied",
        "country": "US", "fit_score": 91, "fit_reason": "matched: python, react",
    })
    n = jobstore.sweep_delisted(observed_ids={"Acme:greenhouse:99"}, fetched_ok={"Acme"})
    assert n == 0
    rec = jobstore.load_records()["Acme:greenhouse:2"]
    assert rec.get("ghost") is not True
    assert rec["status"] == "applied"


def test_sweep_ignores_a_company_whose_board_returned_zero_this_run(temp_db):
    """Companion to the fetch-node guard: even given a broad observed_ids set
    from elsewhere, a company simply absent from fetched_ok (because its
    board returned nothing, or never got queried) must never have its stored
    rows swept."""
    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "status": "new",
        "country": "US", "fit_score": 91, "fit_reason": "matched: python, react",
    })
    n = jobstore.sweep_delisted(observed_ids={"unrelated"}, fetched_ok=set())
    assert n == 0
    assert jobstore.load_records()["Acme:greenhouse:1"].get("ghost") is not True


def test_sweep_is_a_no_op_with_empty_inputs(temp_db):
    jobstore.replace_record({"id": "a", "company": "Acme", "status": "new"})
    assert jobstore.sweep_delisted(set(), set()) == 0
    assert jobstore.sweep_delisted({"a"}, set()) == 0
    assert jobstore.sweep_delisted(set(), {"Acme"}) == 0


def test_notify_logs_the_actual_swept_count(temp_db, capsys):
    """The plan requires bounded/destructive coverage to be logged — the
    count printed by notify must reflect exactly how many rows the sweep
    flagged, not a guess."""
    from agents.job_scraper.nodes.notify import make_notify_node

    jobstore.replace_record({
        "id": "Acme:greenhouse:1", "company": "Acme", "status": "new",
        "country": "US", "fit_score": 91, "fit_reason": "matched: python, react",
    })
    jobstore.replace_record({
        "id": "Acme:greenhouse:2", "company": "Acme", "status": "viewed",
        "country": "US", "fit_score": 80, "fit_reason": "matched: sql",
    })
    # A third, applied row must not be counted.
    jobstore.replace_record({
        "id": "Acme:greenhouse:3", "company": "Acme", "status": "applied",
        "country": "US", "fit_score": 70, "fit_reason": "matched: sql",
    })

    notify = make_notify_node(send=False)
    notify({"new": [], "observed_ids": {"unrelated"}, "fetched_ok": {"Acme"}})
    out = capsys.readouterr().out
    assert "swept 2 stored posting(s) as delisted" in out


def test_freshness_never_flags_a_row_with_blank_id_or_company():
    """A missing/blank id or company must fall to the SAFE side (not flagged),
    never the unsafe side."""
    out = freshness_node({
        "new": [
            {"id": "", "company": "Acme", "posted_at": "2026-07-01"},
            {"id": "x", "company": "", "posted_at": "2026-07-01"},
        ],
        "observed_ids": set(),
        "fetched_ok": {"Acme", ""},
    })["new"]
    assert all(p["ghost"] is False for p in out)
