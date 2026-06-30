"""Settings route: accept the prefs form, validate, save, refresh config.

Multi-line fields (news topics, watchlist, headline topics) are one item per
line. Job sources are one ``company, ats, token`` per line. Secrets are never
posted here — they stay in .env.
"""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from web import prefs

router = APIRouter()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _parse_sources(text: str) -> list[dict]:
    out: list[dict] = []
    for ln in _lines(text):
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 3:
            raise ValueError(
                f"job source line '{ln}' must be 'company, ats, token'"
            )
        out.append({"company": parts[0], "ats": parts[1], "token": parts[2]})
    return out


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()

    def g(key: str) -> str:
        return str(form.get(key, "")).strip()

    payload = {
        "WEATHER_LATITUDE": g("WEATHER_LATITUDE"),
        "WEATHER_LONGITUDE": g("WEATHER_LONGITUDE"),
        "WEATHER_TIMEZONE": g("WEATHER_TIMEZONE"),
        "WEATHER_TEMP_UNIT": g("WEATHER_TEMP_UNIT"),
        "COMMUTE_ORIGIN": g("COMMUTE_ORIGIN"),
        "COMMUTE_DESTINATION": g("COMMUTE_DESTINATION"),
        "NEWS_TOPICS": _lines(g("NEWS_TOPICS")),
        "NEWS_MAX_ITEMS_PER_TOPIC": g("NEWS_MAX_ITEMS_PER_TOPIC"),
        "STOCK_WATCHLIST": _lines(g("STOCK_WATCHLIST")),
        "STOCK_HEADLINE_TOPICS": _lines(g("STOCK_HEADLINE_TOPICS")),
    }
    try:
        payload["JOB_SOURCES"] = _parse_sources(g("JOB_SOURCES"))
        prefs.save_prefs(payload)
    except ValueError as exc:
        msg = urllib.parse.quote(str(exc))
        return RedirectResponse(url=f"/settings?error={msg}", status_code=303)

    return RedirectResponse(url="/settings?saved=1", status_code=303)
