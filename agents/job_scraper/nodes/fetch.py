"""Fetch node — pull + normalize postings from every configured source.

Each source is wrapped in try/except: a single failing/unknown token logs a
warning into state but never kills the rest of the run.
"""

from __future__ import annotations

from agents.job_scraper.ats import fetch_source
from agents.job_scraper.sources import get_sources
from agents.job_scraper.state import JobScraperState


def fetch_node(state: JobScraperState) -> JobScraperState:
    raw: list[dict] = []
    warnings: list[str] = []
    for source in get_sources():
        label = f"{source.get('company', '?')}/{source.get('ats', '?')}"
        try:
            postings = fetch_source(source)
            raw.extend(postings)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
    return {"raw": raw, "warnings": warnings}
