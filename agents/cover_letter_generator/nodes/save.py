"""Save node — persist the letter and build the run's summary.

If an earlier node set `error`, this saves NOTHING and passes that node's message
through untouched: a refusal must not leave a row behind.
"""

from __future__ import annotations

from agents.cover_letter_generator import store as cl_store
from agents.cover_letter_generator.state import CoverLetterState


def save_node(state: CoverLetterState) -> CoverLetterState:
    if state.get("error"):
        return {}  # message already set by the refusing node

    job = state.get("job") or {}
    cl_store.upsert_cover_letter(
        state.get("job_id", ""),
        company=job.get("company", ""),
        role=job.get("title", ""),
        body=state.get("body", ""),
        status="draft",
    )
    words = len(state.get("body", "").split())
    return {
        "message": (
            f"Drafted a cover letter for {job.get('title', '?')} at "
            f"{job.get('company', '?')} ({words} words, saved as draft). "
            f"Read it on the résumé tab before you use it."
        )
    }
