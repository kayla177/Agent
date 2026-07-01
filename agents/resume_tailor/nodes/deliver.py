"""Deliver node — ping Discord that a draft is ready (not the whole document).

A full tailored résumé + cover letter is long and better read in the web UI, so
we send only a short notification when send=True.
"""

from __future__ import annotations

from agents.resume_tailor.state import TailorState
from shell.discord_client import send_message


def deliver_node(state: TailorState) -> TailorState:
    if state.get("error"):
        return {}
    company = state.get("company") or "a role"
    role = state.get("role") or ""
    note = f"📄 Tailored résumé + cover letter ready for **{role} @ {company}** — review in the control center."
    try:
        send_message(note)
    except Exception as exc:
        print(f"⚠️ Discord delivery failed: {exc}")
    return {}
