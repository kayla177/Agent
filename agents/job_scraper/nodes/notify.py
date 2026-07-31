"""Notify node — format ranked roles, persist records, and (optionally) deliver.

Mirrors the morning_briefing deliver pattern: the graph wires delivery only
when built with send=True (via the `send` closure passed into the node). The
message is always assembled and written to state so a print-only dev run can
show it. Delivery failure is caught and surfaced, never raised.

Full enriched records are persisted here on every run (the same store powers the
web jobs view + the dedupe "seen" memory). Discord delivery is the only side
effect gated on send=True — so the web "run scraper" button (send=0) still fills
the jobs board.

The backfill node re-injects stored rows tagged `_rescored` so they get scored /
re-freshness-checked / persisted, but they are NOT genuinely new finds — so the
digest must not re-announce them. Every posting is persisted regardless (the
board needs the refreshed score/country), but only untagged, shown-country rows
are formatted into the announcement. Transient `_`-prefixed pipeline tags
(`_rescored`, `_skip_llm`) are stripped before persistence so they never reach
the mirrored columns or the `data` JSON blob.

`JOB_COUNTRIES` empty means "no country filtering" (same convention as
`JOB_SOURCES` / `STOCK_WATCHLIST`), not "show nothing" — an empty prefs list
must never silently blank the whole digest. A missing/blank `country` is
treated as `UNKNOWN`, which is never hidden (see locations.py: only `OTHER` is
ever filtered out downstream, never `UNKNOWN`).

After persisting, this node also repairs stored descriptions
(`store.refresh_descriptions`) from `state["raw"] + state["filtered"]` — the
full JD text this run already downloaded — refreshes `last_seen`
(`store.touch_last_seen`) on everything observed this run, and re-derives the
ghost flag across the WHOLE
store (`store.sweep_ghosts`) — not just the rows that happened to pass through
the pipeline this run. That sweep flags AND un-flags, so each of its four
outcomes is logged separately. See `freshness.py`'s module docstring for why
the pipeline-only check can't reach a fully-processed row on its own.
"""

from __future__ import annotations

import datetime as dt

import config
from agents.job_scraper.state import JobScraperState
from agents.job_scraper.store import (
    refresh_descriptions,
    sweep_ghosts,
    touch_last_seen,
    upsert_records,
)
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
        all_rows = state.get("new", [])
        warnings = state.get("warnings", [])

        # Announce only genuinely new postings in the shown countries. Rescored
        # backlog rows are persisted but never re-announced. An empty
        # JOB_COUNTRIES means "no filtering" (matches JOB_SOURCES /
        # STOCK_WATCHLIST's empty-means-default convention) — otherwise
        # clearing that Settings textarea would silently blank the whole
        # digest. A missing/blank country is kept as UNKNOWN rather than
        # dropped, per "never drop a posting on an UNKNOWN country".
        shown = set(config.JOB_COUNTRIES)
        announce = []
        for p in all_rows:
            if p.get("_rescored"):
                continue
            c = p.get("country") or "UNKNOWN"
            if shown and c not in shown and c != "UNKNOWN":
                continue
            announce.append(p)
        message = _format_message(announce, warnings)

        # Persist the scraped roles on EVERY run — this is the data the web jobs
        # view + dedupe memory read, and the web "run scraper" button runs with
        # send=0. Persistence is a data operation; Discord delivery is the only
        # send-gated side effect. Transient pipeline tags (`_rescored`,
        # `_skip_llm`) must never reach the store: strip any `_`-prefixed key
        # before persisting, so they can't land in the mirrored columns or blob.
        try:
            upsert_records([{k: v for k, v in p.items() if not k.startswith("_")} for p in all_rows])
        except Exception as exc:
            print(f"⚠️ Could not persist job records: {exc}")

        # Repair stored descriptions from text this run already downloaded.
        # Rows written under the old 1200-char cap keep a truncated description
        # forever otherwise: dedupe drops already-seen ids before we get here,
        # so they are never re-persisted. `raw` + `filtered` are still in state,
        # so this costs no extra network calls.
        #
        # MUST run AFTER upsert_records. A backlog row re-injected by backfill
        # carries the OLD stored (short) description, and upsert merges the
        # posting over the stored record — so refreshing first would just get
        # clobbered. refresh_descriptions only ever lengthens, so running it
        # last is safe in both directions.
        try:
            refreshed = refresh_descriptions(
                list(state.get("raw") or []) + list(state.get("filtered") or [])
            )
            if refreshed:
                print(f"ℹ️ refreshed the description on {refreshed} stored posting(s) with longer fetched text")
        except Exception as exc:
            print(f"⚠️ Could not refresh stored descriptions: {exc}")

        # Refresh last_seen on everything actually observed this run. dedupe
        # drops already-seen postings before this point, so without this a
        # posting that is STILL listed would never get re-stamped — this runs
        # AFTER upsert_records so a row that is both re-injected (_rescored)
        # and still listed ends up correctly stamped with today.
        try:
            touched = touch_last_seen(state.get("observed_ids") or set())
            if touched:
                print(f"ℹ️ refreshed last_seen on {touched} still-listed posting(s)")
        except Exception as exc:
            print(f"⚠️ Could not refresh last_seen: {exc}")

        # freshness_node only sees rows that re-enter the pipeline this run
        # (fresh finds, or backlog rows backfill re-injected because they
        # still needed work) — a fully-processed row is never re-selected by
        # backfill and so can never be flagged, or UN-flagged, there. This
        # sweep re-derives the ghost state of every stored new/viewed row, so a
        # delisted posting is flagged even once it's fully scored and refined,
        # a delisting the board later contradicts is cleared, and a row that
        # aged past JOB_MAX_AGE_DAYS since its last pass gets flagged.
        #
        # Each outcome is logged separately: "flagged 3" and "cleared 3" are
        # very different events and a single net number would hide both.
        try:
            counts = sweep_ghosts(
                state.get("observed_ids") or set(), state.get("fetched_ok") or set()
            )
            if counts["delisted"]:
                print(f"ℹ️ swept {counts['delisted']} stored posting(s) as delisted (absent from a healthy board)")
            if counts["relisted"]:
                print(f"ℹ️ cleared the delisted flag on {counts['relisted']} posting(s) seen on their board again")
            if counts["stale"]:
                print(f"ℹ️ flagged {counts['stale']} stored posting(s) as stale (age/deadline)")
            if counts["unstale"]:
                print(f"ℹ️ cleared a stale flag that no longer holds on {counts['unstale']} posting(s)")
        except Exception as exc:
            print(f"⚠️ Could not re-derive ghost flags: {exc}")

        if send:
            try:
                send_message(message)
            except Exception as exc:
                # Don't crash the run on a delivery failure — surface it.
                print(f"⚠️ Discord delivery failed: {exc}")

        return {"message": message}

    return notify_node
