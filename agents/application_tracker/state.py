"""Shared state for the application-tracker graph."""

from __future__ import annotations

from typing import TypedDict


class TrackerState(TypedDict, total=False):
    apps: list[dict]          # all applications, loaded once by the summary node
    summary_text: str         # pipeline counts section
    followups_text: str       # stale follow-ups + upcoming interviews section
    message: str              # final assembled message (the registry output_key)
