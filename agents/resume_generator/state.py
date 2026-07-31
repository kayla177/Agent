"""Shared state for the resume-generator graph.

Linear chain: gather -> research -> keywords -> draft -> save. `gather` seeds the
job record + experience pool; each later node reads what it needs and writes its
own key. If `gather` can't proceed (no job id, unknown job, or empty experience
pool) it sets `error`, and every downstream node short-circuits on it — so a
missing prerequisite yields a clear message instead of a fabricated resume.
"""

from __future__ import annotations

from typing import TypedDict


class ResumeState(TypedDict, total=False):
    # Input: the scraped job's id (from the jobs store).
    job_id: str

    # gather: the full job record + the concatenated experience-pool text.
    job: dict
    experience: str

    # gather: the user's master résumé LaTeX (their template), if set. The
    # latexify node tailors this per job; empty string means no template yet.
    master_latex: str

    # research: plain text pulled from the posting URL + company page ("" on fail).
    company_research: str

    # keywords: ATS keywords/skills extracted from the JD (+ research).
    keywords: list[str]

    # draft: the tailored resume, Markdown.
    markdown: str

    # latexify: the tailored resume as LaTeX (their template), compile-verified.
    # Falls back to the master .tex verbatim if tailoring won't compile.
    latex: str

    # save: human-facing summary (this is the registry/CLI output_key).
    message: str

    # Set by gather when a prerequisite is missing; halts the pipeline cleanly.
    error: str

    # Non-fatal problems (e.g. research fetch failed), surfaced but never raised.
    warnings: list[str]
