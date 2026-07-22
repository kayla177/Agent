"""Stocks 'desk' data for the /stocks view — market views only (no paper trading).

`GET /stocks/desk` returns the watchlist % bars, a normalized price-trend series,
and simple RSI/trend BUY/SELL/HOLD signals — all computed on demand from the
stock_digest market machinery (fetch_market + indicators). Cached briefly since a
market sweep costs network. Signals are informational, never advice.

Without a TWELVE_DATA_API_KEY the keyless source still gives price/% (so the
watchlist bars work), but there's no price history — trend + RSI degrade to empty.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from agents.stock_digest import indicators as ind
from agents.stock_digest.market import fetch_market
from agents.stock_digest.watchlist import get_watchlist

router = APIRouter()

_GREEN, _RED, _MUTED = "#7fc08a", "#e0705a", "#8b93a6"
_SIGNAL_COLOR = {"buy": _GREEN, "sell": _RED, "hold": _MUTED}
_CACHE: dict = {"ts": 0.0, "data": None}
_TTL = 300  # seconds


def _signal(rsi, price, sma20, sma50):
    """Transparent RSI/trend heuristic — informational only, not advice."""
    if rsi is not None and rsi >= 70:
        return "sell", f"RSI {rsi:.0f} — overbought"
    if rsi is not None and rsi <= 30:
        return "buy", f"RSI {rsi:.0f} — oversold"
    if sma20 and sma50 and price:
        if price > sma20 > sma50:
            return "buy", "uptrend (price > SMA20 > SMA50)"
        if price < sma20 < sma50:
            return "sell", "downtrend (price < SMA20 < SMA50)"
    return "hold", "no strong signal"


def _trend(rows: list[dict], points: int = 30) -> dict:
    """Normalized (base=100) recent close series per ticker."""
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


def _compute() -> dict:
    rows, warnings = fetch_market(get_watchlist())

    wl_labels, wl_series, wl_colors = [], [], []
    signals = []
    for r in rows:
        pct = r.get("pct")
        if pct is not None:
            wl_labels.append(r["symbol"])
            wl_series.append(round(pct, 2))
            wl_colors.append(_GREEN if pct >= 0 else _RED)

        c = r.get("closes")
        rsi = ind.rsi(c) if c else None
        sma20 = ind.sma(c, 20) if c else None
        sma50 = ind.sma(c, 50) if c else None
        sig, reason = _signal(rsi, r.get("price"), sma20, sma50)
        signals.append({
            "symbol": r["symbol"],
            "price": r.get("price"),
            "rsi": round(rsi) if rsi is not None else None,
            "signal": sig,
            "color": _SIGNAL_COLOR[sig],
            "reason": reason,
        })

    return {
        "watchlist": {"labels": wl_labels, "series": wl_series, "colors": wl_colors},
        "trend": _trend(rows),
        "signals": signals,
        "warnings": warnings,
    }


@router.get("/stocks/desk")
def stocks_desk() -> dict:
    now = time.monotonic()
    if _CACHE["data"] is None or (now - _CACHE["ts"]) > _TTL:
        _CACHE["data"] = _compute()
        _CACHE["ts"] = now
    return _CACHE["data"]
