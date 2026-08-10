"""The cover-letter store. Mirrors resume_generator/store.py, including the one
behaviour that matters most: a regenerate SNAPSHOTS the outgoing letter before
overwriting it, so a draft is never destroyed by pressing the button again."""
from __future__ import annotations

import pytest

from agents.cover_letter_generator import store as cl


def test_the_master_letter_round_trips(temp_db):
    assert cl.get_master_cover_letter()["body"] == "", "absent reads as empty, not None"
    cl.upsert_master_cover_letter("Dear Hiring Manager,\n\nI build things.\n")
    got = cl.get_master_cover_letter()
    assert got["body"] == "Dear Hiring Manager,\n\nI build things.\n"
    assert got["updated_at"], "a save must stamp updated_at"


def test_saving_the_master_letter_twice_replaces_rather_than_appends(temp_db):
    cl.upsert_master_cover_letter("first")
    cl.upsert_master_cover_letter("second")
    assert cl.get_master_cover_letter()["body"] == "second"
    import store_db
    with store_db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM master_cover_letter").fetchone()[0]
    assert n == 1, "the master letter is a singleton, like master_resume"


def test_a_letter_round_trips_for_one_job(temp_db):
    cl.upsert_cover_letter("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                           body="Dear Acme,")
    got = cl.get_cover_letter("Acme:greenhouse:1")
    assert got["company"] == "Acme" and got["role"] == "SWE Intern"
    assert got["body"] == "Dear Acme," and got["status"] == "draft"
    assert got["created_at"] and got["updated_at"]


def test_no_letter_for_a_job_reads_as_none(temp_db):
    assert cl.get_cover_letter("nobody:greenhouse:0") is None


def test_regenerating_snapshots_the_outgoing_letter_instead_of_losing_it(temp_db):
    """The whole reason `cover_letter_versions` exists. Pressing generate twice
    must not destroy the draft you had."""
    cl.upsert_cover_letter("j", company="Acme", role="R", body="first draft")
    cl.upsert_cover_letter("j", company="Acme", role="R", body="second draft")

    assert cl.get_cover_letter("j")["body"] == "second draft"
    versions = cl.list_cover_letter_versions("j")
    assert [v["body"] for v in versions] == ["first draft"]


def test_created_at_survives_a_regenerate(temp_db):
    cl.upsert_cover_letter("j", company="A", role="R", body="one")
    first = cl.get_cover_letter("j")["created_at"]
    cl.upsert_cover_letter("j", company="A", role="R", body="two")
    assert cl.get_cover_letter("j")["created_at"] == first


def test_status_can_be_set_and_is_validated(temp_db):
    cl.upsert_cover_letter("j", company="A", role="R", body="x")
    assert cl.set_cover_letter_status("j", "final")["status"] == "final"
    with pytest.raises(ValueError):
        cl.upsert_cover_letter("k", company="A", role="R", body="x", status="nonsense")


def test_setting_status_on_a_missing_letter_is_none_not_a_crash(temp_db):
    assert cl.set_cover_letter_status("missing", "final") is None


def test_versions_are_newest_first(temp_db):
    for body in ("v1", "v2", "v3"):
        cl.upsert_cover_letter("j", company="A", role="R", body=body)
    assert [v["body"] for v in cl.list_cover_letter_versions("j")] == ["v2", "v1"]
