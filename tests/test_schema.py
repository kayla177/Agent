"""Schema guards — every new column must exist in a fresh DB AND after a
migration of a pre-existing one (schema.sql only covers fresh databases)."""

from __future__ import annotations

import store_db


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_jobs_has_country(temp_db):
    with store_db.connect() as conn:
        assert "country" in _cols(conn, "jobs")


def test_jobs_has_ghost_reason(temp_db):
    """ghost_reason must be a real column, not only a key in the `data` blob —
    Prisma reads columns, so without it the UI cannot say WHY a row is flagged
    and renders every ghost identically as "stale"."""
    with store_db.connect() as conn:
        assert "ghost_reason" in _cols(conn, "jobs")


def test_applications_has_resume_pdf_key(temp_db):
    with store_db.connect() as conn:
        assert "resume_pdf_key" in _cols(conn, "applications")


def test_applications_has_confirmed_at(temp_db):
    """Phase B Task 9. Separates a row VERIFIED against an ATS confirmation page
    from the optimistic row Phase A writes on the modal's confirm."""
    with store_db.connect() as conn:
        assert "confirmed_at" in _cols(conn, "applications")


def test_confirmed_at_is_nullable_with_no_default_and_is_not_backfilled(tmp_path, monkeypatch):
    """The migration must not invent evidence.

    Every application row that exists today was recorded optimistically and none
    of them has been verified, so a DEFAULT or a backfill would stamp real rows
    with a confirmation that never happened — the exact false positive the whole
    feature is built to avoid. NULL reads as "unverified", which is true of all
    of them.
    """
    db = tmp_path / "old.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    with store_db.connect() as conn:
        conn.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT NOT NULL, "
            "role TEXT NOT NULL, applied_date TEXT NOT NULL, updated_date TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO applications (company, role, applied_date, updated_date) "
            "VALUES ('Acme', 'SWE', '2026-01-01', '2026-01-01')"
        )

    store_db.init_db()

    with store_db.connect() as conn:
        assert "confirmed_at" in _cols(conn, "applications")
        rows = conn.execute("SELECT confirmed_at FROM applications").fetchall()
        assert [r[0] for r in rows] == [None]
        info = {r[1]: r for r in conn.execute("PRAGMA table_info(applications)")}
        assert info["confirmed_at"][3] == 0, "must be nullable"
        assert info["confirmed_at"][4] is None, "must have no default"


def test_init_db_is_idempotent_and_preserves_a_stamp(tmp_path, monkeypatch):
    """`server/app.py` calls `init_db()` on every startup and every agent store
    calls it on every write, so running it twice has to be a no-op — and must not
    disturb a confirmation already recorded."""
    db = tmp_path / "twice.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    store_db.init_db()
    with store_db.connect() as conn:
        conn.execute(
            "INSERT INTO applications (company, role, applied_date, updated_date, confirmed_at) "
            "VALUES ('Acme', 'SWE', '2026-01-01', '2026-01-01', '2026-08-02T12:00:00+00:00')"
        )
        first = _cols(conn, "applications")

    store_db.init_db()  # must not raise

    with store_db.connect() as conn:
        assert _cols(conn, "applications") == first
        assert conn.execute("SELECT confirmed_at FROM applications").fetchone()[0] \
            == "2026-08-02T12:00:00+00:00"


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
        assert "ghost_reason" in _cols(conn, "jobs")
        assert "resume_pdf_key" in _cols(conn, "applications")
        assert "confirmed_at" in _cols(conn, "applications")


def test_migrate_seeds_ghost_reason_from_the_existing_data_blob(tmp_path, monkeypatch):
    """The blob has carried ghost_reason all along, so the new column is seeded
    from it. Without that, all 222 already-flagged rows in the live DB would
    render as a bare "stale" — the mirrored column contradicting the blob — until
    something happened to rewrite each row."""
    import json

    db = tmp_path / "old.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    blob = json.dumps({"id": "a", "ghost": True, "ghost_reason": "delisted (not on Acme's board)"})
    with store_db.connect() as conn:
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, company TEXT, "
            "ghost INTEGER DEFAULT 0, data TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute("INSERT INTO jobs (id, status, ghost, data) VALUES ('a', 'new', 1, ?)", (blob,))
        # A row with no reason in its blob must land as '' (the column is NOT NULL).
        conn.execute("INSERT INTO jobs (id, status, ghost, data) VALUES ('b', 'new', 0, '{}')")
        # A corrupt blob must not abort init_db, which runs on every server start.
        conn.execute("INSERT INTO jobs (id, status, ghost, data) VALUES ('c', 'new', 1, 'not json')")

    store_db.init_db()  # must not raise

    with store_db.connect() as conn:
        rows = {r["id"]: r["ghost_reason"] for r in conn.execute("SELECT id, ghost_reason FROM jobs")}
    assert rows["a"] == "delisted (not on Acme's board)"
    assert rows["b"] == ""
    assert rows["c"] == ""

    store_db.init_db()  # idempotent: the seed is inside the add-column branch
    with store_db.connect() as conn:
        assert conn.execute("SELECT ghost_reason FROM jobs WHERE id = 'a'").fetchone()[0] \
            == "delisted (not on Acme's board)"


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
