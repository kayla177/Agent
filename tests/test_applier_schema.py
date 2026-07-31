"""Greenhouse exposes the application form as structured JSON, so mapping is
schema-driven rather than DOM-guessing. Parsed from a captured fixture — this
test must never hit the network."""
from __future__ import annotations

import json
import pathlib

from agents.job_applier.schema_greenhouse import (
    demographic_questions_raw,
    excluded_eeo_questions,
    form_url,
    parse_questions,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ats" / "greenhouse-questions.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def _questions():
    return parse_questions(_payload())


def _q(label, name="q", field_type="input_text", required=False, values=None):
    """Build one minimal `questions[]`-shaped entry for synthetic payloads."""
    entry = {"name": name, "type": field_type}
    if values is not None:
        entry["values"] = values
    return {"label": label, "required": required, "fields": [entry]}


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
    """The captured fixture's `demographic_questions` is `null`, so this test
    alone would pass even if parse_questions accidentally looped over that
    key too (an empty array merged into anything changes nothing) — see
    test_demographic_questions_with_real_content_never_leak below for the
    version that actually exercises the exclusion."""
    payload = _payload()
    demo_labels = {
        d.get("label", "").lower() for d in demographic_questions_raw(payload) or []
    }
    core_labels = {q.label.lower() for q in parse_questions(payload)}
    assert not (demo_labels & core_labels)


def test_demographic_questions_with_real_content_never_leak():
    """Regression test with teeth: inject REAL EEO content into
    `demographic_questions` (the fixture's is `null`, which cannot catch a
    merge bug) and assert none of it reaches parse_questions(). A version of
    parse_questions that loops over `demographic_questions` in addition to
    `questions` — the exact mutation an ATS reviewer verified independently —
    fails this test."""
    payload = _payload()
    payload = dict(payload)
    payload["demographic_questions"] = [
        _q("Gender", name="gender", field_type="multi_value_single_select",
           values=[{"label": "Male", "value": 1}, {"label": "Female", "value": 2}]),
        _q("Race/Ethnicity", name="race_ethnicity", field_type="multi_value_single_select",
           values=[{"label": "White", "value": 1}, {"label": "Black or African American", "value": 2}]),
        _q("Veteran Status", name="veteran_status", field_type="multi_value_single_select",
           values=[{"label": "I am a veteran", "value": 1}]),
    ]
    core_labels = {q.label.lower() for q in parse_questions(payload)}
    assert "gender" not in core_labels
    assert "race/ethnicity" not in core_labels
    assert "veteran status" not in core_labels


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


# --- Content-based EEO backstop ---------------------------------------
#
# Which ARRAY a question arrives in is not a safety property: an employer
# can write a custom EEO-flavored question straight into the core
# `questions` array. These adversarial payloads reproduce that exactly
# ("Race" (select, options White/Black), "Gender", "Veteran Status" placed
# in `questions`) and assert the content backstop still keeps them out of
# parse_questions(), one per required term.

_EEO_LABELS_BY_TERM = {
    "race": "What is your race?",
    "ethnicity": "What is your ethnicity?",
    "gender": "What is your gender?",
    "sex": "What is your sex?",
    "veteran": "Are you a veteran?",
    "disability": "Do you have a disability?",
    "sexual orientation": "What is your sexual orientation?",
    "pronoun": "What are your pronouns?",
}


def test_eeo_terms_are_excluded_from_the_core_list_even_in_the_core_array():
    for term, label in _EEO_LABELS_BY_TERM.items():
        payload = {"questions": [_q(label, name=f"q_{term}")]}
        labels = {q.label.lower() for q in parse_questions(payload)}
        assert label.lower() not in labels, f"{term!r} term leaked: {label!r}"


def test_eeo_terms_are_retrievable_via_the_excluded_accessor():
    """Excluded questions must not silently vanish — they stay retrievable
    via excluded_eeo_questions, just never via parse_questions."""
    payload = {
        "questions": [
            _q(
                "Race",
                name="race",
                field_type="multi_value_single_select",
                values=[{"label": "White", "value": 1}, {"label": "Black", "value": 2}],
            ),
            _q("Gender", name="gender"),
            _q("Veteran Status", name="veteran_status"),
        ]
    }
    assert parse_questions(payload) == []
    excluded = {q.label for q in excluded_eeo_questions(payload)}
    assert excluded == {"Race", "Gender", "Veteran Status"}
    race = next(q for q in excluded_eeo_questions(payload) if q.label == "Race")
    assert race.options == ["White", "Black"]  # excluded questions are still fully parsed


def test_excluded_eeo_questions_empty_payload_is_not_an_error():
    assert excluded_eeo_questions({}) == []
    assert excluded_eeo_questions({"questions": []}) == []


# --- Near-misses: must NOT be excluded ---------------------------------


def test_substring_race_inside_embrace_is_not_excluded():
    """Word-boundary regression: this repo already shipped one substring bug
    (`agents/job_scraper/locations.py` matched "uk" inside "Milwaukee"). This
    label contains "race" as a substring of "embrace", not the standalone
    word, and must survive."""
    label = "How do you embrace ambiguity in a fast-moving environment?"
    payload = {"questions": [_q(label, name="embrace_q")]}
    labels = {q.label for q in parse_questions(payload)}
    assert label in labels
    assert excluded_eeo_questions(payload) == []


def test_substring_sex_inside_essex_is_not_excluded():
    """Same class of bug for "sex": "Essex" contains it as a substring, not
    the standalone word."""
    label = "Which office do you prefer: Essex, London, or Remote?"
    payload = {
        "questions": [
            _q(
                label,
                name="office_pref",
                field_type="multi_value_single_select",
                values=[{"label": "Essex", "value": 1}, {"label": "London", "value": 2}],
            )
        ]
    }
    labels = {q.label for q in parse_questions(payload)}
    assert label in labels


def test_eeo_term_in_a_select_option_does_not_taint_the_question():
    """Only the question LABEL is screened, never its options. "Gender
    Studies" sitting among other majors in a dropdown must not make the
    whole "what was your major" question look like an EEO question — the
    protected term lives in one answer choice, not in what is being asked."""
    label = "What was your undergraduate major?"
    payload = {
        "questions": [
            _q(
                label,
                name="major",
                field_type="multi_value_single_select",
                values=[
                    {"label": "Gender Studies", "value": 1},
                    {"label": "Biology", "value": 2},
                    {"label": "Computer Science", "value": 3},
                ],
            )
        ]
    }
    q = next(q for q in parse_questions(payload) if q.label == label)
    assert q.kind == "select"
    assert "Gender Studies" in q.options  # the option text is untouched
    assert excluded_eeo_questions(payload) == []


# --- Key collisions ------------------------------------------------------


def test_duplicate_labels_with_no_name_get_disambiguated_keys():
    """Two questions both labelled 'Additional Comments' with no `name` at
    all both slugify to the same key. The first keeps the plain slug; the
    second gets a stable positional suffix instead of silently colliding."""
    payload = {
        "questions": [
            _q("Additional Comments", name=""),
            _q("Additional Comments", name=""),
        ]
    }
    ks = [q.key for q in parse_questions(payload)]
    assert len(ks) == len(set(ks)) == 2
    assert ks[0] == "additional_comments"
    assert ks[1] == "additional_comments__2"


def test_duplicate_explicit_names_get_disambiguated_keys():
    """Two structurally distinct questions that happen to share a literal
    `name` (should never happen on a real Greenhouse form, but must not be
    assumed) must still end up with unique, stable keys."""
    payload = {
        "questions": [
            _q("First Question", name="dup"),
            _q("Second Question", name="dup"),
        ]
    }
    ks = [q.key for q in parse_questions(payload)]
    assert len(ks) == len(set(ks)) == 2
    assert ks[0] == "dup"
    assert ks[1] == "dup__2"


# --- `required` string coercion -----------------------------------------


def test_required_string_false_is_treated_as_false():
    payload = {"questions": [_q("Optional Field", name="opt", required="false")]}
    q = parse_questions(payload)[0]
    assert q.required is False


def test_required_string_true_is_treated_as_true():
    payload = {"questions": [_q("Required Field", name="req", required="true")]}
    q = parse_questions(payload)[0]
    assert q.required is True
