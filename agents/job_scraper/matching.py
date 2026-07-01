"""Title and location matching helpers (deterministic, no LLM).

Keyword matching uses word boundaries so "intern" matches "Software Engineer
Intern" but NOT "Internal Audit" / "Database Engine Internals" — a common
false-positive trap with naive substring checks.
"""

from __future__ import annotations

import datetime as dt
import re

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

# Locations the user prefers (soft filter — does NOT exclude by default).
_PREFERRED_LOCATION_RE = re.compile(
    r"\b(canada|waterloo|toronto|ontario|remote)\b",
    re.IGNORECASE,
)


def is_target_role(title: str) -> bool:
    """True if the title looks like a co-op / intern / new-grad role."""
    return bool(_ROLE_RE.search(title or ""))


def is_preferred_location(location: str) -> bool:
    """True if the location matches the preferred set (Canada/Waterloo/etc.)."""
    return bool(_PREFERRED_LOCATION_RE.search(location or ""))


def age_days(posted_at: str) -> int | None:
    """Whole days between an ISO date string and today; None if unparseable."""
    try:
        d = dt.date.fromisoformat((posted_at or "")[:10])
    except (ValueError, TypeError):
        return None
    return (dt.date.today() - d).days


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
