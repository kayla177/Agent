"""Keywords node — extract the ATS keywords the resume should target (LLM).

An ATS matches a resume against the terms in the job posting, so the posting's
own text is the ground truth (not a Google-snippet trick). This asks the local
model for the concrete skills, tools, and phrases a screener would scan for,
drawing from the stored job description plus any company/posting text the
research node fetched.

Mirrors the rank node's contract: a JSON-only reply parsed with a regex + a
graceful fallback (an empty keyword list) so a model or parse failure lets the
pipeline continue to a still-useful, if un-optimized, draft.
"""

from __future__ import annotations

import json
import re

from agents.resume_generator.state import ResumeState
from shell.model_router import llm

_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)
_MAX_KEYWORDS = 25
_DESC_SLICE = 3000
_RESEARCH_SLICE = 2000

_SYSTEM = (
    "You are an ATS keyword extractor. Given a job posting, list the concrete "
    "skills, tools, technologies, certifications, and role-specific phrases an "
    "applicant-tracking system would scan a resume for. Prefer terms stated in "
    "the posting. Reply with ONLY a JSON array of short strings (no objects, no "
    "prose, no code fences), most important first."
)


def _prompt(job: dict, research: str) -> str:
    desc = (job.get("description") or "")[:_DESC_SLICE]
    lines = [
        f"ROLE: {job.get('title', '?')} @ {job.get('company', '?')}",
        "",
        "JOB DESCRIPTION:",
        desc or "(none provided)",
    ]
    if research:
        lines += ["", "ADDITIONAL CONTEXT:", research[:_RESEARCH_SLICE]]
    return "\n".join(lines)


def _parse(reply: str) -> list[str]:
    match = _JSON_RE.search(reply or "")
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    seen: list[str] = []
    for it in items if isinstance(items, list) else []:
        kw = str(it).strip()
        if kw and kw.lower() not in {s.lower() for s in seen}:
            seen.append(kw)
    return seen[:_MAX_KEYWORDS]


def keywords_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}

    job = state.get("job") or {}
    warnings = list(state.get("warnings", []))
    try:
        reply = llm(
            "local",
            _prompt(job, state.get("company_research", "")),
            system=_SYSTEM,
            temperature=0.2,
        )
    except Exception as exc:  # model unavailable / transport error
        warnings.append(f"keywords: extraction failed ({exc})")
        return {"keywords": [], "warnings": warnings}

    return {"keywords": _parse(reply), "warnings": warnings}
