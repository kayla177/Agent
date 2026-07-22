"""Central configuration for the daily-agents platform.

Secrets come from .env (gitignored). Personal preferences (location, addresses,
news topics, watchlist, job sources) live here as plain defaults, but can be
overridden at runtime by the web control center, which writes ``data/prefs.json``.

Resolution order for each editable pref:  env var  ->  prefs.json overlay  ->
hardcoded default. Env still wins (so power users can pin a value in .env), and
if ``data/prefs.json`` is absent every value equals the hardcoded default — so
existing CLI/launchd runs behave exactly as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root regardless of where a script is launched.
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# The one and only SQLite location — every store and the server layer use this.
DB_PATH = PROJECT_ROOT / "data" / "control_center.db"


# --------------------------------------------------------------------------
# Preferences overlay (data/prefs.json), written by the web settings page.
# Read once at import. A fresh launchd process picks up the latest file; the
# long-lived web process reloads this module after a save (see web/prefs.py).
# --------------------------------------------------------------------------
PREFS_FILE = PROJECT_ROOT / "data" / "prefs.json"


def _load_prefs() -> dict:
    try:
        with PREFS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}  # no overlay -> behave exactly as the hardcoded defaults


_PREFS = _load_prefs()


def _pref(key: str, default):
    """prefs.json overlay value for `key`, or `default` if unset."""
    return _PREFS.get(key, default)


def _apply_prefs() -> None:
    """(Re)compute the editable preference globals from env + the overlay.

    Called at import and by :func:`refresh`. Each pref resolves env var ->
    prefs.json overlay -> hardcoded default.
    """
    global WEATHER_LATITUDE, WEATHER_LONGITUDE, WEATHER_TIMEZONE, WEATHER_TEMP_UNIT
    global COMMUTE_ORIGIN, COMMUTE_DESTINATION
    global NEWS_TOPICS, NEWS_MAX_ITEMS_PER_TOPIC
    global STOCK_WATCHLIST, STOCK_HEADLINE_TOPICS, JOB_SOURCES
    global JOB_MAX_AGE_DAYS, JOB_DROP_GHOSTS, JOB_PROFILE, JOB_MIN_FIT

    # Weather location (Open-Meteo, no API key). Waterloo, Ontario.
    WEATHER_LATITUDE = float(os.getenv("WEATHER_LATITUDE", _pref("WEATHER_LATITUDE", 43.4643)))
    WEATHER_LONGITUDE = float(os.getenv("WEATHER_LONGITUDE", _pref("WEATHER_LONGITUDE", -80.5204)))
    WEATHER_TIMEZONE = os.getenv("WEATHER_TIMEZONE", _pref("WEATHER_TIMEZONE", "America/Toronto"))
    WEATHER_TEMP_UNIT = os.getenv(
        "WEATHER_TEMP_UNIT", _pref("WEATHER_TEMP_UNIT", "celsius")
    )  # "celsius" | "fahrenheit"

    # Commute (Google Maps Routes API). Free-form addresses are fine.
    COMMUTE_ORIGIN = os.getenv("COMMUTE_ORIGIN", _pref("COMMUTE_ORIGIN", ""))
    COMMUTE_DESTINATION = os.getenv("COMMUTE_DESTINATION", _pref("COMMUTE_DESTINATION", ""))

    # News catch-up topics — each becomes a Google News RSS query.
    NEWS_TOPICS = _pref("NEWS_TOPICS", ["technology"])
    NEWS_MAX_ITEMS_PER_TOPIC = int(_pref("NEWS_MAX_ITEMS_PER_TOPIC", 4))

    # Stock digest — tickers and market-news topics (overlay the module defaults
    # in agents/stock_digest/watchlist.py). Empty -> use module defaults.
    STOCK_WATCHLIST = _pref("STOCK_WATCHLIST", [])
    STOCK_HEADLINE_TOPICS = _pref("STOCK_HEADLINE_TOPICS", [])

    # Job scraper — list of {company, ats, token} sources (overlay the module
    # defaults in agents/job_scraper/sources.py). Empty -> use module defaults.
    JOB_SOURCES = _pref("JOB_SOURCES", [])

    # Job scraper — freshness / ghost-job controls.
    #   JOB_MAX_AGE_DAYS: postings older than this are flagged as stale/ghost.
    #   JOB_DROP_GHOSTS:  when True, drop flagged roles instead of just tagging.
    JOB_MAX_AGE_DAYS = int(_pref("JOB_MAX_AGE_DAYS", 60))
    JOB_DROP_GHOSTS = bool(_pref("JOB_DROP_GHOSTS", False))

    # Job scraper — LLM fit-ranking.
    #   JOB_PROFILE: free-text description of the candidate (drives fit scores).
    #   JOB_MIN_FIT: drop roles scoring below this (0 = keep everything).
    JOB_PROFILE = _pref("JOB_PROFILE", "")
    JOB_MIN_FIT = int(_pref("JOB_MIN_FIT", 0))


def refresh() -> None:
    """Re-read ``data/prefs.json`` and reapply the editable prefs in place.

    Lets the long-lived web process pick up a settings save without a restart.
    Agent nodes read these as ``config.<NAME>`` (or via the live getters in
    watchlist.py / sources.py) at call time, so the next run sees new values.
    """
    global _PREFS
    _PREFS = _load_prefs()
    _apply_prefs()


# --------------------------------------------------------------------------
# Model routing (the "hybrid" strategy)
#
# Logical roles -> concrete LiteLLM model strings. Switch any role between a
# local Ollama model and a hosted API by changing one line here.
#   - "local":    fast non-reasoning instruct model for summarize/format steps
#   - "reasoner": local reasoning model for math/logic (slow; use deliberately)
#   - "smart":    higher quality hosted model (not used in Phase 1)
# --------------------------------------------------------------------------
MODEL_ROLES = {
    "local": "ollama/llama3.1:8b",
    "reasoner": "ollama/deepseek-r1:8b",
    "smart": "anthropic/claude-sonnet-5",  # deferred — needs ANTHROPIC_API_KEY
}

# Base URL for the local Ollama server (LiteLLM reads this for ollama/* models).
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")


# --------------------------------------------------------------------------
# Personal preferences — defaults here; editable from the web settings page.
# Applied via _apply_prefs() so refresh() can recompute them in place.
# --------------------------------------------------------------------------
_apply_prefs()


# --------------------------------------------------------------------------
# Secrets (from .env)
# --------------------------------------------------------------------------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_OAUTH_CLIENT_FILE = PROJECT_ROOT / os.getenv(
    "GOOGLE_OAUTH_CLIENT_FILE", "google_oauth_client.json"
)
GOOGLE_TOKEN_FILE = PROJECT_ROOT / os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Twelve Data (stock digest): free tier at https://twelvedata.com/pricing
# (800 req/day, 8/min). Optional — without it the digest falls back to keyless
# CNBC quotes (no price history, so technical indicators show as n/a).
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
