"""Single-row store for the applicant profile (`applicant_profile` table).

Lives at the project root beside store_db.py because BOTH the agents (job fit
scoring reads `summary`; Phase B's autofill reads the typed fields) and the
server layer need it, and agents must not import the server package.

One row only — `id` is always 1, enforced here rather than by a constraint, the
same pattern master_resume uses. Reads degrade gracefully: a missing table
returns the all-empty default rather than raising, so a run never dies on it.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import store_db

# Editable fields, in display order. `id` and `updated_at` are managed here.
FIELDS: tuple[str, ...] = (
    "full_name", "email", "phone", "location",
    "linkedin_url", "github_url", "portfolio_url",
    "school", "degree", "grad_date",
    "us_work_auth", "ca_work_auth", "needs_sponsorship",
    "summary",
)

# Accepted work-authorization values (free text elsewhere would defeat the
# point of typed fields — an autofill must never guess this answer).
WORK_AUTH = ("", "citizen", "permanent_resident", "f1_opt", "tn_eligible", "needs_sponsorship")

_INT_FIELDS = frozenset({"needs_sponsorship"})


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _empty() -> dict:
    row = {f: 0 if f in _INT_FIELDS else "" for f in FIELDS}
    row["id"] = 1
    row["updated_at"] = ""
    return row


def get_profile() -> dict:
    """The profile row, or an all-empty default if absent."""
    try:
        with store_db.connect() as conn:
            row = conn.execute("SELECT * FROM applicant_profile WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return _empty()
    return dict(row) if row else _empty()


def upsert_profile(**fields) -> dict:
    """Write the supplied fields (partial update) and return the full row.

    Raises ValueError on an unknown field or an invalid work-auth value, so a
    typo in a request body can never silently land in the DB.
    """
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown profile field(s): {', '.join(sorted(unknown))}")
    for key in ("us_work_auth", "ca_work_auth"):
        if key in fields and str(fields[key]) not in WORK_AUTH:
            raise ValueError(f"{key} must be one of {WORK_AUTH}")

    store_db.init_db()
    clean = {
        k: (1 if str(v) not in ("", "0", "False", "false") else 0) if k in _INT_FIELDS else str(v).strip()
        for k, v in fields.items()
    }

    with store_db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM applicant_profile WHERE id = 1").fetchone()
        if exists is None:
            cols = ["id", "updated_at", *clean]
            vals = [1, _now(), *clean.values()]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO applicant_profile ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        else:
            assigns = ", ".join(f"{k} = ?" for k in clean)
            conn.execute(
                f"UPDATE applicant_profile SET {assigns}, updated_at = ? WHERE id = 1",
                [*clean.values(), _now()],
            )
    return get_profile()


def fit_profile_text() -> str:
    """The free-text candidate description used for job fit scoring ('' if unset)."""
    return (get_profile().get("summary") or "").strip()
