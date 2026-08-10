"""Draft node — the only node that calls the model.

Writes ONE letter in the user's own voice, imitating her master letter's
structure and rhythm. Deliberately no AI-drafted marker: she reads the letter in
the résumé tab before it goes anywhere, and a marker she must delete every time
is friction with no safety value. Phase 3 MUST add one when the applier starts
pasting letters into live forms.
"""

from __future__ import annotations

from agents.cover_letter_generator.state import CoverLetterState
from shell.model_router import llm

_MAX_TOKENS = 1200

_SYSTEM = (
    "You write a cover letter for a specific job, in the applicant's own voice.\n\n"
    "ABSOLUTE RULES:\n"
    "- Imitate the VOICE, STRUCTURE and PARAGRAPH RHYTHM of the sample letter you "
    "are given. It is how this person writes; match it.\n"
    "- Use ONLY facts present in the sample letter, the résumé excerpt, or the "
    "profile. Never invent an employer, title, date, school, degree, metric or "
    "technology. If you do not know something, leave it out.\n"
    "- Mirror the posting's wording only where it truthfully describes real "
    "experience.\n"
    "- Output ONLY the letter body as plain text. No markdown, no headings, no "
    "bullet points, no commentary, no placeholders like [Company].\n"
    "- Keep it to three or four short paragraphs."
)


def _prompt(job: dict, master: str, resume_body: str, profile: dict) -> str:
    lines = [
        f"TARGET ROLE: {job.get('title', '?')} at {job.get('company', '?')}",
        f"LOCATION: {job.get('location', '?')}",
        "",
        "POSTING:",
        str(job.get("description") or "(no description captured)")[:2000],
        "",
        f"APPLICANT: {profile.get('full_name', '')} — {profile.get('degree', '')}, "
        f"{profile.get('school', '')} (graduating {profile.get('grad_date', '')})",
    ]
    if resume_body:
        lines += [
            "",
            "RÉSUMÉ FOR THIS ROLE (do not contradict it, do not exceed it):",
            resume_body[:2000],
        ]
    lines += [
        "",
        "SAMPLE LETTER — imitate this voice and structure:",
        master,
        "",
        "Write the letter now.",
    ]
    return "\n".join(lines)


def draft_node(state: CoverLetterState) -> CoverLetterState:
    if state.get("error"):
        return {}

    try:
        raw = llm(
            "local",
            _prompt(
                state.get("job") or {},
                state.get("master", ""),
                state.get("resume_body", ""),
                state.get("profile") or {},
            ),
            system=_SYSTEM,
            temperature=0.4,
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — model unavailable / transport error
        return {
            "error": "draft_failed",
            "message": f"The cover letter could not be drafted ({type(exc).__name__}: {exc}).",
        }

    body = raw or ""
    if not body.strip():
        return {
            "error": "draft_failed",
            "message": "The model returned an empty letter; nothing was saved.",
        }
    return {"body": body}
