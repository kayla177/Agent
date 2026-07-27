"""Fetch node — pull + normalize postings from every configured source.

Each source is wrapped in try/except: a single failing/unknown token logs a
warning into state but never kills the rest of the run.

Also reports `observed_ids` (every posting id any source returned this run) and
`fetched_ok` (companies whose EVERY configured source succeeded AND returned at
least one posting). Together these let `freshness_node` (and the store-level
delisting sweep) detect a delisted posting directly: a stored id that is
absent from `observed_ids` for a company in `fetched_ok` was not on the board
this run, even though the board was read without error.

A source that raises is the obvious failure mode, but an ATS provider schema
change (or a moved token/board) can just as easily make an adapter return `[]`
without raising at all — e.g. Greenhouse's adapter is `resp.json().get("jobs",
[])`. If that company were still added to `fetched_ok`, every one of its
stored postings would look absent from a "healthy" board and get mass-flagged
delisted in one run. So a company only earns `fetched_ok` when EVERY one of
its configured sources both succeeded AND returned at least one posting — a
company on two boards where one returns postings and the other silently
returns `[]` is NOT trustworthy, exactly as if that second source had raised.
`empty` tracks this the same way `failed` tracks exceptions, so a single
empty-but-productive-elsewhere company can't slip through via the union in
`ok`.
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
    empty: set[str] = set()

    for source in get_sources():
        company = source.get("company", "")
        label = f"{company or '?'}/{source.get('ats', '?')}"
        try:
            postings = fetch_source(source)
            raw.extend(postings)
            # Never trust a blank/unknown company name. A source that
            # returned postings counts toward trustworthiness; one that
            # succeeded but returned nothing is tracked separately in
            # `empty` (see docstring) rather than simply left out of `ok` —
            # otherwise a second, productive source for the same company
            # would paper over the empty one via the `ok` union below.
            if company:
                if postings:
                    ok.add(company)
                else:
                    empty.add(company)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
            if company:
                failed.add(company)

    # A company is only trustworthy for delisting decisions when EVERY one of
    # its sources succeeded AND returned at least one posting. If a company is
    # on two boards and one 404s or silently returns empty, a posting missing
    # from the other could easily still be live.
    return {
        "raw": raw,
        "warnings": warnings,
        "observed_ids": {p.get("id", "") for p in raw if p.get("id")},
        "fetched_ok": ok - failed - empty,
    }
