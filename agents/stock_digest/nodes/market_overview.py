"""Market-overview node — the context strip. Fetches the big index proxies
(S&P 500 / Nasdaq / Dow / VIX) and computes each one's day change + a short
normalized trend, plus a "biggest movers today" list from the watchlist itself
(cheap discovery, no extra API). Deterministic only — the one-line plain read is
filled in later by the analyst node.

Runs in parallel with the market/news branches; writes its own `overview` key.
"""

from __future__ import annotations

from agents.stock_digest.market import fetch_market
from agents.stock_digest.state import StockDigestState
from agents.stock_digest.watchlist import get_index_symbols

_TREND_POINTS = 30


def _sparkline(closes) -> list[float]:
    """Normalized (base=100) recent close series for a mini sparkline."""
    if not closes:
        return []
    tail = closes[-_TREND_POINTS:]
    base = tail[0] or tail[-1] or 1.0
    return [round(x / base * 100.0, 2) for x in tail]


def _movers(watchlist_rows: list[dict]) -> list[dict]:
    """Biggest absolute movers among the user's watchlist today."""
    priced = [r for r in watchlist_rows if r.get("pct") is not None]
    top = sorted(priced, key=lambda r: abs(r["pct"]), reverse=True)[:5]
    return [{"symbol": r["symbol"], "pct": round(r["pct"], 2)} for r in top]


def compute_overview(watchlist_rows: list[dict]) -> dict:
    """Deterministic overview: index tiles + biggest watchlist movers (no LLM).

    Shared by the graph node and the live /stocks desk fallback. `read` is left
    blank here; the analyst node fills the plain-English market read on a run.
    """
    names = dict(get_index_symbols())
    rows, _warnings = fetch_market(list(names))  # index-fetch failures are non-fatal here

    indices: list[dict] = []
    for r in rows:
        if r.get("error") or r.get("pct") is None:
            continue  # silently skip an index we couldn't resolve (context, not core)
        indices.append({
            "symbol": r["symbol"],
            "name": names.get(r["symbol"], r["symbol"]),
            "price": r.get("price"),
            "pct": round(r["pct"], 2),
            "series": _sparkline(r.get("closes")),
        })

    return {"indices": indices, "movers": _movers(watchlist_rows), "read": ""}


def market_overview_node(state: StockDigestState) -> StockDigestState:
    watchlist_rows = (state.get("market") or {}).get("rows", [])
    return {"overview": compute_overview(watchlist_rows)}
