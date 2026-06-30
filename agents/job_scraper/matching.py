"""Title and location matching helpers (deterministic, no LLM).

Keyword matching uses word boundaries so "intern" matches "Software Engineer
Intern" but NOT "Internal Audit" / "Database Engine Internals" — a common
false-positive trap with naive substring checks.
"""

from __future__ import annotations

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
