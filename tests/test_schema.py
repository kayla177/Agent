"""Schema guards — every new column must exist in a fresh DB AND after a
migration of a pre-existing one (schema.sql only covers fresh databases)."""

from __future__ import annotations

import store_db


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_jobs_has_country(temp_db):
    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")


def test_applications_has_resume_pdf_key(temp_db):
    with store_db.connect() as conn:
        assert "resume_pdf_key" in _cols(conn, "applications")


def test_applicant_profile_table(temp_db):
    with store_db.connect() as conn:
        cols = _cols(conn, "applicant_profile")
    for c in ("full_name", "email", "phone", "location", "linkedin_url",
              "github_url", "portfolio_url", "school", "degree", "grad_date",
              "us_work_auth", "ca_work_auth", "needs_sponsorship", "summary",
              "updated_at"):
        assert c in cols, c


def test_migrate_adds_columns_to_preexisting_tables(tmp_path, monkeypatch):
    """Simulate an old DB: create jobs/applications WITHOUT the new columns,
    then prove init_db() adds them."""
    db = tmp_path / "old.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    with store_db.connect() as conn:
        # `company` is included because schema.sql's pre-existing idx_jobs_company
        # index requires it; unlike `country`, `company` has never needed a
        # migration guard (it has existed since the table's original creation).
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, company TEXT)")
        conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT)")
    store_db.init_db()
    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")
        assert "resume_pdf_key" in _cols(conn, "applications")


def test_indexed_new_column_survives_a_preexisting_table(tmp_path, monkeypatch):
    """Regression: schema.sql declares idx_jobs_country over a column that only
    _migrate adds. CREATE INDEX IF NOT EXISTS guards the index NAME, not the
    column, so if the schema script ran before the migration this raised
    'no such column: country' against every already-existing database."""
    db = tmp_path / "preexisting.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    with store_db.connect() as conn:
        # A `jobs` table shaped like the live one, WITHOUT `country`.
        conn.execute(
            "CREATE TABLE jobs (id TEXT NOT NULL PRIMARY KEY, company TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'new', location TEXT NOT NULL DEFAULT '', "
            "data TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute("CREATE INDEX idx_jobs_company ON jobs(company)")

    store_db.init_db()  # must not raise

    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")
        idx = {r[1] for r in conn.execute("PRAGMA index_list(jobs)")}
        assert "idx_jobs_country" in idx
