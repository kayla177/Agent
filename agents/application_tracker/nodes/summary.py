"""Summary node — load applications and build the pipeline-counts section."""

from __future__ import annotations

from agents.application_tracker.state import TrackerState
from agents.application_tracker.store import STATUSES, load_all

_EMOJI = {
    "applied": "📨",
    "interview": "🗣️",
    "offer": "🎉",
    "accepted": "✅",
    "rejected": "❌",
}


def summary_node(state: TrackerState) -> TrackerState:
    apps = load_all()
    if not apps:
        return {"apps": [], "summary_text": "No applications logged yet."}

    counts = {s: 0 for s in STATUSES}
    for a in apps:
        counts[a.get("status", "applied")] = counts.get(a.get("status", "applied"), 0) + 1

    active = sum(counts[s] for s in ("applied", "interview", "offer"))
    parts = [f"**{len(apps)}** total · **{active}** active"]
    line = "  ".join(
        f"{_EMOJI[s]} {counts[s]} {s}" for s in STATUSES if counts.get(s)
    )
    if line:
        parts.append(line)
    return {"apps": apps, "summary_text": "\n".join(parts)}
