"""Synthesize node — assemble section text into the final briefing message.

Facts (weather, commute, calendar, news) are placed deterministically so they
are never hallucinated. The local model only writes a short friendly greeting
line, where a mistake is harmless.
"""

from __future__ import annotations

import datetime as dt

from agents.morning_briefing.state import BriefingState
from shell.model_router import llm


def _greeting(sections: dict[str, str]) -> str:
    try:
        return llm(
            "local",
            "Write a SHORT, warm one-sentence good-morning greeting for a daily "
            "briefing. No quotes, no emoji, max 15 words.",
            max_tokens=60,
            temperature=0.7,
        )
    except Exception:
        return "Good morning! Here's your briefing."


def synthesize(state: BriefingState) -> str:
    today = dt.datetime.now().strftime("%A, %B %d")
    parts = [
        f"**🌅 Morning Briefing — {today}**",
        _greeting(state),
        "",
        f"**Weather**\n{state.get('weather', 'n/a')}",
        f"**Commute**\n{state.get('commute', 'n/a')}",
        f"**Calendar**\n{state.get('calendar', 'n/a')}",
        f"**Catch-up**\n{state.get('news', 'n/a')}",
    ]
    return "\n\n".join(parts)


def synthesize_node(state: BriefingState) -> BriefingState:
    return {"message": synthesize(state)}
