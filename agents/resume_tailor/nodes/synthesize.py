"""Synthesize node — assemble the tailored résumé + cover letter into one doc."""

from __future__ import annotations

from agents.resume_tailor.state import TailorState

_MODEL_LABEL = {"smart": "hosted (Claude)", "local": "local model (set ANTHROPIC_API_KEY for higher quality)"}


def synthesize_node(state: TailorState) -> TailorState:
    if state.get("error"):
        return {"message": f"⚠️ {state['error']}"}

    company = state.get("company") or "the company"
    role = state.get("role") or "the role"
    model = _MODEL_LABEL.get(state.get("model_used", "local"), state.get("model_used", ""))

    parts = [
        f"# Tailored application — {role} @ {company}",
        f"_Drafted with {model}. Review before sending — no auto-submit._",
        "\n## Tailored résumé\n",
        state.get("tailored_resume", "(none)"),
        "\n## Cover letter\n",
        state.get("cover_letter", "(none)"),
    ]
    return {"message": "\n".join(parts)}
