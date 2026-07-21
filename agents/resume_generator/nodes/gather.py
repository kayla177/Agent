"""Gather node — load the target job record and the user's experience pool.

This is the pipeline's fail-fast gate. It refuses to proceed (setting `error`,
which every downstream node honors) when there is no job id, the id is unknown,
or the experience pool is empty — because a resume with no real experience to
draw on could only be fabricated, which this feature explicitly won't do.
"""

from __future__ import annotations

from agents.job_scraper import store as jobstore
from agents.resume_generator import store as resume_store
from agents.resume_generator.state import ResumeState


def gather_node(state: ResumeState) -> ResumeState:
    job_id = (state.get("job_id") or "").strip()
    if not job_id:
        return {
            "error": "no_job",
            "message": (
                "Resume generation is per-job. Trigger it from a specific job "
                "(pass --job-id on the CLI)."
            ),
        }

    job = jobstore.load_records().get(job_id)
    if job is None:
        return {
            "error": "no_job",
            "message": f"No scraped job found for id '{job_id}'.",
        }

    experience = resume_store.load_experience_text()
    if not experience:
        return {
            "error": "no_experience",
            "message": (
                "Your experience pool is empty. Upload your resume and past "
                "projects first (CLI: --add-experience <file>)."
            ),
        }

    return {"job": job, "experience": experience}
