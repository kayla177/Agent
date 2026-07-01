"""Rank node — score each new role's fit against the user's profile (LLM).

Uses the platform's local model (via ``shell/model_router.llm``) to attach a
``fit_score`` (0–100) and a one-line ``fit_reason`` to every posting, judged
against ``config.JOB_PROFILE`` (free text the user sets in settings).

Design choices that keep this safe and cheap:
  - Batched: several postings per model call (title + location + a short slice
    of the description) so one prompt scores many roles.
  - Graceful degradation: if the profile is unset, or the model / JSON parse
    fails, postings keep ``fit_score=None`` and the run continues — matching the
    fetch and notify nodes, which never let a subsystem failure kill the run.
  - Optional filtering: ``config.JOB_MIN_FIT`` drops roles that score below the
    threshold (0 = keep everything). Unscored roles are always kept.
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
    "You are a job-fit scorer. Given a candidate profile and a list of roles, "
    "rate each role's fit for THIS candidate from 0 (irrelevant) to 100 (ideal). "
    "Reply with ONLY a JSON array of objects {\"i\": <index>, \"score\": <0-100>, "
    "\"reason\": \"<=12 words\"}. No prose, no code fences."
)


def _prompt(profile: str, batch: list[dict]) -> str:
    lines = [f"CANDIDATE PROFILE:\n{profile}\n", "ROLES:"]
    for i, p in enumerate(batch):
        desc = (p.get("description") or "")[:_DESC_SLICE].replace("\n", " ")
        lines.append(
            f"[{i}] {p.get('title', '?')} @ {p.get('company', '?')} "
            f"({p.get('location', '—')})\n{desc}"
        )
    return "\n".join(lines)


def _parse(reply: str, size: int) -> dict[int, dict]:
    """Extract {index: {score, reason}} from the model reply; {} on failure."""
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
            score = max(0, min(100, int(it["score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < size:
            out[idx] = {"score": score, "reason": str(it.get("reason", "")).strip()}
    return out


def _score_batch(profile: str, batch: list[dict]) -> None:
    """Attach fit_score / fit_reason to a batch in place (best-effort)."""
    try:
        reply = llm("local", _prompt(profile, batch), system=_SYSTEM, temperature=0.2)
    except Exception as exc:  # model unavailable / transport error
        for p in batch:
            p.setdefault("fit_score", None)
            p.setdefault("fit_reason", f"unranked ({exc})")
        return
    parsed = _parse(reply, len(batch))
    for i, p in enumerate(batch):
        hit = parsed.get(i)
        p["fit_score"] = hit["score"] if hit else None
        p["fit_reason"] = hit["reason"] if hit else "unranked"


def rank_node(state: JobScraperState) -> JobScraperState:
    new = [dict(p) for p in state.get("new", [])]
    profile = (config.JOB_PROFILE or "").strip()

    if not new:
        return {"new": new}

    if not profile:
        # No profile configured — skip ranking but keep the pipeline intact.
        for p in new:
            p["fit_score"] = None
            p["fit_reason"] = "no profile set"
        return {"new": new}

    for start in range(0, len(new), _BATCH):
        _score_batch(profile, new[start : start + _BATCH])

    # Optional threshold filter (never drops unscored roles).
    if config.JOB_MIN_FIT > 0:
        new = [
            p for p in new
            if p.get("fit_score") is None or p["fit_score"] >= config.JOB_MIN_FIT
        ]

    # Highest fit first; unscored (None) sink to the bottom.
    new.sort(key=lambda p: (p.get("fit_score") is not None, p.get("fit_score") or 0), reverse=True)
    return {"new": new}
