"""Draft node — write the tailored resume in Markdown (LLM).

This is where truthfulness is enforced. The system prompt hard-constrains the
model to the facts present in the user's experience pool: it may reorder,
reframe, and surface real experience to match the role and weave in the targeted
ATS keywords, but it must NOT invent employers, titles, dates, degrees, or
metrics. Keywords it can't ground in real experience go into an explicit
"Keyword gaps" note at the end instead of being fabricated into bullet points.

On model failure the node sets `error` so the save node writes nothing — better
no resume than a broken or empty one.
"""

from __future__ import annotations

from agents.resume_generator.state import ResumeState
from shell.model_router import llm

_MAX_TOKENS = 2048

_SYSTEM = (
    "You are a resume writer. You tailor a candidate's REAL experience to a "
    "specific job so it passes that company's applicant-tracking system (ATS).\n\n"
    "ABSOLUTE RULES:\n"
    "- Use ONLY facts found in the candidate's experience pool. Never invent or "
    "embellish employers, job titles, dates, schools, degrees, certifications, "
    "or metrics. If a number isn't in the pool, don't state a number.\n"
    "- You MAY reorder, regroup, rephrase, and emphasize real experience, and "
    "mirror the posting's wording where it truthfully describes what the "
    "candidate actually did.\n"
    "- Weave in the target ATS keywords ONLY where they truthfully apply to real "
    "experience.\n"
    "- For target keywords you cannot ground in the pool, do NOT fabricate them "
    "into the body. Instead list them under a final '## Keyword gaps' section as "
    "honest gaps to consider addressing.\n\n"
    "OUTPUT: a complete resume in clean Markdown (name/contact if present in the "
    "pool, then Summary, Skills, Experience, Projects, Education as supported by "
    "the pool), followed by the '## Keyword gaps' section. No commentary before "
    "or after the resume."
)


def _prompt(job: dict, experience: str, keywords: list[str], research: str) -> str:
    kw = ", ".join(keywords) if keywords else "(none extracted)"
    lines = [
        f"TARGET ROLE: {job.get('title', '?')} @ {job.get('company', '?')}",
        f"LOCATION: {job.get('location', '—')}",
        "",
        f"TARGET ATS KEYWORDS: {kw}",
    ]
    if research:
        lines += ["", "COMPANY / POSTING CONTEXT (for tone & priorities):", research[:1500]]
    lines += [
        "",
        "CANDIDATE EXPERIENCE POOL (the ONLY source of facts):",
        experience,
        "",
        "Write the tailored resume now.",
    ]
    return "\n".join(lines)


def draft_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}

    job = state.get("job") or {}
    try:
        markdown = llm(
            "local",
            _prompt(
                job,
                state.get("experience", ""),
                state.get("keywords", []),
                state.get("company_research", ""),
            ),
            system=_SYSTEM,
            temperature=0.3,
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:  # model unavailable / transport error
        return {
            "error": "draft_failed",
            "message": f"Resume drafting failed (model error: {exc}).",
        }

    markdown = (markdown or "").strip()
    if not markdown:
        return {"error": "draft_failed", "message": "Model returned an empty draft."}

    return {"markdown": markdown}
