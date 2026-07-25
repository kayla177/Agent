"""Filter node — keep only early-career SOFTWARE/ML/CS/AI roles.

A role is kept when its title is (a) co-op/intern/new-grad, (b) a software/ML/
data/CS/AI field (not a generic "Box Office Internship"), and (c) not senior /
advanced-degree. Location is a SOFT preference: by default we do NOT exclude on
location. Set PREFER_LOCATIONS_ONLY = True to hard-filter to the preferred set.
"""

from __future__ import annotations

from agents.job_scraper.matching import (
    is_excluded,
    is_preferred_location,
    is_target_role,
    is_tech_role,
)
from agents.job_scraper.state import JobScraperState

# Config flag. Default OFF: keep every matching role regardless of location.
PREFER_LOCATIONS_ONLY = False


def filter_node(state: JobScraperState) -> JobScraperState:
    raw = state.get("raw", [])
    kept: list[dict] = []
    for p in raw:
        title = p.get("title", "")
        # Early-career AND a software/ML/CS/AI field AND not senior/advanced-degree.
        if not is_target_role(title) or not is_tech_role(title) or is_excluded(title):
            continue
        if PREFER_LOCATIONS_ONLY and not is_preferred_location(p.get("location", "")):
            continue
        kept.append(p)
    return {"filtered": kept}
