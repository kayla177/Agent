"""Backfill node — pull stored rows back through the pipeline tail.

dedupe drops every already-seen id before rank runs, so a posting scored once
(or never) is frozen forever. This node re-injects stored rows that still need
work, tagged `_rescored` so notify persists them WITHOUT announcing them as new
finds. Because they then traverse freshness and rank like any other posting, one
node fixes three separate defects at once:

  * the fit_score backlog gets scored,
  * `ghost` is recomputed for rows that have since aged past JOB_MAX_AGE_DAYS,
  * `last_seen` is refreshed (it previously froze at first sight, so a delisted
    posting was undetectable).

Cost control, measured 2026-07-25 at ~6.5s/job of local inference:
  * the deterministic half (country, baseline score) covers EVERY selected row —
    it is free,
  * only non-dismissed rows are eligible for LLM refinement (392 of 523 rows are
    dismissed, so this alone saves ~42 minutes),
  * LLM_CAP bounds one run and the skipped count is logged, so a bounded pass
    never silently reads as "covered everything". Successive runs converge.
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


def _needs_score(rec: dict) -> bool:
    if rec.get("fit_score") is None:
        return True
    # Baseline-only reason means the LLM has not refined this row yet.
    return is_baseline_reason(rec.get("fit_reason", ""))


def backfill_node(state: JobScraperState) -> JobScraperState:
    incoming = list(state.get("new", []))
    incoming_ids = {p.get("id") for p in incoming}

    selected: list[dict] = []
    for rec in load_records().values():
        pid = rec.get("id")
        if not pid or pid in incoming_ids:
            continue
        if not (_needs_country(rec) or _needs_score(rec)):
            continue
        selected.append(rec)

    # Deterministic, free: fill country for every selected row.
    for rec in selected:
        if _needs_country(rec):
            rec["country"] = country_of(rec.get("location", ""))
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
        if rec.get("status") == "dismissed" or not _needs_score(rec):
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
