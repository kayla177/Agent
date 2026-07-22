"""JSON settings API — read effective prefs + secret presence; save the overlay.

POST reuses server.prefs.save_prefs (validation + config.refresh() in-process), so
a saved change is reflected on the next run with no restart. The multiline
string fields mirror the old settings form (one item per line; job sources as
'company, ats, token').
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import prefs as prefstore

router = APIRouter()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _parse_sources(text: str) -> list[dict]:
    out: list[dict] = []
    for ln in _lines(text):
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 3:
            raise ValueError(f"job source line '{ln}' must be 'company, ats, token'")
        out.append({"company": parts[0], "ats": parts[1], "token": parts[2]})
    return out


@router.get("/prefs")
def get_prefs():
    return JSONResponse({"prefs": prefstore.current(), "secrets": prefstore.secret_status()})


@router.post("/prefs")
async def post_prefs(request: Request):
    body = await request.json()

    def g(key: str) -> str:
        return str(body.get(key, "")).strip()

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
        clean = prefstore.save_prefs(payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"saved": clean})
