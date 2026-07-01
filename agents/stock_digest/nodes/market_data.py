"""Market-data node — the first analyst. Fetches quotes + price history for the
watchlist (Twelve Data, keyless CNBC fallback) into state["market"]."""

from __future__ import annotations

from agents.stock_digest.market import fetch_market
from agents.stock_digest.state import StockDigestState
from agents.stock_digest.watchlist import get_watchlist


def market_data_node(state: StockDigestState) -> StockDigestState:
    rows, warnings = fetch_market(get_watchlist())
    return {"market": {"rows": rows, "warnings": warnings}}
