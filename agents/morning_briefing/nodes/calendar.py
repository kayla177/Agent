"""Calendar node — today's events from Google Calendar.

First run needs a one-time browser consent:  `uv run python -m
agents.morning_briefing.nodes.calendar --auth`  which mints and caches a token
to GOOGLE_TOKEN_FILE. After that the node refreshes silently.
"""

from __future__ import annotations

import datetime as dt

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config
from agents.morning_briefing.state import BriefingState

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _load_credentials(*, allow_interactive: bool) -> Credentials | None:
    creds: Credentials | None = None
    if config.GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.GOOGLE_TOKEN_FILE), _SCOPES
        )

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
        return creds

    if not allow_interactive:
        return None  # scheduled run; can't pop a browser

    if not config.GOOGLE_OAUTH_CLIENT_FILE.exists():
        raise FileNotFoundError(
            f"OAuth client file not found: {config.GOOGLE_OAUTH_CLIENT_FILE}. "
            "Download it from Google Cloud Console (OAuth client, Desktop app)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.GOOGLE_OAUTH_CLIENT_FILE), _SCOPES
    )
    creds = flow.run_local_server(port=0)
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
    return creds


def fetch_calendar(*, allow_interactive: bool = False) -> str:
    """Return today's events as a short bulleted list."""
    creds = _load_credentials(allow_interactive=allow_interactive)
    if creds is None:
        return "⚠️ Calendar not authorized yet (run the --auth step once)."

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    tz = dt.timezone.utc
    now = dt.datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)

    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )
    if not events:
        return "📅 No events today — clear schedule."

    lines = []
    for ev in events:
        start_info = ev["start"]
        if "dateTime" in start_info:
            when = dt.datetime.fromisoformat(start_info["dateTime"]).strftime("%H:%M")
        else:
            when = "all-day"
        lines.append(f"• {when} {ev.get('summary', '(no title)')}")
    return "📅 Today:\n" + "\n".join(lines)


def calendar_node(state: BriefingState) -> BriefingState:
    try:
        return {"calendar": fetch_calendar(allow_interactive=False)}
    except Exception as exc:
        return {"calendar": f"⚠️ Calendar unavailable ({exc})"}


if __name__ == "__main__":
    import sys

    interactive = "--auth" in sys.argv
    print(fetch_calendar(allow_interactive=interactive))
