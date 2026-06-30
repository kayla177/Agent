"""Deliver node — push the tracker digest to Discord (gated on send)."""

from __future__ import annotations

from agents.application_tracker.state import TrackerState
from shell.discord_client import send_message


def deliver_node(state: TrackerState) -> TrackerState:
    message = state.get("message", "")
    if not message:
        return {}
    try:
        send_message(message)
    except Exception as exc:
        # Don't crash the run on a delivery failure — surface it.
        print(f"⚠️ Discord delivery failed: {exc}")
    return {}
