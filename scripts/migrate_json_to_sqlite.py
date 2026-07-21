"""One-time migration: JSON flat files -> SQLite (data/control_center.db).

Idempotent. Applications are matched by (company, role) so re-runs don't
duplicate them and keep their original ids; jobs are written verbatim (status +
timestamps preserved) and re-runs upsert by id.

Run:  uv run python scripts/migrate_json_to_sqlite.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import store_db
from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

_APPS_JSON = config.PROJECT_ROOT / "data" / "applications.json"
_JOBS_JSON = config.PROJECT_ROOT / "agents" / "job_scraper" / "data" / "jobs.json"


def _load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def migrate_applications() -> int:
    data = _load_json(_APPS_JSON)
    apps = data.get("applications", []) if isinstance(data, dict) else []
    seen = {(a["company"], a["role"]) for a in appstore.load_all()}
    added = 0
    for a in apps:
        key = (a.get("company", ""), a.get("role", ""))
        if key in seen:
            continue
        with store_db.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO applications (id, company, role, url, status, "
                    "applied_date, updated_date, notes, auto_detected) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (a.get("id"), a.get("company", ""), a.get("role", ""),
                     a.get("url", ""), a.get("status", "applied"),
                     a.get("applied_date", ""), a.get("updated_date", ""),
                     a.get("notes", "")),
                )
            except sqlite3.IntegrityError:
                # id slot already taken by a different row — migrate without the
                # explicit id, letting AUTOINCREMENT assign a fresh one.
                conn.execute(
                    "INSERT INTO applications (company, role, url, status, "
                    "applied_date, updated_date, notes, auto_detected) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                    (a.get("company", ""), a.get("role", ""),
                     a.get("url", ""), a.get("status", "applied"),
                     a.get("applied_date", ""), a.get("updated_date", ""),
                     a.get("notes", "")),
                )
        seen.add(key)
        added += 1
    return added


def migrate_jobs() -> int:
    data = _load_json(_JOBS_JSON)
    jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
    for pid, rec in jobs.items():
        jobstore.replace_record({**rec, "id": pid})
    return len(jobs)


def main() -> None:
    store_db.init_db()
    n_apps = migrate_applications()
    n_jobs = migrate_jobs()
    print(f"migrated {n_apps} applications, {n_jobs} jobs into {store_db.DB_PATH}")


if __name__ == "__main__":
    main()
