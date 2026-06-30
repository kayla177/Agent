"""Follow-ups node — flag stale applications and upcoming interviews.

A "stale" application is still in `applied` status and hasn't been touched in
FOLLOWUP_DAYS days — a nudge to follow up. Interviews are surfaced so they
aren't forgotten. Both read the apps already loaded by the summary node.
"""

from __future__ import annotations

from agents.application_tracker.state import TrackerState
from agents.application_tracker.store import days_since

FOLLOWUP_DAYS = 7


def followups_node(state: TrackerState) -> TrackerState:
    apps = state.get("apps", [])
    if not apps:
        return {"followups_text": ""}

    stale = [
        a
        for a in apps
        if a.get("status") == "applied"
        and days_since(a.get("updated_date", a.get("applied_date", ""))) >= FOLLOWUP_DAYS
    ]
    interviews = [a for a in apps if a.get("status") == "interview"]

    sections: list[str] = []
    if stale:
        lines = ["**⏰ Follow up (no update in 7+ days)**"]
        for a in sorted(stale, key=lambda x: days_since(x.get("updated_date", "")), reverse=True):
            d = days_since(a.get("updated_date", a.get("applied_date", "")))
            lines.append(f"• {a['company']} — {a['role']} ({d}d ago)")
        sections.append("\n".join(lines))

    if interviews:
        lines = ["**🗣️ Upcoming / active interviews**"]
        for a in interviews:
            lines.append(f"• {a['company']} — {a['role']}")
        sections.append("\n".join(lines))

    return {"followups_text": "\n\n".join(sections)}
