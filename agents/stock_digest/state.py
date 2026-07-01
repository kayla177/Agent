"""Shared state for the multi-node stock-digest graph.

Each node writes its OWN top-level key so parallel branches never write the same
key concurrently (LangGraph merges disjoint keys safely):

  market_data     -> "market"     {"rows": [...], "warnings": [...]}
  technical        -> "technical"  {"summary": str, "by_symbol": {...}}   (runs after market_data)
  news_sentiment   -> "news"       {"overall": str, "by_symbol": {...}, "warnings": [...]}
  synthesize       -> "message"    (final digest; consumed by deliver)
"""

from __future__ import annotations

from typing import TypedDict


class StockDigestState(TypedDict, total=False):
    market: dict
    technical: dict
    news: dict
    message: str
