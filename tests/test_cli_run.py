"""`scripts/run.py` is what launchd calls, and it recorded NOTHING.

Every scheduled run — the four launchd agents, every night — invoked the graph
directly instead of going through `server/runner.py`, so no `runs` row was ever
created for it. `/history` therefore showed only the runs Kayla started by hand
from the dashboard.

That is how a scrape with 261 fetch failures went unnoticed for days: the only
place it was visible at all was `jobscraper.log`. It also meant the
`NoBoardReachable` guard added in `d5d3ab1` could not surface in the UI, because
there was no row for it to fail.
"""
from __future__ import annotations

import sys

import pytest

import scripts.run as cli
from server import db as server_db


@pytest.fixture
def run_db(temp_db, monkeypatch):
    """`server.db` caches DB_PATH at import (see tests/conftest.py)."""
    monkeypatch.setattr(server_db, "DB_PATH", temp_db)
    server_db.init_db()
    return server_db


class _FakeSpec:
    def __init__(self, result=None, raises=None):
        self.result, self.raises = result or {}, raises
        self.built_with = None

    def build_graph(self, *, send=False):
        self.built_with = send
        spec = self

        class _G:
            def invoke(self, payload):
                spec.invoked_with = payload
                if spec.raises:
                    raise spec.raises
                return spec.result

        return _G()


def _run(monkeypatch, spec, argv=("run.py", "job_scraper")):
    monkeypatch.setattr(cli, "get_spec", lambda key: spec)
    monkeypatch.setattr(cli, "list_specs", lambda: [type("S", (), {"key": "job_scraper"})()])
    monkeypatch.setattr(sys, "argv", list(argv))
    return cli.main()


def test_a_scheduled_run_is_recorded_so_history_can_show_it(run_db, monkeypatch, capsys):
    spec = _FakeSpec({"message": "Found 1 new role."})
    _run(monkeypatch, spec)

    runs = run_db.list_runs(limit=5) if hasattr(run_db, "list_runs") else None
    row = _latest(run_db)
    assert row is not None, "a CLI run must create a runs row"
    assert row["agent_key"] == "job_scraper"
    assert row["status"] == "success"
    assert row["output_message"] == "Found 1 new role."
    assert row["finished_at"], "the row must not be left 'running' forever"


def test_a_raising_graph_is_recorded_as_an_error_not_left_running(run_db, monkeypatch):
    """`NoBoardReachable` is the reason this matters: the scraper now raises when
    it reaches no board, and that has to land somewhere Kayla will see."""
    spec = _FakeSpec(raises=RuntimeError("all 259 configured boards failed"))
    with pytest.raises(RuntimeError):
        _run(monkeypatch, spec)

    row = _latest(run_db)
    assert row is not None and row["status"] == "error"
    assert "259 configured boards failed" in (row["error"] or "")
    assert row["finished_at"]


def test_a_state_level_error_is_recorded_as_an_error(run_db, monkeypatch):
    spec = _FakeSpec({"error": "no_job", "message": "No scraped job found."})
    _run(monkeypatch, spec)
    row = _latest(run_db)
    assert row["status"] == "error"
    assert "no_job" in (row["error"] or "")
    assert row["output_message"] == "No scraped job found.", "keep the explanation"


def test_the_send_flag_and_backfill_flag_still_reach_the_graph(run_db, monkeypatch):
    spec = _FakeSpec({"message": "ok"})
    _run(monkeypatch, spec, argv=("run.py", "job_scraper", "--send", "--backfill"))
    assert spec.built_with is True, "--send must still reach build_graph"
    assert spec.invoked_with == {"backfill": True}


def _latest(db):
    with db._connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchall()
    return dict(rows[0]) if rows else None
