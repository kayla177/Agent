"""Cover-letter node — draft a short, specific cover letter for the target job."""

from __future__ import annotations

from agents.resume_tailor.state import TailorState
from shell.model_router import llm

_SYSTEM = (
    "You write concise, specific, non-generic cover letters (max ~250 words). "
    "Ground every claim in the candidate's real résumé — never invent experience. "
    "Connect the candidate's actual background to the job's needs. Warm but "
    "professional; no clichés or filler. Output clean markdown only."
)


def cover_node(state: TailorState) -> TailorState:
    if state.get("error"):
        return {}
    prompt = (
        f"TARGET ROLE: {state.get('role','(unspecified)')} at "
        f"{state.get('company','(unspecified)')}\n\n"
        f"JOB DESCRIPTION:\n{state['job_description']}\n\n"
        f"CANDIDATE RÉSUMÉ (only source of truth):\n{state['base_resume']}\n\n"
        "Write the cover letter in markdown."
    )
    try:
        text = llm(
            state.get("model_used", "local"),
            prompt,
            system=_SYSTEM,
            max_tokens=900,
            temperature=0.6,
        )
        return {"cover_letter": text}
    except Exception as exc:
        return {"error": f"Cover letter failed: {exc}"}
