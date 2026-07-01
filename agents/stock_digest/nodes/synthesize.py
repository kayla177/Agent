"""Synthesize node — assemble the multi-analyst digest into one message.

All figures come straight from the market/technical nodes and are placed
verbatim (never from the LLM). Layout: what's moving -> watchlist -> technical
-> news & sentiment. A non-negotiable disclaimer makes clear this is
information, NOT financial advice.
"""

from __future__ import annotations

import datetime as dt

from agents.stock_digest.nodes.quotes import _format_line
from agents.stock_digest.state import StockDigestState

_DISCLAIMER = (
    "_Information only — not financial advice. Figures are delayed and may be "
    "inaccurate; verify before acting._"
)
_SENTIMENT_ICON = {"positive": "🟢", "negative": "🔴", "neutral": "⚪", "n/a": "▫️"}


def _movers(rows: list[dict]) -> str:
    priced = [r for r in rows if r.get("pct") is not None]
    if not priced:
        return "n/a"
    top = sorted(priced, key=lambda r: abs(r["pct"]), reverse=True)[:3]
    bits = []
    for r in top:
        arrow = "🟢" if r["pct"] > 0 else "🔴" if r["pct"] < 0 else "⚪"
        sign = "+" if r["pct"] >= 0 else ""
        bits.append(f"{arrow} {r['symbol']} {sign}{r['pct']:.1f}%")
    return "  ".join(bits)


def _watchlist_block(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r.get("price") is None:
            lines.append(f"⚠️ {r['symbol']}: quote unavailable")
            continue
        lines.append(_format_line(r["symbol"], r["price"], r["prev"], r.get("currency", "")))
    return "\n".join(lines) if lines else "⚠️ No tickers configured."


def _news_block(news: dict) -> str:
    by = news.get("by_symbol", {})
    lines = []
    for sym, s in by.items():
        icon = _SENTIMENT_ICON.get(s.get("label", "n/a"), "▫️")
        score = s.get("score", 0.0)
        lines.append(f"{icon} **{sym}** ({score:+.2f}) — {s.get('theme', '')}")
    return "\n".join(lines) if lines else "No per-ticker news."


def synthesize(state: StockDigestState) -> str:
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    market = state.get("market") or {}
    rows = market.get("rows", [])
    technical = state.get("technical") or {}
    news = state.get("news") or {}

    warnings = list(market.get("warnings", [])) + list(news.get("warnings", []))

    parts = [
        f"**📈 Market Digest — {today}**",
        _DISCLAIMER,
        "",
        f"**What's moving**   {_movers(rows)}",
        "",
        "**Watchlist**",
        _watchlist_block(rows),
        "",
        "**Technical**",
        technical.get("summary", "n/a"),
        "",
        "**News & sentiment**",
        _news_block(news),
        "",
        f"_Market:_ {news.get('overall', 'n/a')}",
    ]
    if warnings:
        parts += ["", "\n".join(warnings)]
    return "\n".join(parts)


def synthesize_node(state: StockDigestState) -> StockDigestState:
    return {"message": synthesize(state)}
