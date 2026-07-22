"""Notify node — format ranked roles, persist records, and (optionally) deliver.

Mirrors the morning_briefing deliver pattern: the graph wires delivery only
when built with send=True (via the `send` closure passed into the node). The
message is always assembled and written to state so a print-only dev run can
show it. Delivery failure is caught and surfaced, never raised.

Full enriched records are persisted here on every run (the same store powers the
web jobs view + the dedupe "seen" memory). Discord delivery is the only side
effect gated on send=True — so the web "run scraper" button (send=0) still fills
the jobs board.
"""

from __future__ import annotations

import datetime as dt

from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import upsert_records
from shell.discord_client import send_message


def _badges(p: dict) -> str:
    """Compact inline badges: fit %, freshness/ghost, comp, extra sources."""
    bits: list[str] = []
    score = p.get("fit_score")
    if score is not None:
        reason = p.get("fit_reason", "")
        bits.append(f"🎯 {score}%" + (f" — {reason}" if reason else ""))
    if p.get("ghost"):
        bits.append(f"⚠️ {p.get('ghost_reason', 'stale')}")
    elif p.get("age_days") is not None:
        bits.append(f"🕒 {p['age_days']}d ago")
    if p.get("compensation"):
        bits.append(f"💰 {p['compensation']}")
    also = p.get("also_on") or []
    if also:
        bits.append("also on " + ", ".join(also))
    return "  ·  ".join(bits)


def _format_message(new: list[dict], warnings: list[str]) -> str:
    today = dt.datetime.now().strftime("%A, %B %d")
    lines: list[str] = [f"**🧑‍💻 Job Scraper — {today}**"]

    if not new:
        lines.append("No new co-op / intern / new-grad roles since last check.")
    else:
        n = len(new)
        lines.append(f"Found **{n}** new role{'s' if n != 1 else ''} (best fit first):")
        # Preserve the incoming order (rank node sorts by fit desc), but print a
        # company header whenever it changes so the digest stays scannable.
        current = None
        for p in new:
            company = p.get("company", "?")
            if company != current:
                current = company
                lines.append("")
                lines.append(f"**{company}**")
            title = p.get("title", "?")
            loc = p.get("location", "—")
            url = p.get("url", "")
            lines.append(f"• {title} — {loc} — {url}")
            badges = _badges(p)
            if badges:
                lines.append(f"   {badges}")

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

        # Persist the scraped roles on EVERY run — this is the data the web jobs
        # view + dedupe memory read, and the web "run scraper" button runs with
        # send=0. Persistence is a data operation; Discord delivery is the only
        # send-gated side effect.
        try:
            upsert_records(new)
        except Exception as exc:
            print(f"⚠️ Could not persist job records: {exc}")

        if send:
            try:
                send_message(message)
            except Exception as exc:
                # Don't crash the run on a delivery failure — surface it.
                print(f"⚠️ Discord delivery failed: {exc}")

        return {"message": message}

    return notify_node
