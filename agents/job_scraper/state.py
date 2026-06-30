"""Shared state for the job-scraper graph.

The pipeline is a linear chain (fetch -> filter -> dedupe -> notify), so each
node reads the previous node's list and writes its own key. `warnings`
accumulates per-source failure strings so one bad ATS token never kills the run.

A posting is a normalized dict:
    {"company": str, "ats": str, "title": str, "location": str,
     "url": str, "id": str}
where `id` is already prefixed with company+ats to be globally unique.
"""

from __future__ import annotations

from typing import TypedDict


class JobScraperState(TypedDict, total=False):
    # Every posting pulled from every source (normalized).
    raw: list[dict]
    # Postings whose title matches co-op/intern/new-grad keywords.
    filtered: list[dict]
    # Of the filtered set, the ones not already in the seen-store.
    new: list[dict]
    # Final assembled Discord message, consumed by notify/deliver.
    message: str
    # Non-fatal per-source problems, surfaced but never raised.
    warnings: list[str]
