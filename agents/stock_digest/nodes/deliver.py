"""Deliver node — push the assembled digest to Discord."""

from __future__ import annotations

from agents.stock_digest.state import StockDigestState
from shell.discord_client import send_message


def deliver_node(state: StockDigestState) -> StockDigestState:
    message = state.get("message", "")
    if not message:
        return {}
    try:
        send_message(message)
    except Exception as exc:
        # Don't crash the run on a delivery failure — surface it instead.
        print(f"⚠️ Discord delivery failed: {exc}")
    return {}
