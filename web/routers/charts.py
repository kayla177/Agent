"""JSON data endpoints for the dashboard charts (consumed by charts.js).

Kept separate from the page routes so the front-end can fetch numbers and let
ApexCharts render them. Stock quotes are cached briefly so repeated dashboard
loads don't hammer the (keyless, unofficial) CNBC endpoint.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from agents.application_tracker import store
from agents.job_scraper import store as jobstore

router = APIRouter()

# Palette (kept in sync with the app.css space theme) for per-series colors.
_GREEN = "#7fc08a"
_BLUE = "#7fb0ff"
_AMBER = "#e0b15a"
_RED = "#e0705a"

_STATUS_COLORS = {
    "applied": _BLUE,
    "interview": _AMBER,
    "offer": _GREEN,
    "accepted": _GREEN,
    "rejected": _RED,
}


@router.get("/charts/applications")
def applications_chart() -> dict:
    """Pipeline counts per status (donut)."""
    apps = store.load_all()
    counts = {s: 0 for s in store.STATUSES}
    for a in apps:
        s = a.get("status")
        if s in counts:
            counts[s] += 1
    # Only include statuses that have entries, so the donut isn't all zeros.
    labels = [s for s in store.STATUSES if counts[s] > 0]
    return {
        "labels": [s.capitalize() for s in labels],
        "series": [counts[s] for s in labels],
        "colors": [_STATUS_COLORS[s] for s in labels],
        "total": sum(counts.values()),
    }


_JOB_STATUS_COLORS = {
    "new": _BLUE,
    "viewed": _AMBER,
    "applied": _GREEN,
    "dismissed": _RED,
}


@router.get("/charts/jobs")
def jobs_chart() -> dict:
    """Scraped-role counts per status (donut)."""
    records = jobstore.load_records().values()
    counts = {s: 0 for s in jobstore.STATUSES}
    for r in records:
        s = r.get("status")
        if s in counts:
            counts[s] += 1
    labels = [s for s in jobstore.STATUSES if counts[s] > 0]
    return {
        "labels": [s.capitalize() for s in labels],
        "series": [counts[s] for s in labels],
        "colors": [_JOB_STATUS_COLORS[s] for s in labels],
        "total": sum(counts.values()),
    }


# --- Stock quotes (cached) ----------------------------------------------------
_STOCK_CACHE: dict = {"ts": 0.0, "data": None}
_STOCK_TTL = 300  # seconds


def _compute_stocks() -> dict:
    # Imported lazily so loading this module doesn't pull httpx until needed.
    from agents.stock_digest.nodes.quotes import QuoteError, _fetch_quote
    from agents.stock_digest.watchlist import get_watchlist

    labels, series, colors = [], [], []
    for symbol in get_watchlist():
        try:
            price, prev, _currency = _fetch_quote(symbol)
        except (QuoteError, Exception):
            continue  # skip a symbol that won't resolve; never error the chart
        pct = ((price - prev) / prev * 100.0) if prev else 0.0
        labels.append(symbol)
        series.append(round(pct, 2))
        colors.append(_GREEN if pct >= 0 else _RED)
    return {"labels": labels, "series": series, "colors": colors}


@router.get("/charts/stocks")
def stocks_chart() -> dict:
    """Daily % change per watchlist ticker (bar), cached for a few minutes."""
    now = time.monotonic()
    if _STOCK_CACHE["data"] is None or (now - _STOCK_CACHE["ts"]) > _STOCK_TTL:
        _STOCK_CACHE["data"] = _compute_stocks()
        _STOCK_CACHE["ts"] = now
    return _STOCK_CACHE["data"]
