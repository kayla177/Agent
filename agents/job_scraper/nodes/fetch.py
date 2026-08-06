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


class NoBoardReachable(RuntimeError):
    """Every configured board failed, so this run learned nothing.

    Raised rather than returned, and that is deliberate: `server/runner.py`
    marks any graph that completes without raising as `success`, so a returned
    `state["error"]` would still have been filed as a successful run.

    Measured from `jobscraper.log` on 2026-08-06 — one run logged 261 fetch
    failures, every board, all `[Errno 8] nodename nor servname provided`. The
    machine had no DNS and therefore no network; launchd had fired while it was
    asleep or before Wi-Fi came up. That run was recorded as **success** with
    "No new co-op / intern / new-grad roles since last check", which is exactly
    how it stayed invisible: a run that reached nothing looked identical to a run
    that reached everything and found nothing new.

    Nothing downstream is corrupted by such a run — a board that raises never
    enters `fetched_ok`, so no posting is falsely marked delisted — so this is
    about telling the user the truth, not about protecting the data.
    """


#: A failure whose text says the hostname could not be resolved. When EVERY
#: failure looks like this the cause is the machine, not the boards, and the
#: message should say so — but only then. Naming the wrong cause is how a reader
#: stops trusting every other note the report shows.
_DNS_MARKERS = (
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
)


def fetch_node(state: JobScraperState) -> JobScraperState:
    raw: list[dict] = []
    warnings: list[str] = []
    trusted: set[tuple[str, str]] = set()

    attempted = 0
    failures: list[str] = []

    for source in get_sources():
        company = source.get("company", "")
        ats = source.get("ats", "")
        label = f"{company or '?'}/{ats or '?'}"
        attempted += 1
        try:
            postings = fetch_source(source)
        except Exception as exc:  # one bad source must not kill the run
            warnings.append(f"⚠️ {label}: fetch failed ({exc})")
            failures.append(str(exc))
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

    # Reached NOTHING. The bar is deliberately "every board failed", not "fewer
    # than usual": a run that read one board really did learn something, and the
    # per-board warnings already report the rest. An empty-but-successful board
    # is not a failure either — that is the normal case for most of 259 boards,
    # and raising on it would turn a quiet week into an error every run.
    if attempted and len(failures) == attempted:
        detail = ""
        if all(any(m in f.lower() for m in _DNS_MARKERS) for f in failures):
            detail = (
                " Every failure was a hostname-resolution error, so this machine "
                "had no network when the run fired — not a problem with the boards."
            )
        raise NoBoardReachable(
            f"all {attempted} configured boards failed, so this run read nothing "
            f"and cannot be reported as a successful scrape.{detail}"
        )

    return {
        "raw": raw,
        "warnings": warnings,
        "observed_ids": {p.get("id", "") for p in raw if p.get("id")},
        "fetched_ok": trusted,
    }
