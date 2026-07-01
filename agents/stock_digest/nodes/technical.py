"""Technical node — the technical analyst. Runs AFTER market_data and computes
deterministic indicators (RSI, MACD, SMA20/50, % off 52-wk high) from each
symbol's close history. Descriptive facts only — never buy/sell advice."""

from __future__ import annotations

from agents.stock_digest import indicators as ind
from agents.stock_digest.state import StockDigestState


def _trend(price: float, sma20, sma50) -> str:
    """Describe price position vs its moving averages (descriptive, not advice)."""
    if sma20 is None or sma50 is None:
        return "trend n/a"
    if price >= sma20 >= sma50:
        return "above SMA20 & SMA50"
    if price < sma20 < sma50:
        return "below SMA20 & SMA50"
    return "mixed vs SMA20/50"


def technical_node(state: StockDigestState) -> StockDigestState:
    rows = (state.get("market") or {}).get("rows", [])
    by_symbol: dict[str, dict] = {}
    lines: list[str] = []

    for r in rows:
        closes = r.get("closes")
        sym = r.get("symbol", "?")
        if not closes:
            continue  # no history (e.g. keyless fallback) -> skip indicators
        price = r.get("price") or closes[-1]
        rsi = ind.rsi(closes)
        sma20 = ind.sma(closes, 20)
        sma50 = ind.sma(closes, 50)
        _macd, _sig, hist = ind.macd(closes)
        _high, pct_high = ind.pct_from_high(closes)

        by_symbol[sym] = {
            "rsi": rsi, "sma20": sma20, "sma50": sma50,
            "macd_hist": hist, "pct_from_high": pct_high,
            "trend": _trend(price, sma20, sma50),
        }

        parts = []
        if rsi is not None:
            tag = " (overbought)" if rsi >= 70 else " (oversold)" if rsi <= 30 else ""
            parts.append(f"RSI {rsi:.0f}{tag}")
        parts.append(by_symbol[sym]["trend"])
        if hist is not None:
            parts.append(f"MACD {'＋' if hist > 0 else '－'}")
        if pct_high is not None:
            parts.append(f"{pct_high:+.1f}% vs 52wk high")
        lines.append(f"**{sym}** — " + " · ".join(parts))

    if lines:
        summary = "\n".join(lines)
    else:
        summary = "_Indicators need price history — set TWELVE_DATA_API_KEY to enable._"

    return {"technical": {"summary": summary, "by_symbol": by_symbol}}
