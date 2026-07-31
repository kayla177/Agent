"""Watchlist and headline-topic configuration for the stock-digest agent.

Edit WATCHLIST to your own tickers. The quotes node fetches from CNBC's keyless
quote service, which accepts the familiar Yahoo-style symbol convention:

  - US stocks: bare symbol, no suffix          -> "AAPL", "MSFT", "NVDA"
  - Canadian (TSX): append ".TO"               -> "SHOP.TO", "RY.TO"
  - TSX Venture: append ".V"                    -> "ABC.V"
  - Other exchanges use their own suffix        -> ".L" (London), ".DE" (Xetra)

Look up the exact symbol on https://finance.yahoo.com if unsure (CNBC accepts
the same suffixes).
"""

from __future__ import annotations

import config

# Module defaults. The web settings page can override these via the prefs
# overlay (config.STOCK_WATCHLIST / config.STOCK_HEADLINE_TOPICS); an empty
# overlay falls back to these, so CLI/launchd runs behave as before.
_DEFAULT_WATCHLIST: list[str] = [
    "AAPL",      # Apple (US)
    "MSFT",      # Microsoft (US)
    "NVDA",      # NVIDIA (US)
    "SHOP.TO",   # Shopify on the TSX (Canada) — note the .TO suffix
]
_DEFAULT_HEADLINE_TOPICS: list[str] = ["stock market", "Federal Reserve"]

# Snapshot constants (used by CLI/launchd; computed once at import).
WATCHLIST: list[str] = config.STOCK_WATCHLIST or _DEFAULT_WATCHLIST
HEADLINE_TOPICS: list[str] = config.STOCK_HEADLINE_TOPICS or _DEFAULT_HEADLINE_TOPICS

# How many headlines to pull per topic before summarizing.
HEADLINES_PER_TOPIC: int = 4

# Market-overview index proxies (ETFs resolve on both Twelve Data and the keyless
# CNBC fallback). Friendly names are what the beginner sees on the tiles. VIX is
# the market's "fear gauge"; if a symbol can't be fetched it's silently skipped.
INDEX_SYMBOLS: list[tuple[str, str]] = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("DIA", "Dow Jones"),
    ("VIX", "Volatility (VIX)"),
]


# Live getters — read config at CALL time so a web settings save (followed by
# config.refresh()) is reflected on the next run without restarting the process.
def get_watchlist() -> list[str]:
    return config.STOCK_WATCHLIST or _DEFAULT_WATCHLIST


def get_headline_topics() -> list[str]:
    return config.STOCK_HEADLINE_TOPICS or _DEFAULT_HEADLINE_TOPICS


def get_headlines_per_topic() -> int:
    return HEADLINES_PER_TOPIC


def get_index_symbols() -> list[tuple[str, str]]:
    """(symbol, friendly-name) pairs for the market-overview strip."""
    return INDEX_SYMBOLS
