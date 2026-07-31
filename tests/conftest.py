"""Shared pytest fixtures.

`temp_db` repoints the whole storage layer at a throwaway SQLite file, replacing
the old hand-rolled `_use_temp_store()` that mutated a module global with no
cleanup. Every store reads `store_db.DB_PATH` at call time, so monkeypatching it
is enough to isolate a test.

`server/db.py` computes its own `DB_PATH = config.DB_PATH` at import time,
independently of `store_db` — it is a second cached copy of the same value, not
a re-export. Patching only `store_db.DB_PATH` leaves `server.db.DB_PATH`
pointed at the real `data/control_center.db`, so the `client` fixture patches
both: otherwise `TestClient(app)`'s startup hook
(`db.mark_stale_running_as_error()`, an `UPDATE runs SET status='error' WHERE
status='running'`) runs against the live production database on every test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import store_db  # noqa: E402

# Hermetic defaults for the job-scraper prefs. These are the hardcoded defaults
# from config.py as of this fixture's introduction — NOT whatever the developer
# happens to have saved.
_JOB_PREF_DEFAULTS = {
    "JOB_DROP_GHOSTS": False,
    "JOB_MAX_AGE_DAYS": 60,
    "JOB_MIN_FIT": 0,
    "JOB_PROFILE": "",
    "JOB_COUNTRIES": ["US", "CA"],
}


@pytest.fixture(autouse=True)
def hermetic_job_prefs(monkeypatch):
    """Pin the job-scraper prefs so the suite never depends on `data/prefs.json`.

    `config._apply_prefs()` reads that overlay at import, so without this the
    suite's behaviour depends on the developer's saved Settings. That is not
    hypothetical: ticking "drop stale/ghost postings" in the UI set
    `JOB_DROP_GHOSTS=True`, which made `freshness_node` start dropping the very
    rows several tests hand it, and one test failed with `IndexError` on a list
    it expected to contain a posting.

    `JOB_DROP_GHOSTS` is pinned False because most tests assert on the *tags*
    (`ghost`, `ghost_reason`) and need the flagged row returned. Tests that
    exercise the hard-drop path monkeypatch it True themselves; a later
    `setattr` on the same monkeypatch instance wins over this one.
    """
    for name, value in _JOB_PREF_DEFAULTS.items():
        monkeypatch.setattr(config, name, value)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point store_db at a fresh DB built from schema.sql; yield its path."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    store_db.init_db()
    return db


@pytest.fixture
def client(temp_db, monkeypatch):
    """FastAPI TestClient sharing the temp DB (import is lazy: server pulls in
    litellm/google libs, which we only want loaded for router tests).

    Also repoints `server.db.DB_PATH` (see module docstring) so the startup
    hook's `mark_stale_running_as_error()` hits the temp DB, not the real one.
    """
    from fastapi.testclient import TestClient

    from server import db as server_db
    from server.app import app

    monkeypatch.setattr(server_db, "DB_PATH", temp_db)

    with TestClient(app) as c:
        yield c
