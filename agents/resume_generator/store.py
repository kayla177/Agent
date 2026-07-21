"""SQLite store for the resume generator: the experience pool + generated resumes.

Two tables in data/control_center.db (companion to the jobs/applications tables
in store_db):

  experience_docs  the user's real background — uploaded resume(s) + past-project
                   write-ups, parsed to plain text. The "pool" fed to the model
                   is the concatenation of every row's text.
  resumes          one generated resume per job (keyed by the job id), as Markdown
                   plus the ATS keywords it targeted. status is draft | final.

The DDL lives here (not in the shared store_db.SCHEMA) on purpose: this feature
ships on its own branch while a parallel effort migrates the app to Next.js +
Prisma. The column shapes below are chosen to match the Prisma models that effort
will add, so a later `prisma db pull` can adopt these tables verbatim — no data
migration. Reads degrade gracefully when the tables are missing.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import store_db

STATUSES = ("draft", "final")
KINDS = ("resume", "project")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience_docs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    filename  TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT 'resume',   -- resume | project
    text      TEXT NOT NULL DEFAULT '',
    added_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resumes (
    job_id      TEXT PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    markdown    TEXT NOT NULL DEFAULT '',
    keywords    TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | final
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _ensure() -> None:
    """Create this feature's tables if absent. Safe to call repeatedly."""
    store_db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with store_db.connect() as conn:
        conn.executescript(_SCHEMA)


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
    keywords: list[str] | None = None,
    status: str = "draft",
) -> dict:
    """Insert or replace the resume for `job_id`; preserves created_at on update."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    _ensure()
    now = _now()
    kw = json.dumps(keywords or [], ensure_ascii=False)
    with store_db.connect() as conn:
        conn.execute(
            "INSERT INTO resumes (job_id, company, role, markdown, keywords, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET company=excluded.company, "
            "role=excluded.role, markdown=excluded.markdown, "
            "keywords=excluded.keywords, status=excluded.status, "
            "updated_at=excluded.updated_at",
            (job_id, company, role, markdown, kw, status, now, now),
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
