"""Save node — persist the drafted resume and assemble the run summary.

Writes the Markdown draft to the resumes table (keyed by job id, status=draft),
then builds the human-facing `message` that the CLI prints and the registry
surfaces. If an earlier node set `error`, it saves nothing and passes the
existing message through unchanged.
"""

from __future__ import annotations

from agents.resume_generator import store as resume_store
from agents.resume_generator.state import ResumeState


def save_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}  # message already set by the failing node

    job = state.get("job") or {}
    job_id = state.get("job_id", "")
    markdown = state.get("markdown", "")
    keywords = state.get("keywords", [])
    latex = state.get("latex")  # None when no master template -> keep any existing .tex

    resume_store.upsert_resume(
        job_id,
        company=job.get("company", ""),
        role=job.get("title", ""),
        markdown=markdown,
        latex=latex,
        keywords=keywords,
        status="draft",
    )

    warnings = state.get("warnings", [])
    lines = [
        f"Drafted resume for {job.get('title', '?')} @ {job.get('company', '?')} "
        f"(saved as draft).",
        f"Targeted {len(keywords)} ATS keyword(s)"
        + (f": {', '.join(keywords[:10])}" if keywords else "."),
    ]
    if warnings:
        lines.append(f"Notes: {'; '.join(warnings)}")
    return {"message": "\n".join(lines)}
