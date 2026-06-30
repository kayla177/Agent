"""JSON store for job applications (the tracker's memory).

Stored at data/applications.json as:
    {"applications": [ {id, company, role, url, status, applied_date,
                        updated_date, notes}, ... ]}

Ids are small incrementing integers (friendly for the CLI: `track status 3 ...`).
Reads degrade gracefully — a missing or corrupt file is treated as "no
applications yet" so a run never crashes.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import config

STATUSES = ("applied", "interview", "offer", "accepted", "rejected")

_STORE = config.PROJECT_ROOT / "data" / "applications.json"


def _today() -> str:
    return dt.date.today().isoformat()


def load_all() -> list[dict]:
    """Return every application (empty list if none / unreadable)."""
    try:
        with _STORE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        apps = data.get("applications", [])
        return apps if isinstance(apps, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(apps: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"applications": apps}, fh, indent=2)
    tmp.replace(_STORE)  # atomic-ish swap so an interrupted write can't corrupt


def _next_id(apps: list[dict]) -> int:
    return max((int(a.get("id", 0)) for a in apps), default=0) + 1


def add_application(
    company: str, role: str, url: str = "", notes: str = "", status: str = "applied"
) -> dict:
    """Create and persist a new application; returns the stored record."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    apps = load_all()
    record = {
        "id": _next_id(apps),
        "company": company.strip(),
        "role": role.strip(),
        "url": url.strip(),
        "status": status,
        "applied_date": _today(),
        "updated_date": _today(),
        "notes": notes.strip(),
    }
    apps.append(record)
    _save(apps)
    return record


def update_status(app_id: int, status: str) -> dict | None:
    """Set an application's status; returns the updated record or None if absent."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    apps = load_all()
    for a in apps:
        if int(a.get("id", -1)) == int(app_id):
            a["status"] = status
            a["updated_date"] = _today()
            _save(apps)
            return a
    return None


def delete_application(app_id: int) -> bool:
    """Remove an application by id; returns True if one was removed."""
    apps = load_all()
    kept = [a for a in apps if int(a.get("id", -1)) != int(app_id)]
    if len(kept) == len(apps):
        return False
    _save(kept)
    return True


def days_since(date_str: str) -> int:
    """Whole days between `date_str` (ISO) and today; 0 if unparseable."""
    try:
        d = dt.date.fromisoformat(date_str)
        return (dt.date.today() - d).days
    except (ValueError, TypeError):
        return 0
