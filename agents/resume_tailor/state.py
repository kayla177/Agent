"""Shared state for the résumé-tailor graph."""

from __future__ import annotations

from typing import TypedDict


class TailorState(TypedDict, total=False):
    # Inputs (provided when the graph is invoked)
    job_description: str
    company: str
    role: str
    # Loaded by the `load` node
    base_resume: str
    model_used: str          # "smart" or "local" — surfaced to the UI
    # Produced by the tailor / cover nodes
    tailored_resume: str
    cover_letter: str
    # Final assembled markdown (registry output_key) + a short Discord ping
    message: str
    error: str
