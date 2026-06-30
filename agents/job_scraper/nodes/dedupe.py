"""Dedupe node — drop postings already recorded in the seen-store.

We only READ the store here. New ids are persisted in the notify node, AFTER a
successful pass, so a crash mid-run doesn't silently mark roles as seen without
the user ever being told about them.
"""

from __future__ import annotations

from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import load_seen


def dedupe_node(state: JobScraperState) -> JobScraperState:
    seen = load_seen()
    filtered = state.get("filtered", [])
    # De-dup within this run too (a posting can appear twice in one board).
    new: list[dict] = []
    batch_ids: set[str] = set()
    for p in filtered:
        pid = p.get("id", "")
        if pid in seen or pid in batch_ids:
            continue
        batch_ids.add(pid)
        new.append(p)
    return {"new": new}
