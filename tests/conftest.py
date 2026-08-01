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


# ---------------------------------------------------------------------------
# No test may reach a real model
# ---------------------------------------------------------------------------
class ModelCalledInTest(BaseException):
    """Raised when a test reaches the model layer without stubbing it.

    Derived from `BaseException`, NOT `Exception`, and that is load-bearing:
    every model caller in this repo deliberately catches `Exception` so a model
    outage degrades instead of crashing (`agents/job_applier/drafting.draft_one`,
    `agents/resume_generator/nodes/draft.draft_node`). An `AssertionError` from a
    guard is an `Exception`, so it gets swallowed into a perfectly innocent
    looking blank/error result and the test passes for the wrong reason. That is
    not hypothetical — it happened: with an `AssertionError` guard, a mutation
    that routed a question which must never be drafted straight to the model
    passed all 56 tests of the file that was supposed to catch it.
    """


def _model_calls_are_forbidden(*args, **kwargs):
    raise ModelCalledInTest(
        "this test called a real model. Stub the caller's `llm` "
        "(monkeypatch.setattr(<module>, 'llm', ...)), pass `llm_fn=`, or stub "
        "`litellm.completion` — never let a test depend on Ollama being up."
    )


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Suite-wide, because a per-file guard protects exactly one file.

    This started life as an autouse fixture inside `tests/test_applier_drafting.py`
    and that was a bug with a measured consequence: a one-line test in a DIFFERENT
    file calling `drafting.draft_one` without stubbing reached the live Ollama on
    the development machine and came back `source="drafted"`. It passed here and
    would have failed on any machine without Ollama running. Tasks 6, 7 and 8 all
    consume `agents.job_applier.drafting`, so the first test written outside that
    one file silently became a network test.

    Two layers, because each catches what the other cannot:

      1. Every module-level `llm` name bound to `shell.model_router.llm` is
         repointed. This is what produces a readable failure naming the fix, and
         it covers `from shell.model_router import llm` — the form every caller in
         this repo uses, which rebinds the function into the importing module and
         therefore cannot be intercepted by patching `model_router` alone.
      2. `litellm.completion` — the actual network boundary — is repointed as the
         backstop, for a caller imported after this fixture ran, or one that calls
         `model_router.llm` directly. `shell.model_router.llm` ITSELF is
         deliberately left alone: `tests/test_prompt_budgets.py` calls it on
         purpose to measure the kwargs it builds, with `litellm.completion`
         stubbed. Its own monkeypatch runs after this fixture and wins.
    """
    import litellm  # noqa: PLC0415 — imported here so conftest stays cheap

    from shell import model_router

    real = model_router.llm
    for module in list(sys.modules.values()):
        if module is None or module is model_router:
            continue
        name = getattr(module, "__name__", "") or ""
        if not (name.startswith("agents") or name.startswith("shell")
                or name.startswith("server") or name in ("scripts",)):
            continue
        if getattr(module, "llm", None) is real:
            monkeypatch.setattr(module, "llm", _model_calls_are_forbidden)
    monkeypatch.setattr(litellm, "completion", _model_calls_are_forbidden)
