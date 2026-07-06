"""Deliver node — post the trader report to Discord (only wired when send=True).
Mirrors the other agents' deliver pattern; failures are surfaced, never raised."""

from __future__ import annotations

from agents.paper_trader.state import TraderState
from shell.discord_client import send_message


def deliver_node(state: TraderState) -> TraderState:
    message = state.get("message", "")
    if not message:
        return {}
    try:
        send_message(message)
    except Exception as exc:
        print(f"⚠️ Discord delivery failed: {exc}")
    return {}
