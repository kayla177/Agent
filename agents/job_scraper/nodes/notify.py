"""Notify node — format new roles, persist their ids, and (optionally) deliver.

Mirrors the morning_briefing deliver pattern: the graph wires delivery only
when built with send=True (via the `send` closure passed into the node). The
message is always assembled and written to state so a print-only dev run can
show it. Delivery failure is caught and surfaced, never raised.

Seen-ids are persisted here (after the message is built) so that a role is only
marked "seen" once it has actually been reported in this run.
"""

from __future__ import annotations

import datetime as dt

from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import add_seen
from shell.discord_client import send_message


def _format_message(new: list[dict], warnings: list[str]) -> str:
    today = dt.datetime.now().strftime("%A, %B %d")
    lines: list[str] = [f"**🧑‍💻 Job Scraper — {today}**"]

    if not new:
        lines.append("No new co-op / intern / new-grad roles since last check.")
    else:
        n = len(new)
        lines.append(f"Found **{n}** new role{'s' if n != 1 else ''}:")
        # Group by company, stable order.
        by_company: dict[str, list[dict]] = {}
        for p in new:
            by_company.setdefault(p.get("company", "?"), []).append(p)
        for company in sorted(by_company):
            lines.append("")
            lines.append(f"**{company}**")
            for p in by_company[company]:
                title = p.get("title", "?")
                loc = p.get("location", "—")
                url = p.get("url", "")
                lines.append(f"• {title} — {loc} — {url}")

    if warnings:
        lines.append("")
        lines.append("\n".join(warnings))

    return "\n".join(lines)


def make_notify_node(*, send: bool):
    """Return a notify node bound to whether it should deliver to Discord."""

    def notify_node(state: JobScraperState) -> JobScraperState:
        new = state.get("new", [])
        warnings = state.get("warnings", [])
        message = _format_message(new, warnings)

        # Only a real (send=True) run has side effects: deliver, then mark the
        # roles seen. A print-only dry run previews roles without consuming them.
        if send:
            try:
                send_message(message)
            except Exception as exc:
                # Don't crash the run on a delivery failure — surface it.
                print(f"⚠️ Discord delivery failed: {exc}")

            # Persist only after a successful-or-attempted delivery, so dry runs
            # never advance the dedupe memory.
            try:
                add_seen([p["id"] for p in new])
            except Exception as exc:
                print(f"⚠️ Could not persist seen-store: {exc}")

        return {"message": message}

    return notify_node
