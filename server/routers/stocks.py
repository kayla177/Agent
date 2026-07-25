"""Stocks 'desk' data for the /stocks view — market views only (no paper trading).

`GET /stocks/desk` returns everything the beginner-facing stocks page renders:
a market overview (index tiles + movers + a plain-English read), per-stock
verdict cards (bullish/neutral/bearish + summary + explained signals + risks /
catalysts / a learn note), and a normalized price-trend series.

Two data paths:
  - PERSISTED (preferred): the last stock_digest run's LLM analysis, saved by
    agents/stock_digest/store.py. Served as-is (no model call on page load).
  - LIVE fallback: if nothing has been persisted yet, compute a deterministic,
    LLM-free read on demand so a fresh install still shows something useful.

Cached briefly since a live market sweep costs network. Informational, never advice.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from agents.stock_digest import analysis as an
from agents.stock_digest import store
from agents.stock_digest.market import fetch_market
from agents.stock_digest.nodes.market_overview import compute_overview
from agents.stock_digest.watchlist import get_watchlist

router = APIRouter()

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL = 300  # seconds


def _trend(rows: list[dict], points: int = 30) -> dict:
    """Normalized (base=100) recent close series per ticker (for the trend chart)."""
    series, longest = [], 0
    for r in rows:
        c = r.get("closes")
        if not c:
            continue
        tail = c[-points:]
        base = tail[0] or tail[-1] or 1.0
        series.append({"name": r["symbol"], "data": [round(x / base * 100.0, 2) for x in tail]})
        longest = max(longest, len(tail))
    return {"labels": list(range(longest)), "series": series}


def _live() -> dict:
    """Deterministic, model-free desk — the fresh-install / no-persisted fallback."""
    rows, warnings = fetch_market(get_watchlist())
    cards = []
    for r in rows:
        tech = an.indicators_for(r)
        rep = an.deterministic_report(r, tech)
        cards.append({"symbol": r["symbol"], **rep,
                      "price": r.get("price"), "pct": r.get("pct")})
    overview = compute_overview(rows)
    return {
        "generatedAt": None,
        "live": True,
        "market": overview,
        "cards": cards,
        "trend": _trend(rows),
        "warnings": warnings,
    }


def _persisted(latest: dict) -> dict:
    """Rich desk from the last saved run. Trend is recomputed live (cheap, keyless
    when needed) so the chart stays current even though verdicts are from the run."""
    rows, warnings = fetch_market(get_watchlist())
    market = latest.get("market") or {}
    overview = market.get("overview") or {"indices": [], "movers": []}
    overview = {**overview, "read": market.get("read", overview.get("read", ""))}
    return {
        "generatedAt": latest.get("run_at"),
        "live": False,
        "market": overview,
        "cards": latest.get("cards", []),
        "trend": _trend(rows),
        "warnings": warnings,
    }


def _compute() -> dict:
    latest = store.latest_analysis()
    if latest and latest.get("cards"):
        return _persisted(latest)
    return _live()


@router.get("/stocks/desk")
def stocks_desk() -> dict:
    now = time.monotonic()
    if _CACHE["data"] is None or (now - _CACHE["ts"]) > _TTL:
        _CACHE["data"] = _compute()
        _CACHE["ts"] = now
    return _CACHE["data"]
