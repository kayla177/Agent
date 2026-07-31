"""Regression coverage for the `temp_db` / `client` fixtures in conftest.py.

`server/db.py` caches its own `DB_PATH = config.DB_PATH` at import time,
independently of `store_db`'s identical-looking cache. A `client` fixture that
only patched `store_db.DB_PATH` would leave `server.db.DB_PATH` pointed at the
real `data/control_center.db` — and `TestClient(app)` fires the app's startup
hook (`db.mark_stale_running_as_error()`, an `UPDATE runs SET status='error'
WHERE status='running'`) on every use, so that bug would silently mutate the
live database on every test run. This test proves both globals get patched to
the same temp path, and that the real database file is provably untouched.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def _prod_db_fingerprint() -> tuple[bool, str]:
    """(exists, sha256 of contents) for the real production DB so we can prove
    a test run left it untouched, whether or not it exists on this machine."""
    path = config.DB_PATH
    if not path.exists():
        return False, ""
    return True, hashlib.sha256(path.read_bytes()).hexdigest()


def test_client_fixture_isolates_both_db_path_globals(request: pytest.FixtureRequest) -> None:
    before_exists, before_hash = _prod_db_fingerprint()

    # Resolve fixtures via the request object (rather than as normal test
    # parameters) so the "before" fingerprint above is captured strictly prior
    # to the `client` fixture entering `TestClient(app)` and firing the
    # startup hook.
    temp_db = request.getfixturevalue("temp_db")
    request.getfixturevalue("client")

    import store_db
    from server import db as server_db

    assert store_db.DB_PATH == temp_db
    assert server_db.DB_PATH == temp_db
    assert store_db.DB_PATH != config.DB_PATH
    assert server_db.DB_PATH != config.DB_PATH

    after_exists, after_hash = _prod_db_fingerprint()
    assert after_exists == before_exists
    assert after_hash == before_hash
