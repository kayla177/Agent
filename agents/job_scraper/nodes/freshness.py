"""Freshness node — score posting age and flag likely ghost / stale roles.

Runs after dedupe, over the `new` postings. It computes `age_days` from the
posting's `posted_at` and flags a role as a ghost when any of:
  - a healthy board was fetched this run and the posting was NOT in it — a
    direct observation that it has been delisted (see `fetch_node`),
  - it is older than ``config.JOB_MAX_AGE_DAYS`` (default 60),
  - its application ``deadline`` has already passed, or
  - the source marked it un-listed (Ashby ``isListed == False``).

By default this only TAGS roles (``ghost`` / ``ghost_reason``) so nothing
silently disappears — matching the soft-filter philosophy of ``filter.py``. Set
``config.JOB_DROP_GHOSTS = True`` to drop flagged roles from the batch instead.

This node only ever sees `state["new"]` — freshly-fetched postings (observed by
definition) plus whatever `backfill_node` re-injects (rows still missing a
country/score/refinement). A fully-processed row (country set, score set,
already refined) is never re-selected by backfill, so it never passes through
here again and this node alone cannot flag OR un-flag it.
`store.sweep_ghosts`, called from `notify_node`, closes that gap: it re-derives
the ghost state of every stored `new`/`viewed` row directly — flagging a
delisted posting, CLEARING a delisting flag the board later contradicts, and
re-deriving age/deadline staleness in both directions — independent of the
pipeline. The age/deadline rule itself is shared code
(`matching.stale_reason`), so both paths can never disagree.
"""

from __future__ import annotations

import config
from agents.job_scraper.matching import age_days, stale_reason
from agents.job_scraper.state import JobScraperState


def _ghost_reason(p: dict, observed_ids: set[str], fetched_ok: set[tuple[str, str]]) -> str:
    """Return a short reason string if the posting looks like a ghost, else ""."""
    # Direct observation beats every heuristic: the board was read completely
    # and successfully this run and this posting was not in it, so it is gone.
    # Only trust this for the exact `(company, ats)` BOARD that was verified
    # healthy (see fetch_node) — not for the company as a whole, since a
    # company can sit on several boards of differing health. Require id,
    # company AND ats to be non-empty first: a blank/missing value must never
    # fall to the unsafe (flag-it) side of this check.
    if p.get("id") and p.get("company") and p.get("ats") \
            and (p.get("company"), p.get("ats")) in fetched_ok \
            and p.get("id") not in observed_ids:
        return f"delisted (not on {p.get('company')}'s board)"

    # Age / deadline / source-unlisted. Shared with store.sweep_ghosts so a
    # converged row that never re-enters the pipeline is judged by the same
    # rule (see matching.stale_reason).
    return stale_reason(p)


def freshness_node(state: JobScraperState) -> JobScraperState:
    # Read defensively: a caller that omits these keys (older tests, a partial
    # run) must never have anything marked delisted.
    observed_ids = state.get("observed_ids") or set()
    fetched_ok = state.get("fetched_ok") or set()
    kept: list[dict] = []
    delisted = 0
    for p in state.get("new", []):
        p = {**p, "age_days": age_days(p.get("posted_at", ""))}
        reason = _ghost_reason(p, observed_ids, fetched_ok)
        p["ghost"] = bool(reason)
        p["ghost_reason"] = reason
        if reason.startswith("delisted ("):
            delisted += 1
        # `_rescored` rows are backlog rows already in the store; dropping one
        # here would discard its freshly computed country/fit_score before
        # notify can persist them, and backfill would re-select (and, if
        # refinable, re-spend an LLM_CAP slot on) the same row every run.
        if reason and config.JOB_DROP_GHOSTS and not p.get("_rescored"):
            continue  # hard-drop mode: exclude flagged NEW roles entirely
        kept.append(p)
    # A bounded/destructive coverage change must never land silently.
    if delisted:
        print(f"ℹ️ freshness: flagged {delisted} posting(s) as delisted (absent from a healthy board)")
    return {"new": kept}
