"""Commute node — live drive time + traffic via Google Maps Routes API.

Uses the v2 computeRoutes endpoint with TRAFFIC_AWARE routing so the duration
reflects current conditions. Requires GOOGLE_MAPS_API_KEY and the origin/
destination addresses in config.
"""

from __future__ import annotations

import httpx

import config
from agents.morning_briefing.state import BriefingState

_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _traffic_label(live_s: float, static_s: float) -> str:
    """Classify congestion by comparing traffic-aware vs free-flow duration."""
    if static_s <= 0:
        return "unknown"
    ratio = live_s / static_s
    if ratio < 1.15:
        return "light"
    if ratio < 1.4:
        return "moderate"
    return "heavy"


def fetch_commute() -> str:
    """Return a one-line commute summary with current traffic."""
    if not config.GOOGLE_MAPS_API_KEY:
        return "⚠️ Commute skipped (no GOOGLE_MAPS_API_KEY set)."
    if not (config.COMMUTE_ORIGIN and config.COMMUTE_DESTINATION):
        return "⚠️ Commute skipped (set COMMUTE_ORIGIN / COMMUTE_DESTINATION)."

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters",
    }
    body = {
        "origin": {"address": config.COMMUTE_ORIGIN},
        "destination": {"address": config.COMMUTE_DESTINATION},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    resp = httpx.post(_ROUTES_URL, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    routes = resp.json().get("routes") or []
    if not routes:
        return "⚠️ No driving route found between the configured addresses."

    route = routes[0]
    live_s = float(route["duration"].rstrip("s"))
    static_s = float(route.get("staticDuration", route["duration"]).rstrip("s"))
    minutes = round(live_s / 60)
    miles = route.get("distanceMeters", 0) / 1609.34
    return f"🚗 {minutes} min to work ({miles:.1f} mi, traffic {_traffic_label(live_s, static_s)})."


def commute_node(state: BriefingState) -> BriefingState:
    try:
        return {"commute": fetch_commute()}
    except Exception as exc:
        return {"commute": f"⚠️ Commute unavailable ({exc})"}


if __name__ == "__main__":
    print(fetch_commute())
