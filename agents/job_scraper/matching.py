"""Title and location matching helpers (deterministic, no LLM).

Keyword matching uses word boundaries so "intern" matches "Software Engineer
Intern" but NOT "Internal Audit" / "Database Engine Internals" — a common
false-positive trap with naive substring checks.
"""

from __future__ import annotations

import datetime as dt
import re

import config

# Role keywords. \b anchors avoid matching inside "internal", "international",
# "internals", etc. Hyphen/space variants of co-op are handled explicitly.
_ROLE_RE = re.compile(
    r"""\b(
        co[\-\s]?op            # co-op, coop, co op
        | interns?             # intern, interns
        | internships?         # internship(s)
        | new[\s\-]grad(uate)? # new grad, new graduate
        | university[\s\-]grad(uate)?  # university grad(uate)
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Seniority / advanced-degree exclusion. A title that carries an early-career
# keyword AND one of these is not for an undergrad (e.g. "Senior … Intern",
# "New Grad — PhD", "Machine Learning Intern (Master's)"). Deterministic first
# line of defense; the LLM eligibility check (rank node) catches JD-gated cases.
_EXCLUDE_RE = re.compile(
    r"""(
        \bsenior\b | \bstaff\b | \bprincipal\b | \blead\b
        | \bmanager\b | \bdirector\b | \barchitect\b
        | \bph\.?\s?d\b | \bdoctoral\b | \bdoctorate\b
        | \bmaster'?s\b | \bmba\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

def is_target_role(title: str) -> bool:
    """True if the title looks like a co-op / intern / new-grad role."""
    return bool(_ROLE_RE.search(title or ""))


def is_excluded(title: str) -> bool:
    """True if the title names a senior/advanced-degree role an undergrad can't
    take (senior/staff/principal/lead/manager/director, or PhD/Master's/MBA)."""
    return bool(_EXCLUDE_RE.search(title or ""))


def age_days(posted_at: str) -> int | None:
    """Whole days between an ISO date string and today; None if unparseable."""
    try:
        d = dt.date.fromisoformat((posted_at or "")[:10])
    except (ValueError, TypeError):
        return None
    return (dt.date.today() - d).days


def stale_reason(posting: dict) -> str:
    """Age/deadline/source staleness for one posting, or "" if it looks fine.

    This is the half of the ghost decision that depends ONLY on the posting's
    own stored fields, so it can be re-derived from the store at any time
    without a live fetch. It deliberately excludes the "absent from a healthy
    board" rule, which needs this run's fetch evidence and lives in
    `freshness_node`.

    It lives here (rather than in `freshness_node`) because it has TWO callers:
    `freshness_node`, for postings passing through the pipeline, and
    `store.sweep_ghosts`, which re-derives it for every stored `new`/`viewed`
    row. A row that has converged (country + score + refined reason) is never
    re-injected by `backfill_node`, so without that second caller a row could
    age past `JOB_MAX_AGE_DAYS` and never be flagged — the audited symptom of a
    row reading "🕒 90d ago" with no stale badge.

    `age_days` is read from the posting when already computed (freshness sets
    it) and derived from `posted_at` otherwise, so both callers agree.
    """
    age = posting.get("age_days")
    if age is None:
        age = age_days(posting.get("posted_at") or "")
    if age is not None and age > config.JOB_MAX_AGE_DAYS:
        return f"stale ({age}d old)"

    deadline = (posting.get("deadline") or "")[:10]
    if deadline:
        try:
            if dt.date.fromisoformat(deadline) < dt.date.today():
                return f"deadline passed ({deadline})"
        except ValueError:
            pass

    if posting.get("listed") is False:  # Ashby-only signal; absent elsewhere
        return "delisted by source"

    return ""


# Canonical-location normalization. Deterministic rules that collapse the
# "Austin / Remote / United States = 3 listings" duplication problem and unify
# common metro aliases so cross-source dedup can key on location.
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_LOCATION_ALIASES = {
    "nyc": "New York, NY",
    "new york city": "New York, NY",
    "manhattan": "New York, NY",
    "sf": "San Francisco, CA",
    "san francisco bay area": "San Francisco, CA",
    "bay area": "San Francisco, CA",
    "the bay area": "San Francisco, CA",
    "united states": "US",
    "usa": "US",
    "u.s.": "US",
}


def canonical_location(location: str) -> str:
    """Normalize a free-form location into a canonical, dedup-friendly string.

    Remote roles collapse to "Remote" (dropping the trailing region), and common
    metro aliases map to a single canonical form. Unknown values are just
    whitespace/lowercase-normalized so equal strings compare equal.
    """
    loc = (location or "").strip()
    if not loc or loc == "—":
        return ""
    if _REMOTE_RE.search(loc):
        return "Remote"
    key = loc.lower().strip(" ,.")
    if key in _LOCATION_ALIASES:
        return _LOCATION_ALIASES[key]
    return loc
