"""Greenhouse exposes the application form as structured JSON, so mapping is
schema-driven rather than DOM-guessing. Parsed from a captured fixture — this
test must never hit the network."""
from __future__ import annotations

import json
import pathlib

from agents.job_applier.schema_greenhouse import (
    demographic_questions_raw,
    form_url,
    parse_questions,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ats" / "greenhouse-questions.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def _questions():
    return parse_questions(_payload())


def test_parses_the_identity_questions():
    by = {q.label.lower(): q for q in _questions()}
    for label in ("first name", "last name", "email", "phone"):
        assert label in by, f"missing {label}"
        assert by[label].required is True
        assert by[label].kind == "text"


def test_resume_is_a_file_question():
    q = next(q for q in _questions() if "resume" in q.label.lower())
    assert q.kind == "file"
    assert q.required is True


def test_multi_field_question_picks_the_primary_input():
    """Greenhouse models Resume/CV as fields=[input_file, textarea]. A file
    upload beats a paste-in textarea, so `kind` must be `file`, not `textarea`."""
    q = next(q for q in _questions() if "resume" in q.label.lower())
    assert q.kind == "file"


def test_cover_letter_is_optional_and_also_a_file_question():
    """Cover Letter has the same [input_file, textarea] shape as Resume/CV
    but is not required — the precedence rule must apply regardless of
    `required`, and `required` itself must still come through correctly."""
    q = next(q for q in _questions() if "cover letter" in q.label.lower())
    assert q.kind == "file"
    assert q.required is False


def test_every_question_has_a_stable_key():
    ks = [q.key for q in _questions()]
    assert all(ks) and len(ks) == len(set(ks)), "keys must exist and be unique"


def test_key_prefers_the_api_name_over_a_slug():
    """The captured fixture's fields all carry a `name` (e.g. "first_name",
    "question_68177703"); key must use that verbatim, not a slug of the
    (employer-editable) label."""
    by = {q.label.lower(): q for q in _questions()}
    assert by["first name"].key == "first_name"
    assert by["resume/cv"].key == "resume"


def test_select_question_preserves_options():
    q = next(
        q for q in _questions() if "immigration sponsorship" in q.label.lower()
    )
    assert q.kind == "select"
    assert q.options == ["Yes", "No"]


def test_checkbox_question_preserves_options():
    q = next(q for q in _questions() if "candidate privacy policy" in q.label.lower())
    assert q.kind == "checkbox"
    assert q.options == ["Acknowledge/Confirm"]


def test_non_select_questions_have_no_options():
    q = next(q for q in _questions() if "first name" in q.label.lower())
    assert q.options == []


def test_empty_or_missing_questions_is_not_an_error():
    assert parse_questions({}) == []
    assert parse_questions({"questions": []}) == []


def test_demographic_questions_are_not_in_the_core_list():
    """The resolver must never be handed a race/gender/veteran-status
    question to answer, so demographic_questions must never surface as a
    Question from parse_questions."""
    payload = _payload()
    demo_labels = {
        d.get("label", "").lower() for d in demographic_questions_raw(payload) or []
    }
    core_labels = {q.label.lower() for q in parse_questions(payload)}
    assert not (demo_labels & core_labels)


def test_demographic_questions_raw_is_a_separate_accessor():
    assert demographic_questions_raw({}) == []
    assert demographic_questions_raw({"demographic_questions": None}) == []


def test_location_questions_are_excluded_from_the_core_list():
    """location_questions (lat/long/free-text location) is geolocation
    plumbing Greenhouse's own widget fills, not a question for the resolver."""
    labels = {q.label.lower() for q in _questions()}
    assert "latitude" not in labels
    assert "longitude" not in labels


def test_form_url_reads_absolute_url():
    assert form_url(_payload()) == (
        "https://boards.greenhouse.io/cloudflare/jobs/8077075?gh_jid=8077075"
    )


def test_form_url_missing_key_is_empty_string():
    assert form_url({}) == ""
