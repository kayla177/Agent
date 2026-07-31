"""Prefs overlay tests.

The merge behavior is a bug fix: save_prefs used to write ONLY its validated
subset and os.replace the whole file, so any key it did not know about (a
hand-added JOB_PROFILE, for example) was silently destroyed on the next save.
"""

from __future__ import annotations

import json

import pytest

import config
from server import prefs as prefstore


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """Redirect the prefs overlay at a temp file and restore config after.

    `monkeypatch.undo()` MUST come before `config.refresh()`. Fixture finalizers
    run in reverse setup order, so `monkeypatch` (set up first, as a dependency)
    is torn down LAST — meaning a bare `config.refresh()` here would re-read the
    temp overlay and leave those values loaded in `config` for every later test.
    Verified: without the undo, teardown's refresh reads the tmp_path file.
    """
    path = tmp_path / "prefs.json"
    monkeypatch.setattr(config, "PREFS_FILE", path)
    yield path
    monkeypatch.undo()
    config.refresh()


def test_save_preserves_unknown_keys(overlay):
    overlay.write_text(json.dumps({"SOME_FUTURE_KEY": "keep me"}), encoding="utf-8")
    prefstore.save_prefs({"WEATHER_TIMEZONE": "America/Toronto"})
    saved = json.loads(overlay.read_text(encoding="utf-8"))
    assert saved["SOME_FUTURE_KEY"] == "keep me"
    assert saved["WEATHER_TIMEZONE"] == "America/Toronto"


def test_job_profile_is_editable(overlay):
    prefstore.save_prefs({"JOB_PROFILE": "CS undergrad, Python + React"})
    assert json.loads(overlay.read_text(encoding="utf-8"))["JOB_PROFILE"] == "CS undergrad, Python + React"
    assert config.JOB_PROFILE == "CS undergrad, Python + React"


def test_job_min_fit_range_validated(overlay):
    with pytest.raises(ValueError):
        prefstore.save_prefs({"JOB_MIN_FIT": 500})


def test_job_countries_validated(overlay):
    prefstore.save_prefs({"JOB_COUNTRIES": ["US", "CA"]})
    assert config.JOB_COUNTRIES == ["US", "CA"]
    with pytest.raises(ValueError):
        prefstore.save_prefs({"JOB_COUNTRIES": ["MARS"]})


def test_drop_ghosts_bool(overlay):
    prefstore.save_prefs({"JOB_DROP_GHOSTS": True})
    assert config.JOB_DROP_GHOSTS is True


def test_current_exposes_job_keys(overlay):
    cur = prefstore.current()
    for k in ("JOB_PROFILE", "JOB_MIN_FIT", "JOB_MAX_AGE_DAYS", "JOB_DROP_GHOSTS", "JOB_COUNTRIES"):
        assert k in cur
