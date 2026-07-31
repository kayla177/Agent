"""Deterministic fit scoring — the baseline that guarantees a score exists.

Measured on 2026-07-25: llama3.1:8b returned fit_score=null for 3 of 5 postings
even WITH a candidate profile set. Since null sorts to the bottom of sort-by-fit,
a partly-null column actively hides good roles. So every posting gets a cheap,
explainable keyword-overlap score here, and the LLM in rank.py only OVERRIDES it
when it returns a usable integer.

Matching uses negative lookarounds (not \b word boundaries) so that keywords
ending in non-word characters like C++ and C# still match. A keyword like "c++"
with trailing \b would only match if followed by a word character in the haystack,
which never happens in normal prose where a skill is followed by space, comma,
or period. Negative lookarounds work regardless of keyword edge characters while
still protecting against substring matches inside larger words (e.g., "r" in
"Research", "go" in "Going").
"""

from __future__ import annotations

import re

from agents.job_scraper.matching import is_target_role

# Words that are never useful as skill keywords.
_STOPWORDS = frozenset("""
a an and are as at be but by for from had has have i in into is it its of on or
that the to was were will with you your my me our we they this these those am
year years yr yrs student undergrad undergraduate currently seeking looking role
roles job jobs work working experience skills using use used strong good great
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*", re.IGNORECASE)

_NO_KEYWORDS_REASON = "no profile keywords set"
_MATCH_PREFIX = "matched: "

# Score shape: a floor so an unmatched posting is still comparable, most of the
# range driven by overlap, and a small bonus for an explicitly early-career title.
#
# Overlap SATURATES at _SATURATE matches rather than dividing by the keyword
# count. A real profile carries non-skill tokens ("waterloo", "us", "canada",
# "co", "op"), so hits/len(keywords) punishes a descriptive profile: measured on
# a realistic 13-keyword profile, a posting matching Python+React+SQL scored 49
# — mid-tier for what is plainly a strong match. Saturating puts that at 71 and a
# five-skill match at 95, which lands correctly across the hi/mid/lo tiers.
_FLOOR = 25
_OVERLAP_RANGE = 60
_TITLE_BONUS = 10
_SATURATE = 5


def extract_keywords(profile: str) -> list[str]:
    """Skill-ish terms from the free-text profile, lowercased and deduped."""
    seen: list[str] = []
    for raw in _TOKEN_RE.findall(profile or ""):
        term = raw.lower().strip(".")
        # Skip anything starting with a digit: "3rd-year" tokenizes to "3rd",
        # which is ordinal noise, not a skill. `term.isdigit()` alone misses it.
        if len(term) < 2 or term in _STOPWORDS or term[0].isdigit():
            continue
        if term not in seen:
            seen.append(term)
    return seen


def _matches(keywords: list[str], haystack: str) -> list[str]:
    r"""Keywords present in the haystack, word-boundary anchored with lookarounds.

    Uses (?<!\w) and (?!\w) instead of \b so that keywords ending in non-word
    characters (e.g., c++, c#) can match. \b requires a word↔non-word transition,
    so a keyword ending in + or # would only match if followed by a word character,
    which doesn't happen in normal prose (skills are followed by space/comma/period).
    Lookarounds assert "not preceded/followed by word char" regardless of the
    keyword's own edge characters, while still protecting against false positives
    like r in Research or go in Going.
    """
    hits: list[str] = []
    for kw in keywords:
        if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", haystack, re.IGNORECASE):
            hits.append(kw)
    return hits


def score_baseline(keywords: list[str], posting: dict) -> tuple[int, str]:
    """Return (0-100 score, explainable reason) for one posting.

    With no keywords there is nothing to compare, so every posting scores a
    neutral 50 — never null, which is the whole point of this module.
    """
    title = posting.get("title") or ""
    if not keywords:
        return 50, _NO_KEYWORDS_REASON

    haystack = f"{title}\n{posting.get('description') or ''}"
    hits = _matches(keywords, haystack)

    score = _FLOOR + round(_OVERLAP_RANGE * min(1.0, len(hits) / _SATURATE))
    if is_target_role(title):
        score += _TITLE_BONUS
    score = max(0, min(100, score))

    if hits:
        shown = ", ".join(h.title() if h.islower() else h for h in hits[:5])
        reason = f"{_MATCH_PREFIX}{shown}"
    else:
        reason = f"{_MATCH_PREFIX}none"
    return score, reason


def is_baseline_reason(reason: str) -> bool:
    """True if `reason` was written by this module rather than the LLM.

    rank.py overwrites fit_reason when the model returns a usable score, so a
    still-baseline reason marks a row that has not been LLM-refined yet. The
    backfill node selects on exactly this.

    Prefix-tolerant (not exact-match) on BOTH markers: rank.py's exception path
    appends a short suffix (e.g. " (unrefined)") to whatever baseline reason was
    already set, and that round-trip must still read as a baseline reason —
    otherwise a row scored while the model was unavailable and no profile was
    configured is permanently misclassified as "already LLM-refined" and never
    retried by the backfill node.
    """
    r = (reason or "").strip()
    return r.startswith(_MATCH_PREFIX) or r.startswith(_NO_KEYWORDS_REASON)
