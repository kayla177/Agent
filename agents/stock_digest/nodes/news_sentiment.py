"""News + sentiment node — the sentiment analyst. For each watchlist ticker it
pulls recent Google News RSS headlines (keyless) and asks the LOCAL model for a
sentiment score (-1..+1) and a one-line factual theme. Also produces an overall
market-news read. The LLM only reads text — it never touches prices, and is
constrained to describe (no buy/sell advice)."""

from __future__ import annotations

from agents.stock_digest.nodes.headlines import _fetch_headlines, fetch_headlines
from agents.stock_digest.state import StockDigestState
from agents.stock_digest.watchlist import get_watchlist
from shell.model_router import llm

_PER_TICKER = 5  # headlines pulled per ticker before scoring


def _label(score: float) -> str:
    if score >= 0.25:
        return "positive"
    if score <= -0.25:
        return "negative"
    return "neutral"


def _score_ticker(symbol: str) -> dict:
    """Return {score, label, theme} for one ticker from its recent headlines."""
    try:
        headlines = _fetch_headlines(f"{symbol} stock", _PER_TICKER)
    except Exception as exc:
        return {"score": 0.0, "label": "n/a", "theme": f"(headlines unavailable: {exc})"}
    if not headlines:
        return {"score": 0.0, "label": "n/a", "theme": "(no recent headlines)"}

    joined = "\n".join(f"- {h}" for h in headlines)
    raw = llm(
        "local",
        f"Recent news headlines about {symbol}:\n{joined}\n\n"
        "Assess the overall news sentiment. Respond with EXACTLY two lines:\n"
        "SCORE: <a number from -1 (very negative) to 1 (very positive)>\n"
        "THEME: <one short factual sentence describing the news — NO buy/sell "
        "advice, no predictions>",
        max_tokens=120,
    )

    score, theme = 0.0, ""
    for line in raw.splitlines():
        low = line.strip().lower()
        if low.startswith("score:"):
            try:
                score = max(-1.0, min(1.0, float(line.split(":", 1)[1].strip().split()[0])))
            except (ValueError, IndexError):
                score = 0.0
        elif low.startswith("theme:"):
            theme = line.split(":", 1)[1].strip()
    if not theme:
        theme = headlines[0]  # fall back to the top headline
    return {"score": score, "label": _label(score), "theme": theme}


def news_sentiment_node(state: StockDigestState) -> StockDigestState:
    warnings: list[str] = []
    by_symbol: dict[str, dict] = {}
    for sym in get_watchlist():
        by_symbol[sym] = _score_ticker(sym)

    try:
        overall = fetch_headlines()
    except Exception as exc:
        overall = "Market headlines unavailable."
        warnings.append(f"⚠️ market headlines: {exc}")

    return {"news": {"overall": overall, "by_symbol": by_symbol, "warnings": warnings}}
