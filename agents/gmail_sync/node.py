"""scan_gmail node — match recent emails to applications, advance statuses."""

from __future__ import annotations

from agents.application_tracker.store import load_all, update_status
from agents.gmail_sync.gmail import fetch_job_emails
from agents.gmail_sync.matching import company_matches, infer_status, rank_status, should_apply
from agents.gmail_sync.state import GmailSyncState

_AUTH_HINT = (
    "⚠️ Gmail not authorized. Run `uv run python -m agents.gmail_sync.gmail --auth` "
    "once to grant read access (re-consents calendar too)."
)


def scan_gmail_node(state: GmailSyncState) -> GmailSyncState:
    apps = load_all()
    if not apps:
        return {"message": "✉️ Gmail sync: no applications logged to match against."}

    try:
        emails = fetch_job_emails(allow_interactive=False)
    except Exception as exc:  # never crash a run
        return {"message": f"⚠️ Gmail scan failed ({exc})."}
    if emails is None:
        return {"message": _AUTH_HINT}

    updates: list[str] = []
    for app in apps:
        detected: str | None = None
        for em in emails:
            haystack = f"{em['from_name']} {em['subject']} {em['snippet']}"
            if company_matches(app.get("company", ""), haystack):
                st = infer_status(f"{em['subject']} {em['snippet']}")
                if st and (detected is None or rank_status(st) > rank_status(detected)):
                    detected = st
        if detected and should_apply(app.get("status", ""), detected):
            update_status(int(app["id"]), detected, auto_detected=True)
            updates.append(f"{app['company']}: {app.get('status')} → {detected}")

    n = len(emails)
    if updates:
        body = "\n".join(f"• {u}" for u in updates)
        return {"message": f"✉️ Gmail sync scanned {n} emails and updated {len(updates)} application(s):\n\n{body}"}
    return {"message": f"✉️ Gmail sync scanned {n} emails — no status changes detected."}
