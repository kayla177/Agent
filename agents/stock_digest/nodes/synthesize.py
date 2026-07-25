"""Synthesize node — assemble the analyst output into one Discord digest.

All figures come straight from the market/overview nodes and are placed verbatim
(never from the LLM). The per-stock verdict + one-line take come from the analyst
node. Layout: market read -> indices -> what's moving -> per-stock verdicts. A
non-negotiable disclaimer makes clear this is information, NOT financial advice.
"""

from __future__ import annotations

import datetime as dt

from agents.stock_digest.state import StockDigestState

_DISCLAIMER = (
    "_Information only — not financial advice. Figures are delayed and may be "
    "inaccurate; verify before acting._"
)
_VERDICT_ICON = {"bullish": "🟢", "neutral": "⚪", "bearish": "🔴"}


def _indices_line(overview: dict) -> str:
    bits = []
    for i in overview.get("indices", []):
        pct = i.get("pct")
        if pct is None:
            continue
        arrow = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
        bits.append(f"{arrow} {i.get('name', i['symbol'])} {pct:+.1f}%")
    return "  ".join(bits) if bits else "n/a"


def _movers(overview: dict) -> str:
    movers = overview.get("movers", [])
    if not movers:
        return "n/a"
    bits = []
    for m in movers[:3]:
        arrow = "🟢" if m["pct"] > 0 else "🔴" if m["pct"] < 0 else "⚪"
        bits.append(f"{arrow} {m['symbol']} {m['pct']:+.1f}%")
    return "  ".join(bits)


def _stock_block(analysis: dict, market: dict) -> str:
    by = analysis.get("by_symbol", {})
    order = analysis.get("order") or list(by)
    prices = {r["symbol"]: r for r in market.get("rows", [])}
    lines = []
    for sym in order:
        rep = by.get(sym)
        if not rep:
            continue
        icon = _VERDICT_ICON.get(rep.get("verdict", "neutral"), "⚪")
        row = prices.get(sym, {})
        price = row.get("price")
        pct = row.get("pct")
        head = f"{icon} **{sym}**"
        if price is not None:
            sign = "+" if (pct or 0) >= 0 else ""
            head += f"  ${price:,.2f}" + (f" ({sign}{pct:.1f}%)" if pct is not None else "")
        head += f" — _{rep.get('verdict', 'neutral')}_"
        lines.append(head)
        summary = rep.get("summary", "").strip()
        if summary:
            lines.append(summary)
        risks = rep.get("risks") or []
        if risks:
            lines.append(f"⚠️ Watch: {risks[0]}")
        lines.append("")
    return "\n".join(lines).strip() if lines else "⚠️ No tickers configured."


def synthesize(state: StockDigestState) -> str:
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    market = state.get("market") or {}
    overview = state.get("overview") or {}
    analysis = state.get("analysis") or {}

    warnings = (list(market.get("warnings", []))
                + list((state.get("news") or {}).get("warnings", []))
                + list(analysis.get("warnings", [])))

    read = analysis.get("market_read") or overview.get("read") or ""

    parts = [
        f"**📈 Market Digest — {today}**",
        _DISCLAIMER,
        "",
    ]
    if read:
        parts += [f"_{read}_", ""]
    parts += [
        f"**Markets**   {_indices_line(overview)}",
        f"**What's moving**   {_movers(overview)}",
        "",
        "**Your watchlist**",
        _stock_block(analysis, market),
    ]
    if warnings:
        parts += ["", "\n".join(warnings)]
    return "\n".join(parts)


def synthesize_node(state: StockDigestState) -> StockDigestState:
    return {"message": synthesize(state)}
