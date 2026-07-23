"""Filter node — keep only co-op/intern/new-grad roles.

Location is a SOFT preference: by default we do NOT exclude on location so
nothing is missed. Set PREFER_LOCATIONS_ONLY = True to hard-filter to the
preferred locations (Canada/Waterloo/Toronto/Remote).
"""

from __future__ import annotations

from agents.job_scraper.matching import is_excluded, is_preferred_location, is_target_role
from agents.job_scraper.state import JobScraperState

# Config flag. Default OFF: keep every matching role regardless of location.
PREFER_LOCATIONS_ONLY = False


def filter_node(state: JobScraperState) -> JobScraperState:
    raw = state.get("raw", [])
    kept: list[dict] = []
    for p in raw:
        title = p.get("title", "")
        # Must be an early-career role AND not a senior / advanced-degree one.
        if not is_target_role(title) or is_excluded(title):
            continue
        if PREFER_LOCATIONS_ONLY and not is_preferred_location(p.get("location", "")):
            continue
        kept.append(p)
    return {"filtered": kept}
