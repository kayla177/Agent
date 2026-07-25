"""SQLite store for the resume generator: the experience pool + generated resumes.

Two tables in data/control_center.db (companion to the jobs/applications tables
in store_db):

  experience_docs  the user's real background — uploaded resume(s) + past-project
                   write-ups, parsed to plain text. The "pool" fed to the model
                   is the concatenation of every row's text.
  resumes          one generated resume per job (keyed by the job id), as Markdown
                   plus the ATS keywords it targeted. status is draft | final.

These two tables (like all others) are defined in the canonical schema.sql and
created via store_db.init_db(). Reads degrade gracefully when the tables are
missing.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import store_db

STATUSES = ("draft", "final")
KINDS = ("resume", "project")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _ensure() -> None:
    """Create the tables if absent (via the canonical schema). Safe repeatedly."""
    store_db.init_db()


# --------------------------------------------------------------------------
# Experience pool
# --------------------------------------------------------------------------
def add_experience_doc(filename: str, text: str, *, kind: str = "resume") -> int:
    """Store one parsed experience document; returns its new row id."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    _ensure()
    with store_db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO experience_docs (filename, kind, text, added_at) "
            "VALUES (?, ?, ?, ?)",
            (filename, kind, text, _now()),
        )
        return int(cur.lastrowid)


def list_experience_docs() -> list[dict]:
    """All experience docs (newest first); text omitted for a lightweight list."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute(
                "SELECT id, filename, kind, length(text) AS chars, added_at "
                "FROM experience_docs ORDER BY id DESC"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def load_experience_text() -> str:
    """Concatenate every experience doc into one pool string for the model.

    Each doc is prefixed with a labeled header so the model can tell a resume
    from a project write-up. Returns "" when the pool is empty.
    """
    try:
        with store_db.connect() as conn:
            rows = conn.execute(
                "SELECT filename, kind, text FROM experience_docs ORDER BY id"
            ).fetchall()
    except sqlite3.OperationalError:
        return ""
    chunks = []
    for r in rows:
        label = r["kind"].upper()
        name = r["filename"] or "(unnamed)"
        chunks.append(f"### {label}: {name}\n{r['text'].strip()}")
    return "\n\n".join(chunks).strip()


def delete_experience_doc(doc_id: int) -> bool:
    """Remove one experience doc; True if a row was deleted."""
    _ensure()
    with store_db.connect() as conn:
        cur = conn.execute("DELETE FROM experience_docs WHERE id = ?", (doc_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------
# Generated resumes
# --------------------------------------------------------------------------
def upsert_resume(
    job_id: str,
    *,
    company: str,
    role: str,
    markdown: str,
    latex: str | None = None,
    keywords: list[str] | None = None,
    status: str = "draft",
) -> dict:
    """Insert or replace the resume for `job_id`; preserves created_at on update.

    Before overwriting an existing resume, the current row is snapshotted into
    `resume_versions` so past drafts are never lost (see version history in the
    résumé tab). `latex` is the tailored .tex; pass None to leave any existing
    value untouched (so a markdown-only edit doesn't wipe the compiled résumé).
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    now = _now()
    kw = json.dumps(keywords or [], ensure_ascii=False)
    with store_db.connect() as conn:
        # Snapshot the outgoing version (if any) before we overwrite it.
        prev = conn.execute(
            "SELECT markdown, keywords, status, latex FROM resumes WHERE job_id = ?", (job_id,)
        ).fetchone()
        if prev is not None:
            conn.execute(
                "INSERT INTO resume_versions (job_id, markdown, keywords, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, prev["markdown"], prev["keywords"], prev["status"], now),
            )
        # None -> keep the previous latex (or '' for a brand-new row).
        tex = latex if latex is not None else (prev["latex"] if prev is not None else "")
        conn.execute(
            "INSERT INTO resumes (job_id, company, role, markdown, latex, keywords, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET company=excluded.company, "
            "role=excluded.role, markdown=excluded.markdown, latex=excluded.latex, "
            "keywords=excluded.keywords, status=excluded.status, "
            "updated_at=excluded.updated_at",
            (job_id, company, role, markdown, tex, kw, status, now, now),
        )
    return get_resume(job_id) or {}


def get_resume(job_id: str) -> dict | None:
    """Return the resume record for `job_id` (keywords decoded), or None."""
    try:
        with store_db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE job_id = ?", (job_id,)
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    rec = dict(row)
    try:
        rec["keywords"] = json.loads(rec.get("keywords") or "[]")
    except json.JSONDecodeError:
        rec["keywords"] = []
    return rec


def set_resume_status(job_id: str, status: str) -> dict | None:
    """Flip a resume between draft and final; returns the record or None."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    with store_db.connect() as conn:
        cur = conn.execute(
            "UPDATE resumes SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _now(), job_id),
        )
        if cur.rowcount == 0:
            return None
    return get_resume(job_id)


def list_resume_versions(job_id: str) -> list[dict]:
    """Past snapshots for `job_id`, newest first (keywords decoded). [] if none."""
    try:
        with store_db.connect() as conn:
            rows = conn.execute(
                "SELECT id, job_id, markdown, keywords, status, created_at "
                "FROM resume_versions WHERE job_id = ? ORDER BY id DESC",
                (job_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        rec = dict(r)
        try:
            rec["keywords"] = json.loads(rec.get("keywords") or "[]")
        except json.JSONDecodeError:
            rec["keywords"] = []
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Master resume (single canonical row; tailored drafts start from it)
# --------------------------------------------------------------------------
_MASTER_ID = 1


def get_master_resume() -> dict:
    """Return the master resume ({markdown, latex, keywords, updated_at}); empty if unset."""
    empty = {"markdown": "", "latex": "", "keywords": [], "updated_at": ""}
    try:
        with store_db.connect() as conn:
            row = conn.execute(
                "SELECT markdown, latex, keywords, updated_at FROM master_resume WHERE id = ?",
                (_MASTER_ID,),
            ).fetchone()
    except sqlite3.OperationalError:
        return empty
    if row is None:
        return empty
    rec = dict(row)
    try:
        rec["keywords"] = json.loads(rec.get("keywords") or "[]")
    except json.JSONDecodeError:
        rec["keywords"] = []
    return rec


def upsert_master_resume(
    markdown: str | None = None,
    *,
    latex: str | None = None,
    keywords: list[str] | None = None,
) -> dict:
    """Create or update the single master resume row; returns the record.

    Only the fields you pass are changed — None leaves markdown/latex as they
    were, so editing the .tex never wipes the legacy markdown and vice-versa.
    """
    _ensure()
    prev = get_master_resume()
    md = prev["markdown"] if markdown is None else markdown
    tex = prev["latex"] if latex is None else latex
    kw = json.dumps(keywords if keywords is not None else prev["keywords"], ensure_ascii=False)
    with store_db.connect() as conn:
        conn.execute(
            "INSERT INTO master_resume (id, markdown, latex, keywords, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "markdown=excluded.markdown, latex=excluded.latex, keywords=excluded.keywords, "
            "updated_at=excluded.updated_at",
            (_MASTER_ID, md, tex, kw, _now()),
        )
    return get_master_resume()
