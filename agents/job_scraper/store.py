"""SQLite record-store for job postings (dedupe memory + web view source).

Backed by the `jobs` table in data/control_center.db (see store_db). Each row
keeps a `data` JSON blob with the FULL enriched posting (so the scraper pipeline
round-trips every field it depends on) plus mirrored columns (status, company,
fit_score, ...) that let the web UI filter/sort in SQL. The public API matches
the previous JSON version. Reads degrade gracefully (missing table -> "nothing
seen yet"). `status` is one of new | viewed | applied | dismissed.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import store_db
from agents.job_scraper.matching import stale_reason

STATUSES = ("new", "viewed", "applied", "dismissed")

# Prefix of every ghost_reason written by the "absent from a healthy board"
# rule (freshness_node and the sweep below both produce it). The clear pass
# matches on this prefix so it can ONLY ever undo a delisting call — a
# `stale (...)`, `deadline passed (...)` or `delisted by source` flag comes from
# a different rule and must not be cleared by board evidence.
_DELISTED_PREFIX = "delisted ("


def _reason_kind(reason: str) -> str:
    """The RULE a `ghost_reason` came from, stripped of its parenthetical detail.

        "stale (61d old)"              -> "stale"
        "deadline passed (2020-01-01)" -> "deadline passed"
        "delisted (not on Acme's board)" -> "delisted"
        "delisted by source"           -> "delisted by source"  (no parenthetical)
        ""                             -> ""

    Why this exists: `matching.stale_reason` derives age LIVE from `posted_at`, so
    a stale row's reason text changes every single day — "stale (61d old)" ->
    "stale (62d old)". The refreshed string is still written (the stored reason
    must agree with the age the UI computes client-side), but a pure day-count
    refresh is not an EVENT. Counting it as one would make ~108 live rows report a
    transition on every run, and "flagged 108 stale" every morning would train the
    reader to ignore the only log line that reports real coverage changes. So
    `sweep_ghosts` writes on any text change but counts only when the `ghost`
    boolean flipped or the KIND changed.
    """
    return (reason or "").split(" (", 1)[0].strip()


def _today() -> str:
    return dt.date.today().isoformat()


def _mirror(rec: dict) -> dict:
    """Values for the mirrored (queryable) columns, derived from a full record."""
    remote = rec.get("remote")
    return {
        "company": rec.get("company", ""),
        "title": rec.get("title", ""),
        "location": rec.get("location", ""),
        "url": rec.get("url", ""),
        "status": rec.get("status", "new"),
        "ats": rec.get("ats", ""),
        "posted_at": rec.get("posted_at"),
        "remote": None if remote is None else (1 if remote else 0),
        "compensation": rec.get("compensation"),
        "department": rec.get("department"),
        "description": rec.get("description"),
        "fit_score": rec.get("fit_score"),
        "fit_reason": rec.get("fit_reason"),
        "ghost": 1 if rec.get("ghost") else 0,
        # Mirrored so the web UI can say WHY a row is flagged. Without it every
        # ghost renders identically as "stale", and a posting detected as
        # removed from its board — the whole point of the delisting sweep —
        # displays as "stale 3d".
        "ghost_reason": rec.get("ghost_reason") or "",
        "also_on": json.dumps(rec.get("also_on", [])),
        "country": rec.get("country", ""),
        # Undergrad-eligibility screen. Mirrored so the board can hide these
        # rows in SQL/JS the way it hides out-of-country ones, and so the reason
        # is displayable — an unexplained hidden row is indistinguishable from a
        # bug. `.get("eligible", True)` defaults to ELIGIBLE: a record that never
        # went through the screen (a migration, a hand-built row, a model
        # failure) must never be hidden by omission.
        "eligible": 1 if rec.get("eligible", True) else 0,
        "eligible_reason": rec.get("eligible_reason") or "",
        "first_seen": rec.get("first_seen", ""),
        "last_seen": rec.get("last_seen", ""),
    }


_INSERT = (
    "INSERT INTO jobs (id, company, title, location, url, status, ats, posted_at, "
    "remote, compensation, department, description, fit_score, fit_reason, ghost, "
    "ghost_reason, also_on, country, eligible, eligible_reason, first_seen, "
    "last_seen, data) "
    "VALUES (:id, :company, :title, :location, "
    ":url, :status, :ats, :posted_at, :remote, :compensation, :department, "
    ":description, :fit_score, :fit_reason, :ghost, :ghost_reason, :also_on, "
    ":country, :eligible, :eligible_reason, :first_seen, "
    ":last_seen, :data) ON CONFLICT(id) DO UPDATE SET "
    "company=excluded.company, title=excluded.title, location=excluded.location, "
    "url=excluded.url, status=excluded.status, ats=excluded.ats, "
    "posted_at=excluded.posted_at, remote=excluded.remote, "
    "compensation=excluded.compensation, department=excluded.department, "
    "description=excluded.description, fit_score=excluded.fit_score, "
    "fit_reason=excluded.fit_reason, ghost=excluded.ghost, "
    "ghost_reason=excluded.ghost_reason, also_on=excluded.also_on, "
    "country=excluded.country, eligible=excluded.eligible, "
    "eligible_reason=excluded.eligible_reason, "
    "first_seen=excluded.first_seen, last_seen=excluded.last_seen, data=excluded.data"
)


def _write(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        _INSERT,
        {"id": record["id"], **_mirror(record),
         "data": json.dumps(record, ensure_ascii=False)},
    )


def load_records() -> dict[str, dict]:
    """Return the full {id: record} map (empty if none / table missing)."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT id, data FROM jobs").fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        try:
            rec = json.loads(r["data"]) if r["data"] else {}
        except json.JSONDecodeError:
            rec = {}
        rec["id"] = r["id"]
        out[r["id"]] = rec
    return out


