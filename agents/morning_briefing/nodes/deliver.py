"""Deliver node — push the assembled briefing to Discord."""

from __future__ import annotations

from agents.morning_briefing.state import BriefingState
from shell.discord_client import send_message


def deliver_node(state: BriefingState) -> BriefingState:
    message = state.get("message", "")
    if not message:
        return {}
    try:
        send_message(message)
    except Exception as exc:
        # Don't crash the run on a delivery failure — surface it instead.
        print(f"⚠️ Discord delivery failed: {exc}")
    return {}
