"""Backfill node — pull stored rows back through the pipeline tail.

dedupe drops every already-seen id before rank runs, so a posting scored once
(or never) is frozen forever. This node re-injects stored rows that still need
work, tagged `_rescored` so notify persists them WITHOUT announcing them as new
finds. Because they then traverse freshness and rank like any other posting,
the fit_score backlog gets scored.

NOT done here — and this is the honest version of a claim this docstring used
to overstate:

  * `ghost` is NOT this node's job. Selection is `_needs_country` /
    `_lacks_score` / `_needs_llm_refinement`, all of which go False once a row
    has a country, a score and a non-baseline reason, so a converged row is
    never re-injected and its `ghost` could never be recomputed here.
    `store.sweep_ghosts` (called from `notify_node`) owns every ghost decision
    against the whole store instead, in both directions.
  * Delisting detection. `fetch_node` reports `observed_ids` (every id any
    source returned) and `fetched_ok` (the `(company, ats)` boards read
    completely and without error); `freshness_node` applies that to postings
    passing through the pipeline and `store.sweep_ghosts` to everything else.
    `last_seen` is intentionally NOT bumped for `_rescored` rows (see
    store.upsert_records) — it means "observed in a live scrape", and a backlog
    row reprocessed here was NOT re-observed, only rescored.

Convergence is real but PARTIAL, and the gap is load-bearing: a row selected
because it lacks a country or a score stops being selected once those are
filled, but a LIVE row whose reason is still the baseline can only earn a
non-baseline reason from the LLM — and the LLM pass is gated on a candidate
profile existing. With no profile set, the ~132 non-dismissed rows are
therefore re-selected, re-scored by the deterministic baseline and re-upserted
on EVERY run, including every interactive "run scraper" click. That is cheap
(no inference, no network) but it is not free and it is not convergence. It
resolves the moment a profile is set and a backfill run refines those rows.

Cost control, measured 2026-07-25 at ~6.5s/job of local inference:
  * the deterministic half (country, baseline score) covers EVERY selected row —
    it is cheap, but see the convergence caveat above: with no profile set it
    runs over the whole live backlog on every single run,
  * dismissed rows are never eligible for LLM refinement (392 of 523 rows are
    dismissed, so this alone saves ~42 minutes) — and once a dismissed row has
    BOTH a country and a score, it is done: it is never reselected again,
    because (unlike a live row) it can never earn a non-baseline reason,
  * LLM_CAP bounds one run and the over-cap count is logged, so a bounded pass
    never silently reads as "covered everything". Successive runs converge:
    a row selected in one run because it lacked a country/score/refinement is
    not reselected in the next run once that gap is filled (dismissed rows:
    filled by the baseline pass alone; live rows: filled once actually
    refined).
"""

from __future__ import annotations

import config
import profile_store
from agents.job_scraper.locations import country_of
from agents.job_scraper.scoring import is_baseline_reason
from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import load_records

# Max rows handed to the LLM in one run (~6.5s each).
LLM_CAP = 60


def _needs_country(rec: dict) -> bool:
    return not (rec.get("country") or "").strip()


def _lacks_score(rec: dict) -> bool:
    """True if no fit_score has ever been computed for this row at all."""
    return rec.get("fit_score") is None


def _needs_llm_refinement(rec: dict) -> bool:
    """True if the LLM could still meaningfully improve this row's score.

    Dismissed rows are EXCLUDED unconditionally: they are never refined (no
    inference is spent on a job already rejected), so once a dismissed row has
    a score at all, it is done. Basing this on `is_baseline_reason` alone
    (like a live row) would loop forever for a dismissed row, since a
    baseline-only reason never changes for a row that's never sent to the LLM.
    """
    if rec.get("status") == "dismissed":
        return False
    return _lacks_score(rec) or is_baseline_reason(rec.get("fit_reason", ""))


def backfill_node(state: JobScraperState) -> JobScraperState:
    incoming = list(state.get("new", []))
    incoming_ids = {p.get("id") for p in incoming}

    selected: list[dict] = []
    for rec in load_records().values():
        pid = rec.get("id")
        if not pid or pid in incoming_ids:
            continue
        if not (_needs_country(rec) or _lacks_score(rec) or _needs_llm_refinement(rec)):
            continue
        selected.append(rec)

    # Deterministic, free: fill country for every selected row.
    for rec in selected:
        if _needs_country(rec):
            rec["country"] = country_of(rec.get("location", ""), rec.get("title", ""))
        rec["_rescored"] = True

    # Expensive: LLM refinement, only when this run opted in. The launchd runs
    # pass backfill=True (nobody is waiting) and the "score backlog" button does
    # too; the interactive "run scraper" button does not, so it stays fast.
    #
    # ALSO gated on a profile existing. With no profile the rank prompt tells the
    # model to return a null score for every role, so refinement cannot improve
    # anything — and because a null score leaves the baseline reason in place,
    # those rows stay selectable and would be re-queued on EVERY run. Measured on
    # the live data that is 60 rows x ~6.5s = ~6.5 min of inference twice daily,
    # forever, producing nothing. Skip it until a profile exists.
    has_profile = bool(
        (config.JOB_PROFILE or "").strip() or profile_store.fit_profile_text()
    )
    refine = bool(state.get("backfill")) and has_profile
    if state.get("backfill") and not has_profile:
        print(
            "ℹ️ backfill: no candidate profile set, so LLM refinement is skipped "
            "(it could only return null scores). Set one on the Settings page."
        )
    budget = LLM_CAP if refine else 0
    over_cap = 0
    for rec in selected:
        if not _needs_llm_refinement(rec):
            rec["_skip_llm"] = True
        elif budget > 0:
            budget -= 1
        else:
            rec["_skip_llm"] = True
            if refine:
                over_cap += 1  # only "over the cap" when refinement was actually on

    if over_cap:
        print(f"ℹ️ backfill: {over_cap} row(s) over the {LLM_CAP}-row LLM cap, deferred to a later run")

    return {"new": incoming + selected}
