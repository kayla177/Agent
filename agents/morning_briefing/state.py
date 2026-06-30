"""Shared state for the morning-briefing graph.

Each data-gathering node writes to its OWN key, so the parallel fan-out nodes
never write the same key concurrently (LangGraph merges disjoint keys safely).
`synthesize` reads the section keys and writes `message`; `deliver` reads it.
"""

from __future__ import annotations

from typing import TypedDict


class BriefingState(TypedDict, total=False):
    # Section text produced by each data node (markdown-ish, human-readable).
    weather: str
    commute: str
    calendar: str
    news: str
    # Final assembled message produced by synthesize, consumed by deliver.
    message: str
