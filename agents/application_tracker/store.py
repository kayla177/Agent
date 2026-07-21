"""SQLite store for job applications (the tracker's memory).

Backed by the `applications` table in data/control_center.db (see store_db).
The public API is unchanged from the previous JSON version so the graph nodes
and web routes keep working; a new optional `auto_detected` flag on
update_status records status changes inferred from Gmail. Reads degrade
gracefully (a missing table -> "no applications yet") so a run never crashes.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import store_db

STATUSES = ("applied", "interview", "offer", "accepted", "rejected")


def _today() -> str:
    return dt.date.today().isoformat()


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["auto_detected"] = bool(d.get("auto_detected", 0))
    return d


def load_all() -> list[dict]:
    """Return every application (empty list if none / table missing)."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute("SELECT * FROM applications ORDER BY id").fetchall()
        return [_row(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def add_application(
    company: str, role: str, url: str = "", notes: str = "", status: str = "applied"
) -> dict:
    """Create and persist a new application; returns the stored record."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(company, role, url, status, applied_date, updated_date, notes, auto_detected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0) RETURNING *",
            (company.strip(), role.strip(), url.strip(), status, today, today, notes.strip()),
        )
        row = cur.fetchone()
    return _row(row)


def update_status(app_id: int, status: str, auto_detected: bool = False) -> dict | None:
    """Set status; returns the updated record or None if absent.

    A manual update (auto_detected=False) clears the auto flag so a Gmail-set
    status a user later overrides no longer shows as auto-detected.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE applications SET status = ?, updated_date = ?, auto_detected = ? "
            "WHERE id = ? RETURNING *",
            (status, _today(), 1 if auto_detected else 0, int(app_id)),
        )
        row = cur.fetchone()
    return _row(row) if row else None


def delete_application(app_id: int) -> bool:
    """Remove an application by id; returns True if one was removed."""
    store_db.init_db()
    with store_db.connect() as conn:
        cur = conn.execute("DELETE FROM applications WHERE id = ?", (int(app_id),))
    return cur.rowcount > 0


def days_since(date_str: str) -> int:
    """Whole days between `date_str` (ISO) and today; 0 if unparseable."""
    try:
        d = dt.date.fromisoformat(date_str)
        return (dt.date.today() - d).days
    except (ValueError, TypeError):
        return 0
