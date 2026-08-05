"""Trader market-data node — like the stock-digest one, but fetches bars at the
trader's configured interval (config.TRADER_INTERVAL, e.g. 15min) so the
indicators react to intraday moves. Writes state["market"]."""

from __future__ import annotations

import config
from agents.paper_trader.state import TraderState
from agents.stock_digest.market import fetch_market
from agents.stock_digest.watchlist import get_watchlist


def market_data_node(state: TraderState) -> TraderState:
    rows, warnings = fetch_market(get_watchlist(), interval=config.TRADER_INTERVAL)
    return {"market": {"rows": rows, "warnings": warnings}}
