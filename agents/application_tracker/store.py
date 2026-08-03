"""SQLite store for job applications (the tracker's memory).

Backed by the `applications` table in data/control_center.db (see store_db).
The public API is unchanged from the previous JSON version so the graph nodes
and web routes keep working; a new optional `auto_detected` flag on
update_status records status changes inferred from Gmail. Reads degrade
gracefully (a missing table -> "no applications yet") so a run never crashes.

`mark_confirmed` is the one writer of `applications.confirmed_at`, the column
that separates a row VERIFIED against an ATS confirmation page from the
optimistic row Phase A writes the moment the user confirms the apply modal. It
only ever stamps; nothing here clears it.
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
    company: str,
    role: str,
    url: str = "",
    notes: str = "",
    status: str = "applied",
    resume_job_id: str | None = None,
    resume_pdf_key: str | None = None,
    job_id: str | None = None,
) -> dict:
    """Create and persist a new application; returns the stored record.

    `resume_job_id` links the résumé used (-> resumes.job_id) and
    `resume_pdf_key` pins the exact compiled PDF that was sent, so editing that
    résumé later cannot destroy the record of what the company received.
    `job_id` links back to the exact `jobs.id` this application is for — the
    only reliable way to verify an undo-apply request actually belongs to a
    given posting, since company+title alone cannot disambiguate two distinct
    postings sharing both (e.g. the same role on two ATS boards).
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    store_db.init_db()
    today = _today()
    with store_db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(company, role, url, status, applied_date, updated_date, notes, "
            " auto_detected, resume_job_id, resume_pdf_key, job_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?) RETURNING *",
            (company.strip(), role.strip(), url.strip(), status, today, today,
             notes.strip(), (resume_job_id or "").strip() or None,
             (resume_pdf_key or "").strip() or None,
             (job_id or "").strip() or None),
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


def mark_confirmed(app_id: int, when: str | None = None) -> dict | None:
    """Stamp `confirmed_at` — the ONE writer of that column, in one direction.

    Called only after `agents.job_applier.confirm.detect_confirmation` has
    positively identified an ATS confirmation page. Returns the row (stamped, or
    already-stamped and untouched), or None if `app_id` does not exist.

    Three properties, each a deliberate refusal:

      * **It never re-stamps.** The `WHERE confirmed_at IS NULL OR = ''` clause
        means a second detection of the same confirmation keeps the FIRST
        timestamp. Re-stamping would silently move a recorded fact forward every
        time a page happened to be re-read.
      * **It never clears.** There is no code path here, or anywhere else in the
        repo, that writes NULL into this column. A non-match does not call this
        function at all: absence of evidence is not evidence, so an unmatched
        page leaves the row exactly as Phase A wrote it.
      * **It touches nothing else** — not `status`, not `updated_date`. Being
        verified is a different fact from having moved in the pipeline, and
        `updated_date` is the column the tracker shows as "last change you
        made". Confirmation is not a change the user made.

    Returning the row on the already-stamped path (rather than the None that a
    bare `RETURNING` would give when the guarded UPDATE matches nothing) is what
    lets a caller tell "already confirmed" apart from "no such application".
    """
    store_db.init_db()
    stamp = (when or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    with store_db.connect() as conn:
        conn.execute(
            "UPDATE applications SET confirmed_at = ? "
            "WHERE id = ? AND (confirmed_at IS NULL OR confirmed_at = '')",
            (stamp, int(app_id)),
        )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (int(app_id),)
        ).fetchone()
    return _row(row) if row else None


def delete_application(app_id: int) -> bool:
    """Remove an application by id; returns True if one was removed."""
    store_db.init_db()
    with store_db.connect() as conn:
        cur = conn.execute("DELETE FROM applications WHERE id = ?", (int(app_id),))
    return cur.rowcount > 0


def set_resume_link(app_id: int, resume_job_id: str | None) -> dict | None:
    """Record which generated resume was used to apply (or clear it with None).

    `resume_job_id` points at resumes.job_id so interview prep can recall the
    exact resume sent. Returns the updated record, or None if the id is absent.
    """
    store_db.init_db()
    link = (resume_job_id or "").strip() or None
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE applications SET resume_job_id = ? WHERE id = ? RETURNING *",
            (link, int(app_id)),
        )
        row = cur.fetchone()
    return _row(row) if row else None


def days_since(date_str: str) -> int:
    """Whole days between `date_str` (ISO) and today; 0 if unparseable."""
    try:
        d = dt.date.fromisoformat(date_str)
        return (dt.date.today() - d).days
    except (ValueError, TypeError):
        return 0
