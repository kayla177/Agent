"""Shared state for the job-scraper graph.

The pipeline is a linear chain (fetch -> filter -> dedupe -> backfill ->
freshness -> rank -> notify), so each node reads the previous node's list and
writes its own key. `warnings` accumulates per-source failure strings so one
bad ATS token never kills the run.

A posting is a normalized dict:
    {"company": str, "ats": str, "title": str, "location": str,
     "url": str, "id": str,
     # enrichment from the ATS adapter (best-effort; "" / None when omitted):
     "posted_at": str, "updated_at": str, "deadline": str, "remote": bool|None,
     "department": str, "compensation": str|None, "description": str,
     # derived downstream by the freshness / dedupe / rank nodes:
     "age_days": int|None, "ghost": bool, "ghost_reason": str,
     "canonical_location": str, "dup_of": str|None, "also_on": list[str],
     "country": str,            # US | CA | OTHER | UNKNOWN (locations.py)
     "fit_score": int,          # ALWAYS set (deterministic baseline, LLM-refined)
     "fit_reason": str,
     "eligible": bool,          # rank.py: undergrad-eligible? False -> dropped.
                                # NOT transient — it survives into the store.
     # transient pipeline tags (stripped before persistence):
     "_rescored": bool,   # re-injected backlog row; persisted, never announced
     "_skip_llm": bool,   # baseline score only; no inference spent on this row
    }
where `id` is already prefixed with company+ats to be globally unique.
"""

from __future__ import annotations

from typing import TypedDict


class JobScraperState(TypedDict, total=False):
    # Every posting pulled from every source (normalized). Still readable at
    # `notify` time, which is what lets `store.refresh_descriptions` repair
    # truncated stored descriptions with no second fetch pass.
    raw: list[dict]
    # Postings whose title matches co-op/intern/new-grad keywords. Same deal:
    # read again at `notify` time for the description repair.
    filtered: list[dict]
    # Of the filtered set, the ones not already in the seen-store.
    new: list[dict]
    # Graph input: when True, backfill's LLM refinement pass runs (bounded by
    # LLM_CAP). False/absent keeps the interactive "run scraper" path fast.
    backfill: bool
    # Final assembled Discord message, consumed by notify/deliver.
    message: str
    # Non-fatal per-source problems, surfaced but never raised.
    warnings: list[str]
    # fetch: every posting id returned by any source this run. A stored posting
    # absent from this set, whose (company, ats) is in `fetched_ok`, is delisted.
    observed_ids: set[str]
    # fetch: the `(company, ats)` BOARDS whose fetch this run can be trusted as
    # a complete picture — no error, non-empty, and not truncated at the
    # adapter's page cap. Only these can decide a delisting. Keyed per board,
    # not per company, because every way of losing trust is a property of one
    # board (see fetch.py).
    fetched_ok: set[tuple[str, str]]