def upsert_records(postings: list[dict], *, status: str = "new") -> None:
    """Merge postings; new rows get `status`, existing keep status + first_seen."""
    if not postings:
        return
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        for p in postings:
            pid = p.get("id")
            if not pid:
                continue
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            existing = {}
            if row and row["data"]:
                try:
                    existing = json.loads(row["data"])
                except json.JSONDecodeError:
                    existing = {}
            record = {**existing, **p}
            record["id"] = pid
            record["first_seen"] = existing.get("first_seen") or today
            # last_seen means "observed in a live scrape". A genuinely fetched
            # posting never carries its own `last_seen` (fetch/filter/dedupe
            # never set it), so it defaults to today. A backfilled row DOES
            # carry its own `last_seen` (loaded straight from storage by
            # backfill_node) and must NOT be bumped here — it was reprocessed
            # for scoring/country, not re-observed by this scrape.
            record["last_seen"] = p.get("last_seen") or today
            record["status"] = existing.get("status") or status
            _write(conn, record)


def set_status(pid: str, status: str) -> dict | None:
    """Update one record's status; returns the record or None if absent.

    `last_seen` is deliberately NOT touched. It means "observed in a live
    scrape" — the sole basis for detecting a delisted posting — and this
    function is the writer behind dismiss / apply / viewed-on-expand / Restore,
    so stamping it here would let ordinary UI clicks forge an observation. Only
    `touch_last_seen` (fed by `observed_ids`) may write it.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    with store_db.connect() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row["data"]) if row["data"] else {}
        except json.JSONDecodeError:
            record = {}
        record["id"] = pid
        record["status"] = status
        _write(conn, record)
    return record


def replace_record(rec: dict) -> None:
    """Insert/replace one record verbatim (status + timestamps taken as-is).

    Unlike upsert_records (which forces new rows to 'new' and stamps first_seen
    today), this preserves the record's own status/first_seen — used by the
    JSON->SQLite migration so historical state survives the move.
    """
    pid = rec.get("id")
    if not pid:
        return
    store_db.init_db()
    with store_db.connect() as conn:
        _write(conn, {**rec, "id": pid})


def load_seen() -> set[str]:
    """Return the set of posting ids recorded on previous runs."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT id FROM jobs").fetchall()
        return {r["id"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def touch_last_seen(ids: list[str] | set[str]) -> int:
    """Stamp `last_seen` = today on postings observed in this scrape.

    Writes through `_write`, so the mirrored column and the `data` blob stay in
    sync (the dual-write rule). Ids absent from the table are skipped. Nothing
    else on the record is altered — notably `first_seen` and `status`.

    This is what makes `last_seen` mean "observed on a board", which is the
    prerequisite for detecting a delisted posting: `dedupe` removes already-seen
    postings before `notify` persists, so without this they would never be
    re-stamped.
    """
    ids = [i for i in ids if i]
    if not ids:
        return 0
    store_db.init_db()
    today = _today()
    touched = 0
    with store_db.connect() as conn:
        for pid in ids:
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            try:
                record = json.loads(row["data"]) if row["data"] else {}
            except json.JSONDecodeError:
                record = {}
            record["id"] = pid
            record["last_seen"] = today
            _write(conn, record)
            touched += 1
    return touched


def refresh_descriptions(postings: list[dict]) -> int:
    """Replace a stored row's `description` when this run fetched a LONGER one.

    Same shape as `touch_last_seen`, and it exists for the same structural
    reason: `dedupe` drops already-seen ids before `notify` persists, so a row
    written under the old 1200-char `ats._DESC_MAX` would keep its truncated
    description FOREVER even though every run re-downloads the full text. Fed
    `state["raw"] + state["filtered"]`, this repairs those rows from text this
    run already has in memory — no extra network calls, no second fetch pass.

    ONLY-WHEN-LONGER is deliberate, not an optimisation. A board briefly serving
    a stub (schema change, partial render, an adapter that stops returning
    `descriptionPlain`) must never be able to clobber good stored text. The
    comparison is on length because that is exactly the failure being repaired:
    a prefix of the real JD.

    Writes through `_write`, so the mirrored column and the `data` JSON blob
    stay in sync (the dual-write rule). Ids absent from the table are skipped —
    a genuinely new posting is `upsert_records`' job, not this one. Nothing but
    `description` is altered: the record written back is the STORED one with a
    single key replaced, so `status`, `first_seen`, `last_seen`, `country`,
    `fit_score` are untouched and no `_`-prefixed pipeline tag riding on the
    incoming posting can reach the store through here.

    Returns the number of rows updated so `notify` can log it.
    """
    # `raw` and `filtered` overlap (filtered is a subset of raw) and one role can
    # appear on two boards, so collapse to the longest text per id first — one
    # SELECT + at most one write per id instead of a write per duplicate.
    best: dict[str, str] = {}
    for p in postings or []:
        pid = p.get("id")
        desc = p.get("description") or ""
        if not pid or not desc:
            continue
        if len(desc) > len(best.get(pid, "")):
            best[pid] = desc
    if not best:
        return 0
    store_db.init_db()
    updated = 0
    with store_db.connect() as conn:
        for pid, desc in best.items():
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            try:
                record = json.loads(row["data"]) if row["data"] else {}
            except json.JSONDecodeError:
                record = {}
            if len(desc) <= len(record.get("description") or ""):
                continue
            record["id"] = pid
            record["description"] = desc
            _write(conn, record)
            updated += 1
    return updated


def sweep_ghosts(
    observed_ids: set[str], fetched_ok: set[tuple[str, str]]
) -> dict[str, int]:
    """Re-derive the `ghost` flag of every stored `new`/`viewed` row, BOTH ways.

    `freshness_node` only ever sees rows passing through the pipeline this run
    (freshly fetched, or re-injected by `backfill_node` because they still
    lack a country/score/refinement) — so a fully-processed row (country set,
    score set, already-refined reason) is never re-selected by backfill and
    can never be flagged OR un-flagged there, even though it is exactly the
    kind of high-fit row someone would actually apply to. Every ghost decision
    is therefore made here as well, from the store, so it is RECOMPUTED on
    every run instead of being written once and frozen.

    Three outcomes, in strict precedence order:

    * ``delisted`` — the row's own `(company, ats)` board was read completely
      and successfully this run (`fetched_ok`) and its id was not in the
      response (`observed_ids`). Direct observation, so it wins over any
      heuristic.
    * ``relisted`` — the same board WAS read and the id IS in the response,
      while the stored reason is a delisting call. The board contradicts the
      call, so undo it and fall back to the age/deadline rule. Only a reason
      starting with ``"delisted ("`` is ever cleared this way: a
      ``stale (...)``, ``deadline passed (...)`` or ``delisted by source``
      flag comes from a different rule and is none of this pass's business.
      Without this, one false positive stayed on the row forever — nothing in
      the codebase could clear `ghost` for a converged row.
    * ``stale`` / ``unstale`` — no trusted board evidence either way, so
      re-derive `matching.stale_reason` (age past `JOB_MAX_AGE_DAYS`, a passed
      deadline, or the source's own unlisted flag) and write it if it changed.
      This is what makes a row that has *aged into* staleness get flagged, and
      a row whose reason no longer holds get cleared. A row still carrying a
      delisting call is left alone here — with no board evidence there is
      nothing to overrule it with.

    Writing and COUNTING are deliberately separated. `stale_reason` derives age
    live from `posted_at`, so a stale row's reason text changes every day; the
    refreshed text is always written (the stored reason must agree with the age
    `JobRow` computes client-side) but is only counted when the `ghost` boolean
    flipped or the reason changed KIND (see `_reason_kind`). Otherwise every run
    would report a transition for every stale row and the log would stop meaning
    anything.

    Age/deadline re-derivation needs no fetch evidence at all, so it runs even
    when `observed_ids`/`fetched_ok` are empty (a run where every board failed).
    The delisted/relisted passes need a non-empty `observed_ids` — a run that
    observed nothing can never conclude that everything vanished — plus, per row,
    that row's own board in `fetched_ok`.

    Restricted to `new` / `viewed` rows. `applied` rows are deliberately
    excluded — a posting closing after you've already applied is normal, not
    a signal to relabel it. `dismissed` rows are irrelevant either way.

    Returns a count of TRANSITIONS per outcome so `notify_node` can log each
    separately: a bounded, destructive coverage change must never land silently.
    """
    store_db.init_db()
    counts = {"delisted": 0, "relisted": 0, "stale": 0, "unstale": 0}
    # A run that observed NOTHING cannot conclude that anything vanished, so the
    # delisted/relisted passes are skipped entirely. (Staleness is still
    # re-derivable — it needs no fetch evidence at all.)
    #
    # There is deliberately no separate `and fetched_ok` clause here: the
    # per-row `(company, ats) in fetched_ok` test below already makes an empty
    # `fetched_ok` flag nothing, so an extra check would be a guard no test could
    # ever falsify. Both live guards ARE mutation-tested — see
    # test_sweep_never_flags_when_nothing_was_observed and
    # test_sweep_requires_a_trusted_board_before_flagging.
    saw_something = bool(observed_ids)
    with store_db.connect() as conn:
        rows = conn.execute(
            "SELECT id, data FROM jobs WHERE status IN ('new', 'viewed')"
        ).fetchall()
        for row in rows:
            pid = row["id"]
            if not pid:
                continue
            try:
                record = json.loads(row["data"]) if row["data"] else {}
            except json.JSONDecodeError:
                record = {}
            record["id"] = pid
            company = record.get("company") or ""
            ats = record.get("ats") or ""
            was_ghost = bool(record.get("ghost"))
            was_reason = (record.get("ghost_reason") or "").strip()
            # A blank company or ats can never be matched back to a board, so it
            # must fall to the safe side: no board evidence for this row.
            trusted = (
                saw_something and bool(company) and bool(ats)
                and (company, ats) in fetched_ok
            )

            if trusted and pid not in observed_ids:
                reason, outcome = f"{_DELISTED_PREFIX}not on {company}'s board)", "delisted"
            elif trusted and was_reason.startswith(_DELISTED_PREFIX):
                # Seen on the board again: the delisting call was wrong (or the
                # posting was re-listed). Undo it, then re-derive staleness so
                # an old-but-live posting still ends up correctly flagged.
                reason, outcome = stale_reason(record, on_board=True), "relisted"
            elif was_reason.startswith(_DELISTED_PREFIX):
                continue  # no trusted evidence: never overrule a delisting call
            else:
                # `trusted and pid in observed_ids` is direct evidence the board
                # is still serving this posting (the branch above catches
                # trusted-and-absent), so the age rule must not overrule it.
                reason = stale_reason(record, on_board=trusted and pid in observed_ids)
                outcome = "stale" if reason else "unstale"

            if bool(reason) == was_ghost and reason == was_reason:
                continue  # already correct; don't rewrite the row
            record["ghost"] = bool(reason)
            record["ghost_reason"] = reason
            _write(conn, record)
            # WRITE on any change (so the stored reason keeps agreeing with the
            # age the UI derives client-side), but COUNT only a real event: the
            # ghost boolean flipping, or the reason changing KIND. A day-count
            # refresh ("stale (61d old)" -> "stale (62d old)") is neither. See
            # _reason_kind.
            if bool(reason) != was_ghost or _reason_kind(reason) != _reason_kind(was_reason):
                counts[outcome] += 1
    return counts
