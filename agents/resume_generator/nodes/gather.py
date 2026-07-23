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

    # Grounding text = the master resume (if set) + every experience-pool doc.
    # Either source alone is enough to draft from; we only fail if both are empty.
    parts: list[str] = []
    master = resume_store.get_master_resume()
    if master.get("markdown", "").strip():
        parts.append(f"### MASTER RESUME\n{master['markdown'].strip()}")
    pool = resume_store.load_experience_text()
    if pool:
        parts.append(pool)
    experience = "\n\n".join(parts).strip()

    if not experience:
        return {
            "error": "no_experience",
            "message": (
                "You have no experience to draw on yet. Add a master résumé, or "
                "upload your resume and past projects to the experience pool first "
                "(CLI: --add-experience <file>)."
            ),
        }

    return {"job": job, "experience": experience}
