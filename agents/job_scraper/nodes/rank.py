"""Rank node — undergrad-eligibility screen + optional fit score (LLM).

Uses the platform's local model (via ``shell/model_router.llm``) to, for every
posting, judge:
  - ``eligible``: can an UNDERGRAD (bachelor's, ~0-1 yrs exp) realistically apply?
    False only when the role clearly requires a Master's/PhD or senior experience
    (read from the title + a slice of the JD). Roles judged ineligible are dropped.
  - ``fit_score`` (0–100) + ``fit_reason`` against ``config.JOB_PROFILE`` — only
    when a profile is set; otherwise the score stays ``None``.

Design choices that keep this safe and cheap:
  - Batched: several postings per model call so one prompt handles many roles.
  - Runs even with no profile — eligibility is independent of the profile.
  - Graceful degradation: on any model / JSON-parse failure a posting keeps
    ``eligible=True`` (never over-drop) and ``fit_score=None``; the deterministic
    title-exclusion in filter.py is the safety net.
  - Optional ``config.JOB_MIN_FIT`` drops roles scoring below it (0 = keep all,
    unscored roles are always kept).
"""

from __future__ import annotations

import json
import re

import config
from agents.job_scraper.state import JobScraperState
from shell.model_router import llm

_BATCH = 5
_DESC_SLICE = 500
_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)

_SYSTEM = (
    "You screen roles for an UNDERGRADUATE student (bachelor's, ~0-1 years "
    "experience) seeking intern / co-op / new-grad software, ML, and data roles. "
    "For each role set \"eligible\": true, UNLESS it clearly requires a Master's/PhD "
    "or senior (2+ years) experience — then false. If a candidate profile is given, "
    "also give a 0-100 fit \"score\" (else null). Reply with ONLY a JSON array of "
    "{\"i\": <index>, \"eligible\": <true|false>, \"score\": <0-100 or null>, "
    "\"reason\": \"<=12 words\"}. No prose, no code fences."
)


def _prompt(profile: str, batch: list[dict]) -> str:
    head = (
        f"CANDIDATE PROFILE:\n{profile}\n"
        if profile
        else "No candidate profile provided — set every score to null.\n"
    )
    lines = [head, "ROLES:"]
    for i, p in enumerate(batch):
        desc = (p.get("description") or "")[:_DESC_SLICE].replace("\n", " ")
        lines.append(
            f"[{i}] {p.get('title', '?')} @ {p.get('company', '?')} "
            f"({p.get('location', '—')})\n{desc}"
        )
    return "\n".join(lines)


def _parse(reply: str, size: int) -> dict[int, dict]:
    """Extract {index: {eligible, score, reason}} from the reply; {} on failure."""
    match = _JSON_RE.search(reply or "")
    if not match:
        return {}
    try:
        items = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    out: dict[int, dict] = {}
    for it in items if isinstance(items, list) else []:
        try:
            idx = int(it["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < size):
            continue
        # Default eligible=True unless the model explicitly says false.
        eligible = it.get("eligible", True) is not False
        raw = it.get("score", None)
        try:
            score = None if raw is None else max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            score = None
        out[idx] = {"eligible": eligible, "score": score, "reason": str(it.get("reason", "")).strip()}
    return out


def _score_batch(profile: str, batch: list[dict]) -> None:
    """Attach eligible / fit_score / fit_reason to a batch in place (best-effort)."""
    try:
        reply = llm("local", _prompt(profile, batch), system=_SYSTEM, temperature=0.2)
    except Exception as exc:  # model unavailable / transport error → keep everything
        for p in batch:
            p.setdefault("eligible", True)
            p.setdefault("fit_score", None)
            p.setdefault("fit_reason", f"unranked ({exc})")
        return
    parsed = _parse(reply, len(batch))
    for i, p in enumerate(batch):
        hit = parsed.get(i)
        p["eligible"] = hit["eligible"] if hit else True
        p["fit_score"] = hit["score"] if hit else None
        p["fit_reason"] = (hit["reason"] if hit and hit["reason"] else ("unranked" if profile else "no profile set"))


def rank_node(state: JobScraperState) -> JobScraperState:
    new = [dict(p) for p in state.get("new", [])]
    if not new:
        return {"new": new}

    profile = (config.JOB_PROFILE or "").strip()

    # Run the model on every scrape — even without a profile — for the eligibility
    # judgment (and fit score when a profile exists).
    for start in range(0, len(new), _BATCH):
        _score_batch(profile, new[start : start + _BATCH])

    # Drop roles the model judged not undergrad-eligible (grad-only / senior).
    new = [p for p in new if p.get("eligible", True)]

    # Optional fit threshold (never drops unscored roles).
    if config.JOB_MIN_FIT > 0:
        new = [p for p in new if p.get("fit_score") is None or p["fit_score"] >= config.JOB_MIN_FIT]

    # Highest fit first; unscored (None) sink to the bottom.
    new.sort(key=lambda p: (p.get("fit_score") is not None, p.get("fit_score") or 0), reverse=True)
    return {"new": new}
