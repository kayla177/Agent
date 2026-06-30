"""Shared state for the stock-digest graph.

Each data-gathering node writes to its OWN key so the parallel fan-out nodes
never write the same key concurrently (LangGraph merges disjoint keys safely).
`synthesize` reads the section keys and writes `message`; `deliver` reads it.
"""

from __future__ import annotations

from typing import TypedDict


class StockDigestState(TypedDict, total=False):
    # Pre-formatted quote lines, one per ticker (deterministic — numbers never
    # come from the LLM). Produced by the quotes node.
    quotes: str
    # LLM-summarized market-news theme. Produced by the headlines node.
    headlines: str
    # Final assembled message produced by synthesize, consumed by deliver.
    message: str
