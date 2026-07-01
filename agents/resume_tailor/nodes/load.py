"""Load node — pull the base résumé and pick the model role.

Validates that we have both a base résumé and a job description; on failure it
sets state['error'] so downstream nodes short-circuit into a clear message.
"""

from __future__ import annotations

from agents.resume_tailor.state import TailorState
from agents.resume_tailor.store import load_base_resume, smart_role


def load_node(state: TailorState) -> TailorState:
    base = load_base_resume()
    jd = (state.get("job_description") or "").strip()

    if not base:
        return {"error": "No base résumé found — add one at data/resume.md."}
    if not jd:
        return {"error": "No job description provided."}

    return {"base_resume": base, "model_used": smart_role()}
