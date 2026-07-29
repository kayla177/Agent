"""Fetch node — pull + normalize postings from every configured source.

Each source is wrapped in try/except: a single failing/unknown token logs a
warning into state but never kills the rest of the run.

Also reports `observed_ids` (every posting id any source returned this run) and
`fetched_ok` — the set of `(company, ats)` PAIRS whose fetch this run can be
trusted as a complete picture of that board. Together these let
`freshness_node` (and the store-level delisting sweep) detect a delisted
posting directly: a stored id that is absent from `observed_ids`, whose
`(company, ats)` is in `fetched_ok`, was not on the board this run even though
the board was read completely and without error.

Trust is per BOARD, not per company, because every way of losing trust is a
property of one board: a token can 404, one provider can change its schema, and
one adapter can truncate. Keying on the pair means a broken Lever board no
longer blinds us to a healthy Greenhouse board for the same company, while a
stored row from the broken board is still protected — its own `(company, ats)`
is absent from `fetched_ok`, so nothing about it is ever inferred.

A source earns trust only when ALL THREE hold:

1. It did not raise. The obvious failure mode.
2. It returned at least one posting. An ATS provider schema change (or a
   moved token/board) can just as easily make an adapter return `[]` without
   raising at all — e.g. Greenhouse's adapter is `resp.json().get("jobs", [])`.
   If an empty board counted as healthy, every one of its stored postings
   would look absent from a "healthy" board and get mass-flagged delisted in
   one run.
3. It did not come back exactly at its own page cap. `fetch_workday` requests
   one page of 20 and `fetch_smartrecruiters` one page of 100, and NEITHER
   paginates (see `ats.PAGE_CAPS`), so a big board arrives TRUNCATED: every
   stored posting past the first page is missing from `observed_ids` for a
   reason that has nothing to do with it being delisted. A capped-out result
   is therefore treated exactly like a failure. Real pagination is the proper
   long-term fix and is deliberately not attempted here.
"""

from __future__ import annotations

from agents.job_scraper.ats import PAGE_CAPS, fetch_source
from agents.job_scraper.sources import get_sources
from agents.job_scraper.state import JobScraperState


def fetch_node(state: JobScraperState) -> JobScraperState:
    raw: list[dict] = []
    warnings: list[str] = []
    trusted: set[tuple[str, str]] = set()

    for source in get_sources():
        company = source.get("company", "")
        ats = source.get("ats", "")
        label = f"{company or '?'}/{ats or '?'}"
        try:
            postings = fetch_source(source)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
            continue

        raw.extend(postings)

        # Never trust a blank/unknown company or ats — a stored row could never
        # be matched back to it safely.
        if not (company and ats):
            continue
        if not postings:
            continue  # succeeded but empty: not evidence that a board is healthy
        cap = PAGE_CAPS.get(ats)
        if cap is not None and len(postings) >= cap:
            # Truncated first page, not a complete board. Say so: silently
            # withholding trust would look like the delisting sweep is working
            # when it is really just skipping this board.
            warnings.append(
                f"⚠️ {label}: returned exactly {len(postings)} postings (its page cap), "
                "so it may be truncated — skipped for delisting detection"
            )
            continue
        trusted.add((company, ats))

    return {
        "raw": raw,
        "warnings": warnings,
        "observed_ids": {p.get("id", "") for p in raw if p.get("id")},
        "fetched_ok": trusted,
    }
