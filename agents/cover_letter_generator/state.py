"""Shared state for the cover-letter graph."""

from __future__ import annotations

from typing import TypedDict


class CoverLetterState(TypedDict, total=False):
    #: Input: which scraped job to write about.
    job_id: str
    #: The `jobs` record for `job_id`.
    job: dict
    #: The master letter, as the voice to imitate.
    master: str
    #: The tailored résumé's markdown for this job, or "" — context, not required.
    resume_body: str
    #: The applicant profile.
    profile: dict
    #: The drafted letter, plain text.
    body: str
    #: Set by any node that refuses; every later node returns early on it.
    error: str
    #: Human-facing summary, surfaced by the registry and the CLI.
    message: str
