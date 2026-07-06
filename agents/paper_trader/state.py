"""Shared state for the paper-trader graph.

  market_data -> "market"     {"rows": [...], "warnings": [...]}   (reused node)
  technical    -> "technical"  {"summary": str, "by_symbol": {...}}  (reused node)
  decision     -> "account" + "decisions"
  execute      -> "executed"   (list of placed-order results; only when send)
  synthesize   -> "message"    (final report; consumed by deliver)
"""

from __future__ import annotations

from typing import TypedDict


class TraderState(TypedDict, total=False):
    market: dict
    technical: dict
    account: dict
    decisions: list
    executed: list
    message: str
