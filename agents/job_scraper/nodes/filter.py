"""Filter node — keep only co-op/intern/new-grad roles, and tag every posting
with a country.

Location NEVER drops a posting here. Every posting is classified (US | CA |
OTHER | UNKNOWN) and stored, and visibility is decided later — the board filters
to config.JOB_COUNTRIES and the digest omits non-matching rows. That keeps a
misclassification auditable in the database and makes "actually show me London"
a toggle rather than a re-scrape. The only drops are the role filters.
"""

from __future__ import annotations

from agents.job_scraper.locations import country_of
from agents.job_scraper.matching import is_excluded, is_target_role, is_tech_role
from agents.job_scraper.state import JobScraperState


def filter_node(state: JobScraperState) -> JobScraperState:
    kept: list[dict] = []
    for p in state.get("raw", []):
        title = p.get("title", "")
        # Early-career AND a software/ML/CS/AI field AND not senior/advanced-degree.
        if not is_target_role(title) or not is_tech_role(title) or is_excluded(title):
            continue
        kept.append({**p, "country": country_of(p.get("location", ""), p.get("title", ""))})
    return {"filtered": kept}
