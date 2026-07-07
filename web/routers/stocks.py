"""Data endpoint for the dashboard "stocks" trading desk.

One cached call (`GET /stocks/desk`) returns everything the stocks panel needs:
paper portfolio + positions, per-ticker BUY/SELL/HOLD signals, a normalized
price-trend series, watchlist % bars, and recent paper trades. Computed from a
single market sweep (Twelve Data) so it's cheap; cached briefly.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

import config
from agents.paper_trader import broker, rules, store
from agents.stock_digest import indicators as ind
from agents.stock_digest.market import fetch_market
from agents.stock_digest.watchlist import get_watchlist

router = APIRouter()

_GREEN, _RED, _AMBER, _MUTED = "#7fc08a", "#e0705a", "#e0b15a", "#8b93a6"
_SIGNAL_COLOR = {"buy": _GREEN, "sell": _RED, "hold": _MUTED}

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL = 300  # seconds


def _account_and_positions():
    if broker.is_configured():
        try:
            return broker.get_account(), broker.get_positions(), None
        except Exception as exc:
            return ({"cash": config.TRADER_DRYRUN_CASH,
                     "portfolio_value": config.TRADER_DRYRUN_CASH}, {}, str(exc))
    return ({"cash": config.TRADER_DRYRUN_CASH,
             "portfolio_value": config.TRADER_DRYRUN_CASH, "simulated": True}, {}, None)


def _technical(rows: list[dict]) -> dict:
    tech: dict[str, dict] = {}
    for r in rows:
        c = r.get("closes")
        if not c:
            continue
        tech[r["symbol"]] = {
            "rsi": ind.rsi(c),
            "sma20": ind.sma(c, 20),
            "sma50": ind.sma(c, 50),
            "macd_hist": ind.macd(c)[2],
            "pct_from_high": ind.pct_from_high(c)[1],
        }
    return tech


def _trend(rows: list[dict], points: int = 30) -> dict:
    """Normalized (base=100) recent close series per ticker with history."""
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


def _compute_desk() -> dict:
    rows, _warn = fetch_market(get_watchlist())
    tech = _technical(rows)
    account, positions, acct_err = _account_and_positions()

    # Portfolio
    portfolio = {
        "configured": broker.is_configured(),
        "error": acct_err,
        "value": account.get("portfolio_value"),
        "cash": account.get("cash"),
        "simulated": account.get("simulated", False),
        "pl": round(sum(p.get("unrealized_pl", 0.0) for p in positions.values()), 2),
        "positions": [
            {"symbol": s, "qty": round(p["qty"], 4), "avg": round(p["avg_entry"], 2),
             "value": round(p["market_value"], 2), "plpc": round(p["unrealized_plpc"], 2)}
            for s, p in positions.items()
        ],
    }

    # Signals (what the rules would do right now)
    decisions = rules.decide(rows, tech, account, positions)
    signals = []
    for d in decisions:
        sym = d["symbol"]
        sig = d["action"]
        rsi = (tech.get(sym) or {}).get("rsi")
        price = next((r.get("price") for r in rows if r["symbol"] == sym), None)
        signals.append({
            "symbol": sym, "price": price,
            "rsi": round(rsi, 0) if rsi is not None else None,
            "signal": sig, "color": _SIGNAL_COLOR[sig], "reason": d["reason"],
        })

    # Watchlist % bars
    wl_labels, wl_series, wl_colors = [], [], []
    for r in rows:
        if r.get("pct") is None:
            continue
        wl_labels.append(r["symbol"])
        wl_series.append(round(r["pct"], 2))
        wl_colors.append(_GREEN if r["pct"] >= 0 else _RED)

    # Recent trades (newest first)
    trades = list(reversed(store.load_trades()))[:6]

    return {
        "portfolio": portfolio,
        "signals": signals,
        "trend": _trend(rows),
        "watchlist": {"labels": wl_labels, "series": wl_series, "colors": wl_colors},
        "trades": trades,
    }


@router.get("/stocks/desk")
def stocks_desk() -> dict:
    now = time.monotonic()
    if _CACHE["data"] is None or (now - _CACHE["ts"]) > _TTL:
        _CACHE["data"] = _compute_desk()
        _CACHE["ts"] = now
    return _CACHE["data"]
