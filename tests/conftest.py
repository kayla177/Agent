"""Shared pytest fixtures.

`temp_db` repoints the whole storage layer at a throwaway SQLite file, replacing
the old hand-rolled `_use_temp_store()` that mutated a module global with no
cleanup. Every store reads `store_db.DB_PATH` at call time, so monkeypatching it
is enough to isolate a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store_db  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point store_db at a fresh DB built from schema.sql; yield its path."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(store_db, "DB_PATH", db)
    store_db.init_db()
    return db


@pytest.fixture
def client(temp_db):
    """FastAPI TestClient sharing the temp DB (import is lazy: server pulls in
    litellm/google libs, which we only want loaded for router tests)."""
    from fastapi.testclient import TestClient

    from server.app import app

    with TestClient(app) as c:
        yield c
