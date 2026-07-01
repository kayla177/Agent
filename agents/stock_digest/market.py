"""Market data: price history + latest quote per symbol.

Primary source is Twelve Data (needs a free key) — one `time_series` call per
symbol gives ~1y of daily closes, from which the technical node computes
indicators locally. If the key is missing or a symbol fails (e.g. a TSX ticker
Twelve Data can't resolve), we fall back to the keyless CNBC quote used by the
original agent (latest + previous close only, no history).

Every number is fetched deterministically; the LLM never touches prices.
"""

from __future__ import annotations

import httpx

import config
from agents.stock_digest.nodes.quotes import QuoteError, _fetch_quote

_TD_URL = "https://api.twelvedata.com/time_series"
_OUTPUTSIZE = 260  # ~1 trading year, enough for 52wk high + SMA50/MACD


def _fetch_twelvedata(symbol: str) -> dict:
    """Fetch daily closes from Twelve Data. Raises on any non-ok response."""
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": str(_OUTPUTSIZE),
        "apikey": config.TWELVE_DATA_API_KEY,
    }
    resp = httpx.get(_TD_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error" or "values" not in data:
        raise QuoteError(data.get("message", "twelvedata error"))
    values = data["values"]  # newest-first
    if len(values) < 2:
        raise QuoteError("not enough history")
    closes = [float(v["close"]) for v in reversed(values)]  # oldest -> newest
    price = float(values[0]["close"])
    prev = float(values[1]["close"])
    currency = (data.get("meta") or {}).get("currency", "")
    return {"price": price, "prev": prev, "currency": currency, "closes": closes}


def _row(symbol: str) -> dict:
    """Build one market row, preferring Twelve Data, falling back to CNBC."""
    if config.TWELVE_DATA_API_KEY:
        try:
            d = _fetch_twelvedata(symbol)
            pct = ((d["price"] - d["prev"]) / d["prev"] * 100.0) if d["prev"] else 0.0
            return {
                "symbol": symbol, "price": d["price"], "prev": d["prev"],
                "pct": pct, "currency": d["currency"], "closes": d["closes"],
                "source": "twelvedata", "error": None,
            }
        except Exception:
            pass  # fall through to the keyless quote
    try:
        price, prev, currency = _fetch_quote(symbol)
        pct = ((price - prev) / prev * 100.0) if prev else 0.0
        return {
            "symbol": symbol, "price": price, "prev": prev, "pct": pct,
            "currency": currency, "closes": None, "source": "cnbc", "error": None,
        }
    except Exception as exc:
        return {
            "symbol": symbol, "price": None, "prev": None, "pct": None,
            "currency": "", "closes": None, "source": None, "error": str(exc),
        }


def fetch_market(symbols: list[str]) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings) for every watchlist symbol."""
    rows: list[dict] = []
    warnings: list[str] = []
    for sym in symbols:
        row = _row(sym)
        rows.append(row)
        if row["error"]:
            warnings.append(f"⚠️ {sym}: quote unavailable ({row['error']})")
    if symbols and not config.TWELVE_DATA_API_KEY:
        warnings.append(
            "ℹ️ TWELVE_DATA_API_KEY not set — using keyless quotes; "
            "technical indicators need price history."
        )
    return rows, warnings
