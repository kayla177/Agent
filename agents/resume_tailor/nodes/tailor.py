"""Tailor node — rewrite the base résumé for the target job.

Grounding rule baked into the prompt: only reuse facts from the base résumé —
never invent experience. The model may re-order, re-emphasize, and reword to
match the job description, but not fabricate.
"""

from __future__ import annotations

from agents.resume_tailor.state import TailorState
from shell.model_router import llm

_SYSTEM = (
    "You are an expert technical résumé editor. Tailor the candidate's résumé to "
    "the target job. STRICT RULES: use ONLY facts present in the base résumé — do "
    "not invent employers, dates, titles, metrics, or skills. You may reorder, "
    "re-emphasize, and reword bullet points to mirror the job's language and "
    "priorities. Keep it truthful, concise, and one page. Output clean markdown only."
)


def tailor_node(state: TailorState) -> TailorState:
    if state.get("error"):
        return {}
    prompt = (
        f"TARGET ROLE: {state.get('role','(unspecified)')} at "
        f"{state.get('company','(unspecified)')}\n\n"
        f"JOB DESCRIPTION:\n{state['job_description']}\n\n"
        f"BASE RÉSUMÉ (the only source of truth for facts):\n{state['base_resume']}\n\n"
        "Return the tailored résumé in markdown."
    )
    try:
        text = llm(
            state.get("model_used", "local"),
            prompt,
            system=_SYSTEM,
            max_tokens=2000,
            temperature=0.4,
        )
        return {"tailored_resume": text}
    except Exception as exc:
        return {"error": f"Résumé tailoring failed: {exc}"}
