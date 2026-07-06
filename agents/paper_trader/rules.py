"""Deterministic trading rules + guardrails (a transparent mean-reversion
starter). Rules decide buy/sell/hold; the LLM only explains them later.

Signals in:  market rows (price/pct), technical indicators (RSI, SMA, stop),
             account (cash/portfolio_value), current positions.
Decisions out: list of {symbol, action, notional|qty, reason, signals}.

Guardrails: US-only symbols, per-name position cap, cash buffer, and a hard cap
on the number of orders per run. Nothing here places an order — that's execute.
"""

from __future__ import annotations

import config


def _is_us_tradable(symbol: str) -> bool:
    # Alpaca trades US equities; skip suffixed foreign tickers like SHOP.TO.
    return "." not in symbol and ":" not in symbol


def decide(rows: list[dict], tech: dict, account: dict, positions: dict) -> list[dict]:
    portfolio = account.get("portfolio_value", 0.0) or 0.0
    cash = account.get("cash", 0.0) or 0.0
    max_pos_value = portfolio * config.TRADER_MAX_POSITION_PCT / 100.0
    min_cash = portfolio * config.TRADER_MIN_CASH_PCT / 100.0

    sells: list[dict] = []
    buys: list[dict] = []
    holds: list[dict] = []

    for r in rows:
        sym = r.get("symbol", "?")
        price = r.get("price")
        ind = tech.get(sym) or {}
        rsi = ind.get("rsi")
        pos = positions.get(sym)
        held_qty = pos["qty"] if pos else 0.0
        plpc = pos["unrealized_plpc"] if pos else 0.0
        signals = {"rsi": rsi, "price": price, "held": held_qty,
                   "unrealized_plpc": plpc, "pct_from_high": ind.get("pct_from_high")}

        if not _is_us_tradable(sym):
            holds.append({"symbol": sym, "action": "hold", "reason": "not US-tradable on Alpaca", "signals": signals})
            continue
        if rsi is None or price is None:
            holds.append({"symbol": sym, "action": "hold", "reason": "no indicators (need price history)", "signals": signals})
            continue

        # --- SELL: overbought take-profit, or stop-loss ---
        if held_qty > 0 and rsi >= config.TRADER_SELL_RSI:
            sells.append({"symbol": sym, "action": "sell", "qty": held_qty,
                          "reason": f"RSI {rsi:.0f} ≥ {config.TRADER_SELL_RSI} (overbought) — taking profit",
                          "signals": signals})
            continue
        if held_qty > 0 and plpc <= config.TRADER_STOP_LOSS_PCT:
            sells.append({"symbol": sym, "action": "sell", "qty": held_qty,
                          "reason": f"down {plpc:.1f}% vs entry (stop {config.TRADER_STOP_LOSS_PCT}%) — cutting loss",
                          "signals": signals})
            continue

        # --- BUY: oversold entry, within caps ---
        if rsi <= config.TRADER_BUY_RSI:
            existing_value = pos["market_value"] if pos else 0.0
            room = max_pos_value - existing_value
            want = cash * config.TRADER_BUY_FRACTION_PCT / 100.0
            spendable = max(0.0, cash - min_cash)
            notional = min(want, room, spendable)
            if notional >= 1.0:
                buys.append({"symbol": sym, "action": "buy", "notional": notional,
                             "reason": f"RSI {rsi:.0f} ≤ {config.TRADER_BUY_RSI} (oversold) — entering ${notional:.0f}",
                             "signals": signals})
                continue
            holds.append({"symbol": sym, "action": "hold",
                          "reason": f"oversold but blocked (cap/cash) — RSI {rsi:.0f}", "signals": signals})
            continue

        holds.append({"symbol": sym, "action": "hold", "reason": f"RSI {rsi:.0f} — no signal", "signals": signals})

    # Sells first (reduce risk), then buys by most-oversold; cap total orders.
    buys.sort(key=lambda d: d["signals"].get("rsi", 100))
    ordered_trades = (sells + buys)[: config.TRADER_MAX_TRADES_PER_RUN]
    traded_syms = {d["symbol"] for d in ordered_trades}
    # Anything that wanted to trade but got capped becomes a hold note.
    capped = [d for d in (sells + buys) if d["symbol"] not in traded_syms]
    for d in capped:
        holds.append({"symbol": d["symbol"], "action": "hold",
                      "reason": f"signal present but over max {config.TRADER_MAX_TRADES_PER_RUN} trades/run",
                      "signals": d["signals"]})

    return ordered_trades + holds
