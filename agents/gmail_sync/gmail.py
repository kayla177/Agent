"""Gmail read access for the sync agent — mirrors calendar.py's OAuth flow.

Reads the SHARED Google token (config.GOOGLE_TOKEN_FILE). The node calls
fetch_job_emails(allow_interactive=False), which returns None whenever Gmail
isn't usable (no token, no scope, no client file, or a 401/403) so the node can
degrade gracefully. One-time consent:

    uv run python -m agents.gmail_sync.gmail --auth

which mints a token with BOTH calendar + gmail scopes (so calendar keeps
working) and caches it to config.GOOGLE_TOKEN_FILE.
"""

from __future__ import annotations

import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

# The node only needs gmail read; the interactive auth requests the union so a
# re-consent doesn't strip the calendar scope the briefing agent relies on.
_LOAD_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_AUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Recruiter-ish signal in the last 30 days. Broad on purpose; matching.py
# decides relevance per application.
_QUERY = (
    'newer_than:30d (interview OR "next steps" OR offer OR unfortunately OR '
    '"moving forward" OR application OR "phone screen" OR schedule OR '
    '"thank you for applying" OR "your application")'
)


def _load_credentials(*, allow_interactive: bool, scopes: list[str]) -> Credentials | None:
    creds: Credentials | None = None
    if config.GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
            return creds
        except Exception:
            return None
    if not allow_interactive:
        return None
    if not config.GOOGLE_OAUTH_CLIENT_FILE.exists():
        raise FileNotFoundError(
            f"OAuth client file not found: {config.GOOGLE_OAUTH_CLIENT_FILE}. "
            "Download it from Google Cloud Console (OAuth client, Desktop app)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_OAUTH_CLIENT_FILE), scopes)
    creds = flow.run_local_server(port=0)
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
    return creds


def _parse_from(value: str) -> tuple[str, str]:
    """Split a 'Name <email>' From header into (name, email)."""
    m = re.match(r"\s*(.*?)\s*<([^>]+)>\s*$", value or "")
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    return "", (value or "").strip()


def fetch_job_emails(*, allow_interactive: bool = False, max_results: int = 40) -> list[dict] | None:
    """Return recent job-related emails as {from_name, from_email, subject,
    snippet}, or None if Gmail isn't authorized/usable (caller degrades)."""
    creds = _load_credentials(allow_interactive=allow_interactive, scopes=_LOAD_SCOPES)
    if creds is None:
        return None
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        listing = (
            service.users().messages()
            .list(userId="me", q=_QUERY, maxResults=max_results)
            .execute()
        )
        out: list[dict] = []
        for ref in listing.get("messages", []):
            msg = (
                service.users().messages()
                .get(userId="me", id=ref["id"], format="metadata",
                     metadataHeaders=["From", "Subject"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            name, email = _parse_from(headers.get("From", ""))
            out.append({
                "from_name": name, "from_email": email,
                "subject": headers.get("Subject", ""), "snippet": msg.get("snippet", ""),
            })
        return out
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            return None  # not authorized / insufficient scope → degrade
        raise


if __name__ == "__main__":
    # One-time consent with the union scopes (preserves calendar access).
    creds = _load_credentials(allow_interactive=True, scopes=_AUTH_SCOPES)
    print("Authorized." if creds else "Authorization failed.")
