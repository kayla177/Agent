"""Deterministic analysis helpers — the plain-English layer over the numbers.

Everything here is pure math + fixed beginner-friendly copy (no LLM, no network).
It turns a market row + computed indicators into:

  - a `verdict` (bullish | neutral | bearish) and a -2..+2 `score`,
  - a transparent buy/sell/hold `signal` + one-line reason,
  - `explained_signals`: each indicator paired with a beginner "what this means".

The LLM analyst node enriches this with a holistic summary / risks / catalysts,
but the NUMBERS and the per-signal explanations always come from here — so the
page renders identically whether or not a model was available. Shared by
`nodes/analyst.py` (fallback) and `server/routers/stocks.py` (live path).
"""

from __future__ import annotations

from agents.stock_digest import indicators as ind

VERDICTS = ("bearish", "neutral", "bullish")


def signal(rsi, price, sma20, sma50) -> tuple[str, str]:
    """Transparent RSI/trend heuristic — informational only, not advice.

    Lifted verbatim from the original desk endpoint so the buy/sell/hold word
    stays consistent across the agent and the live API path.
    """
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


def _score(rsi, price, sma20, sma50, macd_hist) -> int:
    """A simple, explainable -2..+2 lean from the indicators (never advice)."""
    s = 0
    if sma20 and sma50 and price:
        if price > sma20 > sma50:
            s += 1
        elif price < sma20 < sma50:
            s -= 1
    if macd_hist is not None:
        s += 1 if macd_hist > 0 else -1
    if rsi is not None:
        if rsi >= 70:
            s -= 1  # stretched — more room to fall than rise
        elif rsi <= 30:
            s += 1  # beaten down — room to bounce
    return max(-2, min(2, s))


def _verdict(score: int) -> str:
    if score >= 1:
        return "bullish"
    if score <= -1:
        return "bearish"
    return "neutral"


def explained_signals(tech: dict) -> list[dict]:
    """Pair each computed indicator with a beginner 'what this means'.

    `tech` is one entry from technical_node's `by_symbol`. Returns a list of
    {label, value, meaning}; only includes indicators we actually have.
    """
    out: list[dict] = []
    rsi = tech.get("rsi")
    if rsi is not None:
        if rsi >= 70:
            meaning = ("Above 70 is 'overbought' — the stock has risen fast and buyers "
                       "may be getting exhausted, so a pause or pullback is common.")
        elif rsi <= 30:
            meaning = ("Below 30 is 'oversold' — it has fallen hard and sellers may be "
                       "getting exhausted, so a bounce is common (not guaranteed).")
        else:
            meaning = ("Between 30 and 70 is a normal, balanced range — momentum isn't "
                       "stretched in either direction.")
        out.append({"label": "RSI (momentum)", "value": f"{rsi:.0f}", "meaning": meaning})

    trend = tech.get("trend")
    if trend and trend != "trend n/a":
        if "above" in trend:
            meaning = ("Price is above both its 20- and 50-day averages — a short-term "
                       "uptrend; buyers have had the upper hand recently.")
        elif "below" in trend:
            meaning = ("Price is below both its 20- and 50-day averages — a short-term "
                       "downtrend; sellers have had the upper hand recently.")
        else:
            meaning = ("Price is tangled with its moving averages — no clear short-term "
                       "trend right now (choppy / sideways).")
        out.append({"label": "Trend vs averages", "value": trend, "meaning": meaning})

    hist = tech.get("macd_hist")
    if hist is not None:
        meaning = ("MACD compares fast vs slow momentum. Positive means momentum is "
                   "building to the upside; negative means it's fading."
                   if hist > 0 else
                   "MACD compares fast vs slow momentum. Negative means downward "
                   "momentum; the recent move has been weakening.")
        out.append({"label": "MACD (momentum shift)",
                    "value": "positive" if hist > 0 else "negative", "meaning": meaning})

    pct_high = tech.get("pct_from_high")
    if pct_high is not None:
        meaning = ("How far below its highest price of the past year it sits. Near 0% "
                   "means it's close to a 1-year high; a big negative number means "
                   "it's well off its peak.")
        out.append({"label": "vs 52-week high", "value": f"{pct_high:+.1f}%", "meaning": meaning})

    return out


def indicators_for(row: dict) -> dict:
    """Compute the indicator bundle for one market row (empty if no history)."""
    closes = row.get("closes")
    if not closes:
        return {}
    price = row.get("price") or closes[-1]
    rsi = ind.rsi(closes)
    sma20 = ind.sma(closes, 20)
    sma50 = ind.sma(closes, 50)
    _macd, _sig, hist = ind.macd(closes)
    _high, pct_high = ind.pct_from_high(closes)
    from agents.stock_digest.nodes.technical import _trend  # reuse the exact wording
    return {
        "rsi": rsi, "sma20": sma20, "sma50": sma50,
        "macd_hist": hist, "pct_from_high": pct_high,
        "trend": _trend(price, sma20, sma50),
    }


def deterministic_report(row: dict, tech: dict) -> dict:
    """Build a full report card for one symbol WITHOUT the LLM.

    Used as the analyst node's fallback and as the API's live (no-model) path,
    so both always return the same card shape the frontend expects.
    """
    price = row.get("price")
    sma20, sma50 = tech.get("sma20"), tech.get("sma50")
    rsi = tech.get("rsi")
    sig, reason = signal(rsi, price, sma20, sma50)
    score = _score(rsi, price, sma20, sma50, tech.get("macd_hist"))
    verdict = _verdict(score)

    lean = {"bullish": "leaning positive", "bearish": "leaning negative",
            "neutral": "roughly balanced"}[verdict]
    summary = (
        f"The signals for {row.get('symbol', 'this stock')} are {lean} right now "
        f"({reason}). This is a quick read from price and momentum only — for a "
        f"fuller plain-English take, run the analysis with an LLM key set."
    )
    return {
        "verdict": verdict,
        "score": score,
        "signal": sig,
        "reason": reason,
        "rsi": round(rsi) if rsi is not None else None,
        "summary": summary,
        "signals": explained_signals(tech),
        "risks": [],
        "catalysts": [],
        "learn": ("RSI, moving averages and MACD are 'technical indicators' — they "
                  "describe how the price has behaved, not what a company is worth."),
    }
