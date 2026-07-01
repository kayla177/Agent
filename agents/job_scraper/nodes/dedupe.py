"""Dedupe node — drop already-seen postings and merge same-role duplicates.

Three passes, in order:
  1. Seen-store: skip ids recorded on a previous run (persisted in notify).
  2. In-batch id dedup: a posting can appear twice within one board.
  3. Cross-source dedup: the SAME role listed on two boards (e.g. a company on
     both Greenhouse and Lever) is collapsed to one canonical record, keyed on
     (normalized company, normalized title, canonical location). Duplicates are
     kept out of `new` and their ATS is noted on the survivor's `also_on`.

We only READ the seen-store here. New ids are persisted in the notify node,
AFTER a successful pass, so a crash mid-run doesn't silently mark roles as seen
without the user ever being told about them.
"""

from __future__ import annotations

import re

from agents.job_scraper.matching import canonical_location
from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import load_seen

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Lowercase + strip non-alphanumerics for a stable comparison key."""
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


def _role_key(p: dict) -> tuple[str, str, str]:
    return (
        _norm(p.get("company", "")),
        _norm(p.get("title", "")),
        _norm(canonical_location(p.get("location", ""))),
    )


def dedupe_node(state: JobScraperState) -> JobScraperState:
    seen = load_seen()
    filtered = state.get("filtered", [])

    # Passes 1 & 2: skip previously-seen and within-batch duplicate ids.
    fresh: list[dict] = []
    batch_ids: set[str] = set()
    for p in filtered:
        pid = p.get("id", "")
        if pid in seen or pid in batch_ids:
            continue
        batch_ids.add(pid)
        fresh.append(p)

    # Pass 3: collapse the same role listed across multiple ATS boards.
    canonical: dict[tuple[str, str, str], dict] = {}
    new: list[dict] = []
    for p in fresh:
        key = _role_key(p)
        survivor = canonical.get(key)
        if survivor is None:
            p = {**p, "also_on": [], "dup_of": None}
            canonical[key] = p
            new.append(p)
        else:
            # Merge: note the duplicate's ATS on the survivor, drop the dup.
            ats = p.get("ats", "")
            if ats and ats not in survivor["also_on"] and ats != survivor.get("ats"):
                survivor["also_on"].append(ats)

    return {"new": new}
