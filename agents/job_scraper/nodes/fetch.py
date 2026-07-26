"""Fetch node — pull + normalize postings from every configured source.

Each source is wrapped in try/except: a single failing/unknown token logs a
warning into state but never kills the rest of the run.

Also reports `observed_ids` (every posting id any source returned this run) and
`fetched_ok` (companies whose EVERY configured source succeeded). Together these
let `freshness_node` detect a delisted posting directly: a stored id that is
absent from `observed_ids` for a company in `fetched_ok` was not on the board
this run, even though the board was read without error.
"""

from __future__ import annotations

from agents.job_scraper.ats import fetch_source
from agents.job_scraper.sources import get_sources
from agents.job_scraper.state import JobScraperState


def fetch_node(state: JobScraperState) -> JobScraperState:
    raw: list[dict] = []
    warnings: list[str] = []
    ok: set[str] = set()
    failed: set[str] = set()

    for source in get_sources():
        company = source.get("company", "?")
        label = f"{company}/{source.get('ats', '?')}"
        try:
            postings = fetch_source(source)
            raw.extend(postings)
            ok.add(company)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
            failed.add(company)

    # A company is only trustworthy for delisting decisions when EVERY one of its
    # sources succeeded. If a company is on two boards and one 404s, a posting
    # missing from the other could easily still be live.
    return {
        "raw": raw,
        "warnings": warnings,
        "observed_ids": {p.get("id", "") for p in raw if p.get("id")},
        "fetched_ok": ok - failed,
    }
