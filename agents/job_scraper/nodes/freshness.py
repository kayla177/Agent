"""Freshness node — score posting age and flag likely ghost / stale roles.

Runs after dedupe, over the `new` postings. It computes `age_days` from the
posting's `posted_at` and flags a role as a ghost when any of:
  - it is older than ``config.JOB_MAX_AGE_DAYS`` (default 60),
  - its application ``deadline`` has already passed, or
  - the source marked it un-listed (Ashby ``isListed == False``).

By default this only TAGS roles (``ghost`` / ``ghost_reason``) so nothing
silently disappears — matching the soft-filter philosophy of ``filter.py``. Set
``config.JOB_DROP_GHOSTS = True`` to drop flagged roles from the batch instead.
"""

from __future__ import annotations

import datetime as dt

import config
from agents.job_scraper.matching import age_days
from agents.job_scraper.state import JobScraperState


def _ghost_reason(p: dict) -> str:
    """Return a short reason string if the posting looks like a ghost, else ""."""
    age = p.get("age_days")
    if age is not None and age > config.JOB_MAX_AGE_DAYS:
        return f"stale ({age}d old)"

    deadline = (p.get("deadline") or "")[:10]
    if deadline:
        try:
            if dt.date.fromisoformat(deadline) < dt.date.today():
                return f"deadline passed ({deadline})"
        except ValueError:
            pass

    if p.get("listed") is False:  # Ashby-only signal; absent elsewhere
        return "delisted by source"

    return ""


def freshness_node(state: JobScraperState) -> JobScraperState:
    kept: list[dict] = []
    for p in state.get("new", []):
        p = {**p, "age_days": age_days(p.get("posted_at", ""))}
        reason = _ghost_reason(p)
        p["ghost"] = bool(reason)
        p["ghost_reason"] = reason
        if reason and config.JOB_DROP_GHOSTS:
            continue  # hard-drop mode: exclude flagged roles entirely
        kept.append(p)
    return {"new": kept}
