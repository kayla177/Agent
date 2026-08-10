"""All database access for cover letters.

Mirrors `agents/resume_generator/store.py` deliberately: same function shapes,
same snapshot-before-overwrite guarantee, same "absent reads as an empty dict"
convention for the singleton. A reader who knows the résumé store should need to
learn nothing here.

`body` is PLAIN TEXT, not markdown — a cover letter is prose pasted into a
textarea, where "**Dear**" would land literally.
"""

from __future__ import annotations

import datetime as dt

import store_db

STATUSES = ("draft", "final")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _ensure() -> None:
    """Create the tables if absent (via the canonical schema). Safe repeatedly."""
    store_db.init_db()


# --------------------------------------------------------------------------
# The master letter — the one document the user owns
# --------------------------------------------------------------------------


def get_master_cover_letter() -> dict:
    """The master letter, or an empty-bodied dict when none is saved yet.

    Returns a dict rather than None so every caller can read `["body"]` without
    a guard — the same convention `get_master_resume` uses.
    """
    _ensure()
    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT id, body, updated_at FROM master_cover_letter ORDER BY id LIMIT 1"
        ).fetchone()
    return dict(row) if row else {"id": 1, "body": "", "updated_at": ""}


def upsert_master_cover_letter(body: str) -> dict:
    """Replace the master letter. A singleton: always exactly one row."""
    _ensure()
    now = _now()
    with store_db.connect() as conn:
        row = conn.execute("SELECT id FROM master_cover_letter ORDER BY id LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO master_cover_letter (body, updated_at) VALUES (?, ?)",
                (body, now),
            )
        else:
            conn.execute(
                "UPDATE master_cover_letter SET body = ?, updated_at = ? WHERE id = ?",
                (body, now, row["id"]),
            )
    return get_master_cover_letter()


# --------------------------------------------------------------------------
# Per-job letters
# --------------------------------------------------------------------------


def upsert_cover_letter(
    job_id: str,
    *,
    company: str,
    role: str,
    body: str,
    status: str = "draft",
) -> dict:
    """Insert or replace the letter for `job_id`; preserves created_at on update.

    Before overwriting an existing letter, the current row is snapshotted into
    `cover_letter_versions`, so pressing generate again never destroys the draft
    you had. Same guarantee `upsert_resume` gives.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    now = _now()
    with store_db.connect() as conn:
        prev = conn.execute(
            "SELECT body, status, created_at FROM cover_letters WHERE job_id = ?", (job_id,)
        ).fetchone()
        if prev is not None:
            conn.execute(
                "INSERT INTO cover_letter_versions (job_id, body, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (job_id, prev["body"], prev["status"], now),
            )
        created = prev["created_at"] if prev is not None else now
        conn.execute(
            "INSERT INTO cover_letters (job_id, company, role, body, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET company=excluded.company, "
            "role=excluded.role, body=excluded.body, status=excluded.status, "
            "updated_at=excluded.updated_at",
            (job_id, company, role, body, status, created, now),
        )
    return get_cover_letter(job_id) or {}


def get_cover_letter(job_id: str) -> dict | None:
    _ensure()
    with store_db.connect() as conn:
        row = conn.execute(
            "SELECT job_id, company, role, body, status, created_at, updated_at "
            "FROM cover_letters WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def set_cover_letter_status(job_id: str, status: str) -> dict | None:
    """Set draft/final. Returns None when there is no letter for that job."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE cover_letters SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _now(), job_id),
        )
        if cur.rowcount == 0:
            return None
    return get_cover_letter(job_id)


def list_cover_letter_versions(job_id: str) -> list[dict]:
    """Past bodies for one job, newest first."""
    _ensure()
    with store_db.connect() as conn:
        rows = conn.execute(
            "SELECT id, job_id, body, status, created_at FROM cover_letter_versions "
            "WHERE job_id = ? ORDER BY id DESC",
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]
