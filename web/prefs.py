"""Read/write the editable preferences overlay (``data/prefs.json``).

The web settings page renders :func:`current` (effective values) and posts back
typed values to :func:`save_prefs`, which validates them, writes the overlay
atomically (temp file + ``os.replace``, mirroring ``job_scraper/store.py``), and
calls :func:`config.refresh` so the long-lived web process reflects the change
on the very next run — no restart needed.

Secrets are NOT editable here. :func:`secret_status` reports only presence
(booleans), never values.
"""

from __future__ import annotations

import json
import os
from typing import Any

import config
from agents.job_scraper.sources import get_sources
from agents.stock_digest.watchlist import get_headline_topics, get_watchlist

# Editable preference keys (must match config.py globals).
STR_KEYS = ("WEATHER_TIMEZONE", "WEATHER_TEMP_UNIT", "COMMUTE_ORIGIN", "COMMUTE_DESTINATION")
FLOAT_KEYS = ("WEATHER_LATITUDE", "WEATHER_LONGITUDE")
INT_KEYS = ("NEWS_MAX_ITEMS_PER_TOPIC",)
LIST_KEYS = ("NEWS_TOPICS", "STOCK_WATCHLIST", "STOCK_HEADLINE_TOPICS")
# JOB_SOURCES is a list of {company, ats, token} dicts, handled specially.

_VALID_ATS = {"greenhouse", "lever", "ashby"}


def current() -> dict[str, Any]:
    """Effective editable prefs (overlay or default) for rendering the form."""
    return {
        "WEATHER_LATITUDE": config.WEATHER_LATITUDE,
        "WEATHER_LONGITUDE": config.WEATHER_LONGITUDE,
        "WEATHER_TIMEZONE": config.WEATHER_TIMEZONE,
        "WEATHER_TEMP_UNIT": config.WEATHER_TEMP_UNIT,
        "COMMUTE_ORIGIN": config.COMMUTE_ORIGIN,
        "COMMUTE_DESTINATION": config.COMMUTE_DESTINATION,
        "NEWS_TOPICS": list(config.NEWS_TOPICS),
        "NEWS_MAX_ITEMS_PER_TOPIC": config.NEWS_MAX_ITEMS_PER_TOPIC,
        # For these, show the effective values (overlay or module default).
        "STOCK_WATCHLIST": get_watchlist(),
        "STOCK_HEADLINE_TOPICS": get_headline_topics(),
        "JOB_SOURCES": get_sources(),
    }


def _validate(prefs: dict[str, Any]) -> dict[str, Any]:
    """Coerce/validate typed values; raise ValueError on bad input."""
    out: dict[str, Any] = {}
    for k in FLOAT_KEYS:
        if k in prefs and prefs[k] != "":
            out[k] = float(prefs[k])
    for k in INT_KEYS:
        if k in prefs and prefs[k] != "":
            n = int(prefs[k])
            if n < 1:
                raise ValueError(f"{k} must be >= 1")
            out[k] = n
    for k in STR_KEYS:
        if k in prefs:
            out[k] = str(prefs[k]).strip()
    if out.get("WEATHER_TEMP_UNIT") not in (None, "celsius", "fahrenheit"):
        raise ValueError("WEATHER_TEMP_UNIT must be 'celsius' or 'fahrenheit'")
    for k in LIST_KEYS:
        if k in prefs:
            items = [str(x).strip() for x in prefs[k] if str(x).strip()]
            out[k] = items
    if "JOB_SOURCES" in prefs:
        srcs = []
        for s in prefs["JOB_SOURCES"]:
            company = str(s.get("company", "")).strip()
            ats = str(s.get("ats", "")).strip().lower()
            token = str(s.get("token", "")).strip()
            if not (company and ats and token):
                continue  # skip incomplete rows
            if ats not in _VALID_ATS:
                raise ValueError(
                    f"unknown ATS '{ats}' for {company} (use one of {sorted(_VALID_ATS)})"
                )
            srcs.append({"company": company, "ats": ats, "token": token})
        out["JOB_SOURCES"] = srcs
    return out


def save_prefs(prefs: dict[str, Any]) -> dict[str, Any]:
    """Validate, atomically write the overlay, and refresh config in-process."""
    clean = _validate(prefs)
    config.PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.PREFS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, config.PREFS_FILE)  # atomic swap
    config.refresh()  # long-lived web process now sees the new values
    return clean


def secret_status() -> list[dict[str, Any]]:
    """Presence (booleans) of each secret — never the value itself."""
    return [
        {"name": "DISCORD_BOT_TOKEN", "set": bool(config.DISCORD_BOT_TOKEN)},
        {"name": "DISCORD_CHANNEL_ID", "set": bool(config.DISCORD_CHANNEL_ID)},
        {"name": "GOOGLE_MAPS_API_KEY", "set": bool(config.GOOGLE_MAPS_API_KEY)},
        {"name": "GOOGLE_OAUTH_CLIENT_FILE", "set": config.GOOGLE_OAUTH_CLIENT_FILE.exists()},
        {"name": "GOOGLE_TOKEN_FILE", "set": config.GOOGLE_TOKEN_FILE.exists()},
        {"name": "ANTHROPIC_API_KEY", "set": bool(config.ANTHROPIC_API_KEY)},
    ]
