"""Synthesize node — assemble the tracker message from its sections."""

from __future__ import annotations

import datetime as dt

from agents.application_tracker.state import TrackerState


def synthesize_node(state: TrackerState) -> TrackerState:
    today = dt.datetime.now().strftime("%A, %B %d")
    parts = [f"**📋 Application Tracker — {today}**", state.get("summary_text", "")]

    followups = state.get("followups_text", "")
    if followups:
        parts.append(followups)
    elif state.get("apps"):
        parts.append("Nothing needs follow-up right now. 👍")

    return {"message": "\n\n".join(p for p in parts if p)}
