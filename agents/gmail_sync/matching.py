"""Pure heuristics for matching recruiter emails to applications.

No I/O — every function here is deterministic and unit-tested. The Gmail node
uses these to decide which application (if any) an email refers to and what
status it implies. Kept intentionally conservative: only advance the pipeline
or mark a rejection, and always flag the change as auto-detected.
"""

from __future__ import annotations

import re

# Detection keywords, most-significant first. A rejection signal outranks an
# offer, which outranks an interview (a later rejection overrides earlier news).
_STATUS_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("rejected", (
        "unfortunately", "not moving forward", "not be moving forward",
        "decided not to", "other candidates", "will not be proceeding",
        "regret to inform", "unable to offer", "not to move forward",
        "won't be moving", "no longer under consideration",
    )),
    ("offer", (
        "pleased to offer", "offer of employment", "excited to offer",
        "job offer", "extend an offer", "offer letter",
    )),
    ("interview", (
        "interview", "schedule a call", "schedule a time", "next steps",
        "meet the team", "phone screen", "technical screen", "set up a call",
        "availability", "hiring manager", "coding challenge", "assessment",
    )),
]

_DETECT_RANK = {"interview": 1, "offer": 2, "rejected": 3}
# Pipeline progression (rejected handled specially in should_apply).
_PIPELINE_RANK = {"applied": 0, "interview": 1, "offer": 2, "accepted": 3}


def normalize_company(s: str) -> str:
    """Lowercase, drop common suffixes/noise, keep only [a-z0-9]."""
    n = (s or "").lower()
    n = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|technologies|the)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def company_matches(app_company: str, haystack: str) -> bool:
    """True if the (normalized) application company appears in the haystack
    (sender name + subject + snippet, normalized). Guards against 1-2 char
    company keys that would match noise."""
    key = normalize_company(app_company)
    if len(key) < 3:
        return False
    return key in normalize_company(haystack)


def infer_status(text: str) -> str | None:
    """Most-significant status implied by the text, or None."""
    t = (text or "").lower()
    for status, kws in _STATUS_KEYWORDS:
        if any(k in t for k in kws):
            return status
    return None


def rank_status(status: str) -> int:
    """Detection significance (higher = more significant signal)."""
    return _DETECT_RANK.get(status, 0)


def should_apply(current: str, detected: str) -> bool:
    """Apply a detected status only if it ADVANCES the pipeline or is a fresh
    rejection — never regress, and never revive a rejected application."""
    if current == "rejected":
        return False  # terminal: Gmail never changes a rejected application
    if detected == "rejected":
        return True
    if detected in _PIPELINE_RANK and current in _PIPELINE_RANK:
        return _PIPELINE_RANK[detected] > _PIPELINE_RANK[current]
    return detected != current
