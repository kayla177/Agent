"""Quotes node — latest price + daily % change per ticker.

All numbers are computed deterministically from a keyless HTTP source; the LLM
is NEVER involved in producing price figures.

Data source: CNBC's public, keyless quote web-service. It returns the latest
price and the previous day's close as plain JSON over httpx (no API key, no
browser/TLS fingerprinting hurdles), for both US symbols and Canadian/TSX
symbols using the Yahoo-style ".TO" suffix. We compute the % change ourselves
from `last` and `previous_day_closing` rather than trusting any preformatted
field, so the math is fully under our control.

(We previously evaluated Stooq — its keyless quote CSV now 404s / is behind a
JS challenge — and Yahoo's chart endpoint — which blocks non-browser HTTP
clients with 429 via TLS fingerprinting. CNBC is the source that reliably works
from httpx with the installed dependencies.)

One ticker failing (bad symbol, network blip) must not kill the rest — each is
fetched independently and a failure becomes a warning line in the output.
"""

from __future__ import annotations

import httpx

from agents.stock_digest.state import StockDigestState
from agents.stock_digest.watchlist import get_watchlist

# Keyless CNBC quote web-service. `output=json` returns a FormattedQuoteResult.
_CNBC_URL = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    "?symbols={symbol}&requestMethod=itv&noform=1&partnerId=2&fund=1"
    "&exthrs=1&output=json"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


class QuoteError(Exception):
    """Raised when a usable quote can't be extracted for a symbol."""


def _to_float(value) -> float | None:
    """Parse CNBC's stringy numbers (e.g. '+7.62', '281.74') into a float."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (ValueError, TypeError):
        return None


def _fetch_quote(symbol: str) -> tuple[float, float, str]:
    """Return (latest_price, previous_close, currency) for `symbol`.

    Raises QuoteError if the response lacks usable numbers.
    """
    resp = httpx.get(_CNBC_URL.format(symbol=symbol), headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    try:
        quote = payload["FormattedQuoteResult"]["FormattedQuote"][0]
    except (KeyError, IndexError, TypeError):
        raise QuoteError("no quote in response")

    # CNBC returns an error code (non-zero) for unknown symbols.
    if quote.get("code") not in (0, "0", None):
        raise QuoteError(f"symbol not found (code {quote.get('code')})")

    price = _to_float(quote.get("last"))
    prev = _to_float(quote.get("previous_day_closing"))
    if price is None or prev is None:
        raise QuoteError("price/previous-close unavailable")

    currency = str(quote.get("currencyCode") or "")
    return price, prev, currency


def _format_line(symbol: str, price: float, prev: float, currency: str) -> str:
    """Format one deterministic quote line, e.g. '🟢 AAPL  $213.40  (+1.2%)'."""
    pct = ((price - prev) / prev * 100.0) if prev else 0.0
    arrow = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
    sign = "+" if pct >= 0 else ""
    sym = "$" if currency in ("USD", "CAD", "") else ""
    cur_tag = f" {currency}" if currency and currency != "USD" else ""
    return f"{arrow} {symbol}  {sym}{price:,.2f}{cur_tag}  ({sign}{pct:.1f}%)"


def fetch_quotes() -> str:
    """Return a newline-joined block of one quote line per watchlist ticker."""
    lines: list[str] = []
    for symbol in get_watchlist():
        try:
            price, prev, currency = _fetch_quote(symbol)
            lines.append(_format_line(symbol, price, prev, currency))
        except Exception as exc:  # one bad ticker must not kill the rest
            lines.append(f"⚠️ {symbol}: quote unavailable ({exc})")
    return "\n".join(lines) if lines else "⚠️ No tickers configured."


def quotes_node(state: StockDigestState) -> StockDigestState:
    try:
        return {"quotes": fetch_quotes()}
    except Exception as exc:  # whole-node guard (shouldn't normally trigger)
        return {"quotes": f"⚠️ Quotes unavailable ({exc})"}


if __name__ == "__main__":
    print(fetch_quotes())
