"""Gather node — the fail-fast gate.

Three refusals, and the third is the one that matters: without a master letter
there is no voice to imitate, and a letter invented from nothing is exactly what
this feature must not produce. Every later node returns early on `error`.
"""

from __future__ import annotations

import profile_store
from agents.cover_letter_generator import store as cl_store
from agents.cover_letter_generator.state import CoverLetterState
from agents.job_scraper import store as jobstore
from agents.resume_generator import store as resume_store


def gather_node(state: CoverLetterState) -> CoverLetterState:
    job_id = (state.get("job_id") or "").strip()
    if not job_id:
        return {
            "error": "no_job",
            "message": (
                "Cover letters are written per job. Trigger this from a specific "
                "job on the résumé tab."
            ),
        }

    job = jobstore.load_records().get(job_id)
    if not job:
        return {
            "error": "no_job",
            "message": f"No scraped job found for id '{job_id}'.",
        }

    master = (cl_store.get_master_cover_letter().get("body") or "").strip()
    if not master:
        return {
            "error": "no_master",
            "message": (
                "There is no master cover letter to work from yet. Write one you are "
                "happy with on the résumé tab first — each draft imitates its voice "
                "and structure, so without it a letter could only be invented."
            ),
        }

    # The tailored résumé is CONTEXT, not a prerequisite: it stops the letter
    # claiming experience the résumé does not show. Absent is fine.
    resume = resume_store.get_resume(job_id) or {}

    return {
        "job": job,
        "master": master,
        "resume_body": str(resume.get("markdown") or ""),
        "profile": profile_store.get_profile(),
    }
