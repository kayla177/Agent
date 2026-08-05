"""Stock/trading actions as plain functions returning Discord-ready markdown.

Shared by the Discord bot's slash commands AND its natural-language handler, so
both paths run identical logic. Every function is synchronous and may do
blocking I/O (httpx / litellm / Alpaca) — the bot calls them in a threadpool so
the gateway loop never blocks. Reuses the existing agent code; no new data logic.
"""

from __future__ import annotations

import config
from agents.paper_trader import broker, rules
from agents.stock_digest import indicators as ind
from agents.stock_digest.market import fetch_market
from agents.stock_digest.watchlist import get_watchlist

_SIG_ICON = {"buy": "🟢", "sell": "🔴", "hold": "⚪"}


def help_text() -> str:
    return (
        "**🤖 Stock agent — what I can do**\n"
        "`/portfolio` — paper account value, cash, positions & P&L\n"
        "`/signals` — live BUY/SELL/HOLD per watchlist ticker\n"
        "`/quote SYMBOL` — a single quote\n"
        "`/digest` — run the full Stock Digest\n"
        "`/trade` — run the paper trader now (places PAPER orders, guarded)\n"
        "Or just @mention me in plain English (e.g. \"how's my portfolio?\")."
    )


def portfolio_text() -> str:
    if not broker.is_configured():
        return "⚠️ Alpaca keys aren't set — no paper portfolio to show."
    try:
        a = broker.get_account()
        pos = broker.get_positions()
    except Exception as exc:
        return f"⚠️ Couldn't reach Alpaca: {exc}"
    lines = [
        "**💰 Paper portfolio**",
        f"Value **${a['portfolio_value']:,.2f}** · cash ${a['cash']:,.2f}",
    ]
    if pos:
        pl = sum(p["unrealized_pl"] for p in pos.values())
        lines.append(f"Unrealized P&L: **{'+' if pl >= 0 else ''}${pl:,.2f}**")
        for s, p in pos.items():
            arrow = "🟢" if p["unrealized_plpc"] >= 0 else "🔴"
            lines.append(f"{arrow} {s}: {p['qty']:g} sh · ${p['market_value']:.2f} "
                         f"({'+' if p['unrealized_plpc'] >= 0 else ''}{p['unrealized_plpc']:.1f}%)")
    else:
        lines.append("_No open positions._")
    return "\n".join(lines)


def _signal_rows():
    rows, _ = fetch_market(get_watchlist(), interval=config.TRADER_INTERVAL)
    tech = {}
    for r in rows:
        c = r.get("closes")
        if c:
            tech[r["symbol"]] = {
                "rsi": ind.rsi(c), "sma20": ind.sma(c, 20), "sma50": ind.sma(c, 50),
                "macd_hist": ind.macd(c)[2], "pct_from_high": ind.pct_from_high(c)[1],
            }
    if broker.is_configured():
        try:
            account, positions = broker.get_account(), broker.get_positions()
        except Exception:
            account, positions = {"cash": config.TRADER_DRYRUN_CASH, "portfolio_value": config.TRADER_DRYRUN_CASH}, {}
    else:
        account, positions = {"cash": config.TRADER_DRYRUN_CASH, "portfolio_value": config.TRADER_DRYRUN_CASH}, {}
    return rows, rules.decide(rows, tech, account, positions), tech


def signals_text() -> str:
    rows, decisions, tech = _signal_rows()
    lines = ["**🎯 Signals** _(rules; info only, not advice)_"]
    for d in decisions:
        sym = d["symbol"]
        rsi = (tech.get(sym) or {}).get("rsi")
        price = next((r.get("price") for r in rows if r["symbol"] == sym), None)
        price_s = f"${price:,.2f}" if price is not None else "—"
        rsi_s = f"RSI {rsi:.0f}" if rsi is not None else "RSI —"
        icon = _SIG_ICON.get(d["action"], "⚪")
        lines.append(f"{icon} **{sym}** {d['action'].upper()} · {price_s} · {rsi_s}")
    return "\n".join(lines)


def quote_text(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return "Usage: `/quote SYMBOL` (e.g. `/quote AAPL`)."
    rows, _ = fetch_market([symbol])
    r = rows[0]
    if r.get("price") is None:
        return f"⚠️ Couldn't get a quote for {symbol}."
    arrow = "🟢" if (r["pct"] or 0) >= 0 else "🔴"
    cur = f" {r['currency']}" if r.get("currency") and r["currency"] != "USD" else ""
    return f"{arrow} **{symbol}** ${r['price']:,.2f}{cur} ({'+' if r['pct'] >= 0 else ''}{r['pct']:.1f}%)"


def digest_text() -> str:
    from agents.stock_digest.graph import build_stock_digest_graph
    final = build_stock_digest_graph(send=False).invoke({})
    return final.get("message", "(no digest produced)")


def trade_text() -> str:
    from agents.paper_trader.graph import build_paper_trader_graph
    final = build_paper_trader_graph(send=True).invoke({})
    return final.get("message", "(no result)")
