"""The resolver is the safety boundary: it may only answer from typed profile
fields. Anything it cannot answer becomes source='blank' with a reason, which
the handoff shows the user. It must never guess a name, an email, or — above
all — a work-authorization answer."""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from agents.job_applier.resolver import (
    BLOCKING_KINDS,
    SOURCES,
    Answer,
    blocking,
    classify,
    resolve,
)
from agents.job_applier import resolver
from agents.job_applier.locate_dom import parse_controls
from agents.job_applier.schema_greenhouse import Question, parse_questions

FAKE = {
    "full_name": "Testy McTestface",
    "email": "testy.mctestface@example.invalid",
    "phone": "+1-555-0100",
    "linkedin_url": "https://www.linkedin.com/in/example-invalid",
    "us_work_auth": "", "ca_work_auth": "", "needs_sponsorship": 0,
    "school": "University of Waterloo", "degree": "Computer Engineering (3rd year)",
    "grad_date": "2027-04", "github_url": "", "portfolio_url": "", "location": "",
    "summary": "",
}

# The profile as it actually is today: only school/degree/summary typed in.
# This is the common case in practice, so it gets its own tests.
SPARSE = {**{k: "" for k in FAKE}, "needs_sponsorship": 0,
          "school": "University of Waterloo", "degree": "Computer Engineering", "summary": "x"}

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ats" / "greenhouse-questions.json"

# Derived, never hand-listed: a status added to the enum must be exercised by the
# tests that sweep every status, or it ships untested.
from profile_store import WORK_AUTH as _ALL_STATUSES  # noqa: E402


def q(label, required=True, kind="text"):
    return Question(key=label.lower().replace(" ", "_"), label=label, required=required, kind=kind, options=[])


def sel(label, options, kind="select"):
    return Question(key="x", label=label, required=True, kind=kind, options=options)


# ---------------------------------------------------------------- brief tests

def test_splits_full_name_across_first_and_last():
    a = {x.question.label: x for x in resolve([q("First Name"), q("Last Name")], FAKE)}
    assert a["First Name"].value == "Testy" and a["First Name"].source == "profile"
    assert a["Last Name"].value == "McTestface"


def test_single_name_field_gets_the_whole_name():
    assert resolve([q("Full Name")], FAKE)[0].value == "Testy McTestface"


def test_email_and_phone_come_from_the_profile():
    a = {x.question.label: x.value for x in resolve([q("Email"), q("Phone")], FAKE)}
    assert a["Email"] == "testy.mctestface@example.invalid"
    assert a["Phone"] == "+1-555-0100"


def test_unset_profile_field_is_blank_with_a_reason_not_invented():
    ans = resolve([q("GitHub Profile")], FAKE)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "profile" in ans.note.lower()


def test_work_authorization_is_never_drafted_or_guessed():
    """With both work-auth fields unset, the answer must be blank and marked
    blocking. A wrong answer here goes out on a real application."""
    ans = resolve([q("Are you legally authorized to work in the United States?")], FAKE)[0]
    assert ans.source == "blank"
    assert ans.value == ""
    assert "authorization" in ans.note.lower()


def test_work_authorization_is_used_when_the_profile_sets_it():
    prof = {**FAKE, "us_work_auth": "tn_eligible"}
    ans = resolve([q("Are you legally authorized to work in the United States?")], prof)[0]
    assert ans.source == "profile"
    assert ans.value


def test_free_text_question_is_left_for_the_drafting_node():
    ans = resolve([q("Why do you want to work here?", kind="textarea")], FAKE)[0]
    assert ans.source == "blank"
    assert "draft" in ans.note.lower()


def test_resolver_touches_no_io():
    """Pure function: importing and calling it must not need a browser, a
    network, a database, a file, or a model. Guards the safety boundary against
    future drift.

    Checks IMPORTS via the AST rather than grepping the whole source, because the
    docstrings legitimately discuss the browser and the DOM ("attach the file
    yourself in the open browser window") and a text grep either false-positives
    on that prose or gets weakened until it stops meaning anything.

    The original list was `httpx` / `playwright` / `llm(` / `requests` only —
    which left `sqlite3`, `open()`, `store_db`, `locate_dom` and `browser`
    unguarded. Purity held, but by the import list happening to be short, not
    because this test enforced it.
    """
    import ast
    import inspect

    import agents.job_applier.resolver as r

    tree = ast.parse(inspect.getsource(r))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.add(node.module)

    forbidden_modules = {
        "httpx", "requests", "urllib", "socket", "http",      # network
        "playwright", "selenium",                              # browser
        "sqlite3", "store_db", "server",                       # database
        "os", "pathlib", "shutil", "subprocess", "tempfile",  # filesystem
        "openai", "anthropic", "ollama",                       # models
    }
    leaked = imported & forbidden_modules
    assert not leaked, f"resolver must stay pure; it imports {sorted(leaked)}"

    # Sibling modules that would drag a browser or a DOM in transitively.
    for module in imported:
        assert "locate_dom" not in module, "resolver must not import DOM code"
        assert "browser" not in module, "resolver must not import browser code"

    # And no I/O call sites, checked on docstring-stripped code.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(tree))
    for call in ("open(", "connect(", "read_text(", "write_text(", "urlopen(", "llm("):
        assert call not in code, f"resolver must stay pure; found {call}"


# ------------------------------------------------- classification, ordered

@pytest.mark.parametrize("label", [
    "Are you legally authorized to work in the United States?",
    "Are you authorized to work in the US?",
    "Do you have work authorization in Canada?",
    "Work Authorization",
    "Are you eligible to work in the United States without restriction?",
    "Do you have the right to work in Canada?",
    "Is your employment eligibility restricted in any way?",
])
def test_classifier_recognises_work_authorization_phrasings(label):
    assert classify(q(label)) == "work_auth"


@pytest.mark.parametrize("label", [
    "Do you now or will you in the future require immigration sponsorship to work at Cloudflare?",
    "Will you require visa sponsorship?",
    "Do you require sponsorship for employment?",
])
def test_classifier_recognises_sponsorship_phrasings(label):
    assert classify(q(label)) == "sponsorship"


@pytest.mark.parametrize("label", [
    "Are you a US citizen?",
    "Are you a citizen or permanent resident of Canada?",
    "Do you hold a green card?",
    "Nationality",
    "Do you have permanent residency in Canada?",
])
def test_classifier_recognises_citizenship_phrasings(label):
    assert classify(q(label)) == "citizenship"


@pytest.mark.parametrize("label", [
    "What is your current immigration status?",
    "Do you hold a valid US work permit?",
    "What is your visa status?",
    "What is your status to work in the US",
])
def test_classifier_separates_hold_a_document_from_are_you_authorized(label):
    """'Do you hold a valid US work permit?' is NOT answerable from
    us_work_auth="citizen" — a citizen holds no work permit. These live in
    their own kind rather than sharing an alternation with the mappable
    'are you authorized' phrasings."""
    assert classify(q(label)) == "work_document"


@pytest.mark.parametrize("label", [
    # Both name the country unambiguously, so the blank is caused by the
    # document/status split and NOT by the country gate — the first version of
    # this test used a label whose country was undetectable, so it passed
    # without exercising the split at all.
    "Do you hold a valid work permit for the United States?",
    "Do you hold a valid US work permit?",
])
def test_a_document_question_is_never_answered_from_a_status(label):
    from agents.job_applier.resolver import _country
    assert _country(label) == "us", "label must name a country for this test to bite"
    for status in ("citizen", "permanent_resident", "f1_opt", "tn_eligible"):
        ans = resolve([q(label)], {**FAKE, "us_work_auth": status})[0]
        assert (ans.source, ans.value) == ("blank", ""), status


@pytest.mark.parametrize("status", ["citizen", "permanent_resident", "f1_opt"])
def test_citizenship_is_never_answered_even_with_a_status_typed(status):
    """The profile records work authorization, not citizenship. Reading one as
    the other ('us_work_auth is citizen, so tick US citizen') is a chained
    guess that would put a false legal claim on a real application."""
    ans = resolve([sel("Are you a US citizen?", ["Yes", "No"])],
                  {**FAKE, "us_work_auth": status})[0]
    assert ans.source == "blank" and ans.value == ""
    assert ans.kind in BLOCKING_KINDS


def test_citizenship_in_a_textarea_is_not_routed_to_the_drafting_node():
    ans = resolve([q("Are you a US citizen?", kind="textarea")], FAKE)[0]
    assert ans.kind == "citizenship"
    assert "draft" not in ans.note.lower()


def test_a_yes_no_question_does_not_get_a_profile_value_pasted_into_it():
    """Every character would come from the profile and the answer would still
    be wrong: 'Do you currently live in Milwaukee?' is not asking for a city."""
    prof = {**FAKE, "location": "Toronto, ON"}
    ans = resolve([q("Do you currently live in Milwaukee?")], prof)[0]
    assert ans.kind == "location"
    assert ans.source == "blank" and ans.value == ""


def test_an_invitation_to_paste_a_value_still_gets_filled():
    """Guard against over-reach in the yes/no rule: Greenhouse phrases its
    LinkedIn field as 'Would you like to include…?', which IS asking for the
    value, so 'would'/'will' must stay out of the boolean-auxiliary set."""
    label = "Would you like to include your LinkedIn profile, personal website or blog?"
    assert resolve([q(label)], FAKE)[0].value == FAKE["linkedin_url"]


def test_every_work_auth_family_kind_is_blocking():
    assert {"work_auth", "sponsorship"} <= BLOCKING_KINDS
    assert isinstance(BLOCKING_KINDS, frozenset)


def test_work_auth_in_a_textarea_is_not_routed_to_the_drafting_node():
    """A model must never write a work-authorization answer, whatever the
    widget looks like: the auth rules are checked before the textarea rule."""
    ans = resolve([q("Are you authorized to work in the United States?", kind="textarea")], FAKE)[0]
    assert ans.kind == "work_auth"
    assert "draft" not in ans.note.lower()


def test_legal_name_if_different_is_not_captured_by_the_name_rule():
    """Ordering trap: the specific 'legal name (if different)' rule must win
    over the generic full-name rule, which would otherwise retype the name."""
    ans = resolve([q("Legal Name (if different than above)")], FAKE)[0]
    assert ans.kind == "name_alt"
    assert ans.source == "blank" and ans.value == ""


def test_graduation_date_wins_over_school_and_degree():
    assert classify(q("Expected graduation date from your university")) == "grad_date"
    assert classify(q("Expected graduation date (degree program)")) == "grad_date"


def test_relocation_wins_over_location():
    label = "Do you currently live or are you willing to relocate to the job's location?"
    assert classify(q(label)) == "relocation"
    assert resolve([q(label)], {**FAKE, "location": "Toronto, ON"})[0].source == "blank"


def test_linkedin_wins_over_the_generic_website_rule():
    label = "Would you like to include your LinkedIn profile, personal website or blog?"
    assert classify(q(label)) == "linkedin"
    assert resolve([q(label)], FAKE)[0].value == FAKE["linkedin_url"]


def test_word_boundaries_not_substrings():
    """`locations.py`'s lesson: a naive substring check matched 'uk' inside
    'Milwaukee'. Every rule here is word-boundary anchored."""
    assert classify(q("Are you comfortable using Schoology?")) == "other"
    assert classify(q("How did you hear about this job?")) == "referral_source"


def test_bare_name_label_only_matches_when_the_whole_label_is_name():
    assert classify(q("Name")) == "full_name"
    assert classify(q("Full Name")) == "full_name"
    assert classify(q("Current Company Name")) == "other"
    assert resolve([q("Current Employer Name")], FAKE)[0].value == ""


# ------------------------------------------- a label ABOUT a name, not FOR one

# Phrasings that ask about the FORM or SOUND of a name. The first entry is real:
# it is on the captured Lever fixture (Palantir), and until `name_meta` existed
# it classified as `full_name` and was auto-filled with the applicant's actual
# name — no note, not blocking. A wrong value in a real application is worse than
# a blank one, so this corpus is the invariant, not the one string.
NAME_META_CORPUS = [
    "Name Pronunciation | How do you pronounce your name?",
    "How do you pronounce your name?",
    "How do you pronounce your first name?",
    "HOW DO YOU PRONOUNCE YOUR NAME",
    "how would you pronounce your name?",
    "Name pronunciation",
    "Name Pronunciation (optional)",
    "Preferred name pronunciation",
    "Phonetic spelling of your name",
    "What is the phonetic pronunciation of your name?",
    "Please provide the phonetic spelling of your first name",
    "How do you say your name?",
    "How should we say your name?",
    "Spelling of your name (if unusual)",
    "How is your name spelled?",
    "Pronunciation of your first name",
    # `mis`-prefixed forms. A word boundary excluded these, so they fell through
    # to `full_name` and were auto-filled with the applicant's name — the same
    # defect the class exists to prevent. Found by mutation-testing the boundaries.
    "How often do people mispronounce your name?",
    "Common mispronunciation of your name",
    "Is your name frequently misspelled?",
    "How do people usually misspell your name?",
    "Is your name unpronounceable in English?",
    "Is your name commonly unpronounced correctly?",
]

# A meta token with NO name token. These must NOT be `name_meta`: the rule
# requires both halves, because matching the meta vocabulary alone made "How do
# you say hello in Spanish?" and "Please provide the correct spelling of your
# address" into `name_meta` — blank, which is safe, but with a note reading "this
# asks about how your name is said or spelled", which is untrue of those
# questions. A wrong explanation costs the user trust in every other note.
#
# The accepted cost: a field labelled only "Pronunciation", sitting next to a
# name field, loses its specific note and gets the generic one. It still goes
# blank, so nothing is typed wrongly — and asserting something possibly false
# about a question is worse than being unspecific about it.
NAME_META_NEAR_MISS_CORPUS = [
    "How do you say hello in Spanish?",
    "Please provide the correct spelling of your address",
    "What is the correct spelling of your username?",
    "How do you pronounce our company's product?",
    "Pronunciation",
    "Phonetic spelling",
]

# Labels that DO ask for a name (or for something else entirely) and must be
# completely unaffected. `name_meta` is first in the rule list, so if its pattern
# ever widens into a `\bname\b` denylist this is what catches it — every one of
# these would go blank and the resolver would stop doing its job.
NAME_REQUEST_CORPUS = [
    ("Name", "full_name"),
    ("Full name", "full_name"),
    ("Full Name", "full_name"),
    ("Your name", "full_name"),
    ("Legal name", "full_name"),
    ("Full legal name", "full_name"),
    ("Candidate Name", "full_name"),
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("Given name", "first_name"),
    ("Surname", "last_name"),
    ("Preferred Name | What would you like us to call you?", "name_alt"),
    ("Other Legal Name", "name_alt"),
    # Not `school`: the profile stores one (tertiary) school, and this names a
    # different one. See the `school_level` block at the end of this file.
    ("High School Name", "school_level"),
    ("Name of your university", "school"),
    ("Current Company Name", "other"),
]


@pytest.mark.parametrize("label", NAME_META_CORPUS)
def test_a_question_about_a_name_is_classified_name_meta(label):
    assert classify(q(label)) == "name_meta"


@pytest.mark.parametrize("label", NAME_META_CORPUS)
def test_a_question_about_a_name_is_never_answered_from_the_profile(label):
    """The whole point: blank, with a reason, for every phrasing — and the
    reason must not be the empty string, or the handoff has nothing to show."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.source == "blank"
    assert ans.value == ""
    assert ans.note.strip()
    assert RICH["full_name"] not in ans.note


@pytest.mark.parametrize("label", NAME_META_CORPUS)
def test_a_question_about_a_name_explains_itself_rather_than_falling_back(label):
    """Deleting the `name_meta` branch in `_resolve_one` still yields blank — the
    generic tail does that — so the safety outcome alone cannot tell the two
    apart. What differs is the note the handoff shows the human: "this asks about
    how your name is said, not for the name itself" versus "not derivable from a
    typed profile field". Compared against the generic note rather than pinned to
    a wording, so rephrasing is free but deleting the branch is not.
    """
    generic = resolve([q("Do you own a bicycle?")], RICH)[0]
    assert generic.kind == "other"  # sanity: this really is the fallback note
    ans = resolve([q(label)], RICH)[0]
    assert ans.note != generic.note


@pytest.mark.parametrize("label", NAME_META_CORPUS)
def test_a_question_about_a_name_is_never_handed_to_the_drafting_model(label):
    """`name_meta` also has to beat `free_text`, or "Please describe how you
    pronounce your name" as a textarea would be drafted — i.e. a model inventing
    a fact about the user."""
    ans = resolve([q(label, kind="textarea")], RICH)[0]
    assert ans.kind == "name_meta"
    assert ans.source == "blank"
    assert "draft" not in ans.note.lower()


@pytest.mark.parametrize("label,expected", NAME_REQUEST_CORPUS)
def test_a_question_asking_for_a_name_is_untouched(label, expected):
    assert classify(q(label)) == expected


@pytest.mark.parametrize("label", NAME_META_NEAR_MISS_CORPUS)
def test_a_meta_token_without_a_name_token_is_not_name_meta(label):
    """Both halves are required. Without this, the note shown to the human claims
    the question is about their name when it is about their address, a username,
    or Spanish."""
    assert classify(q(label)) != "name_meta"


@pytest.mark.parametrize("label", NAME_META_NEAR_MISS_CORPUS)
def test_a_near_miss_never_gets_the_name_meta_explanation(label):
    """The note is the thing at stake, so assert on the note directly: whatever
    these classify as, the human must not be told the question is about their
    name."""
    note = resolve([q(label)], RICH)[0].note.lower()
    assert "how your name is said" not in note


def test_a_prefixed_pronounce_stem_is_matched_but_a_prefixed_spell_stem_is_not():
    """The measured asymmetry, pinned. Checked against /usr/share/dict/words:
    every word containing "pronounc"/"pronunciation" non-initially is about
    pronouncing something (mispronounce, unpronounceable, repronounce), so a
    leading boundary there only produced false negatives — and a false negative
    fell through to `full_name` and typed the applicant's name in. Words
    containing "spell" non-initially are mostly NOT about spelling (gospellike,
    dispeller, bespell), so the boundary is protective there and stays.

    A word boundary is not automatically the safe choice; it depends on what the
    neighbouring words in the language actually are.
    """
    for label in ("Is your name unpronounceable in English?",
                  "How often do people mispronounce your name?",
                  "Common mispronunciation of your name"):
        assert classify(q(label)) == "name_meta", label
    # `spell` keeps its boundary: `mis` is allowed explicitly, others are not.
    assert classify(q("Is your name frequently misspelled?")) == "name_meta"
    assert classify(q("Which gospellike name do you prefer?")) != "name_meta"


def test_the_name_half_is_word_anchored_not_a_substring():
    """`\bnames?\b`, so "username" and "surname" do not satisfy it — dropping
    those boundaries is what makes a username field claim to be about a name."""
    assert classify(q("What is the correct spelling of your username?")) != "name_meta"
    assert classify(q("How do you pronounce your name?")) == "name_meta"


def test_the_name_fields_still_fill_after_the_name_meta_rule():
    got = {a.question.label: a.value for a in resolve(
        [q("Full name"), q("First Name"), q("Last Name"), q("Legal name")], FAKE)}
    assert got == {
        "Full name": "Testy McTestface",
        "First Name": "Testy",
        "Last Name": "McTestface",
        "Legal name": "Testy McTestface",
    }


def test_a_pronouns_question_is_not_relabelled_as_a_name_question():
    """`pronounc\\w*`, not `pronoun\\w*`. "Pronouns" is a
    protected-characteristic question with its own handling upstream; quietly
    reclassifying it as a name question would hide it from whatever screens for
    that."""
    for label in ("What are your pronouns?", "Preferred pronouns", "Pronouns"):
        assert classify(q(label)) != "name_meta", label


def test_name_meta_is_not_itself_a_submission_gate():
    """`name_meta` is blank-with-a-note, not a BLOCKING_KIND: a pronunciation
    field does not decide whether an application is considered, and padding
    BLOCKING_KINDS makes the handoff's "needs your attention" list less useful.

    It still surfaces when the FORM marks it required, via the separate
    "required field left blank" arm of `blocking` — which is the right reason to
    surface it, and is why an optional one stays quiet.
    """
    assert "name_meta" not in BLOCKING_KINDS
    optional = resolve([q("Name pronunciation", required=False)], RICH)
    assert blocking(optional) == []
    mandatory = resolve([q("Name pronunciation", required=True)], RICH)
    assert [a.kind for a in blocking(mandatory)] == ["name_meta"]


def test_real_lever_pronunciation_question_is_not_filled_with_a_name():
    """End-to-end against the captured Lever fixture, which is where this bug
    was found. Pure: parses saved HTML, no browser, no network."""
    from agents.job_applier.locate_dom import discover_questions

    lever = pathlib.Path(__file__).parent / "fixtures" / "ats" / "lever-form.html"
    questions = discover_questions(lever.read_text())
    pronunciation = next(qq for qq in questions if "Pronunciation" in qq.label)
    ans = resolve([pronunciation], RICH)[0]
    assert ans.kind == "name_meta"
    assert ans.value == ""
    assert ans.note.strip()
    # ...and its sibling, which asks FOR a name conditionally, is still name_alt.
    preferred = next(qq for qq in questions if qq.label.startswith("Preferred Name"))
    assert classify(preferred) == "name_alt"


def test_no_lever_question_is_auto_filled_with_the_profile_name_by_accident():
    """The invariant behind the specific bug: the applicant's name may only be
    typed into a field the resolver classified as a name REQUEST."""
    from agents.job_applier.locate_dom import discover_questions

    lever = pathlib.Path(__file__).parent / "fixtures" / "ats" / "lever-form.html"
    answers = resolve(discover_questions(lever.read_text()), RICH)
    name_kinds = {"full_name", "first_name", "last_name"}
    for ans in answers:
        if ans.value and RICH["full_name"] in ans.value:
            assert ans.kind in name_kinds, (ans.question.label, ans.kind)


def test_referral_source_is_blank_and_never_drafted():
    ans = resolve([q("How did you hear about this job?", kind="textarea")], FAKE)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "draft" not in ans.note.lower()


# --------------------------------------------------- work-auth enum mapping

def test_unambiguous_statuses_map_to_yes_on_an_authorization_question():
    label = "Are you legally authorized to work in the United States?"
    for status in ("citizen", "permanent_resident"):
        ans = resolve([q(label)], {**FAKE, "us_work_auth": status})[0]
        assert (ans.source, ans.value) == ("profile", "Yes"), status


def test_needing_sponsorship_maps_to_no_on_an_authorization_question():
    label = "Are you legally authorized to work in the United States?"
    ans = resolve([q(label)], {**FAKE, "us_work_auth": "needs_sponsorship"})[0]
    assert (ans.source, ans.value) == ("profile", "No")


def test_unambiguous_statuses_map_on_a_sponsorship_question():
    label = "Do you now or will you in the future require immigration sponsorship to work in the US?"
    assert resolve([q(label)], {**FAKE, "us_work_auth": "citizen"})[0].value == "No"
    assert resolve([q(label)], {**FAKE, "us_work_auth": "needs_sponsorship"})[0].value == "Yes"


@pytest.mark.parametrize("status", ["f1_opt", "tn_eligible"])
def test_conditional_statuses_never_assert_a_bare_yes_or_no(status):
    """F-1 OPT and TN eligibility depend on visa specifics this system does
    not model, so the resolver states the typed status instead of asserting a
    yes/no it cannot back up, and flags the answer for review."""
    label = "Are you legally authorized to work in the United States?"
    ans = resolve([q(label)], {**FAKE, "us_work_auth": status})[0]
    assert ans.source == "profile"
    assert ans.value not in ("Yes", "No")
    assert ans.value
    assert "review" in ans.note.lower()


@pytest.mark.parametrize("status", ["f1_opt", "tn_eligible"])
def test_conditional_status_text_asserts_no_nationality(status):
    """The stated status may encode only what the enum encodes. Rendering
    tn_eligible as "Canadian citizen, eligible for TN status" asserted a
    nationality the profile never stores — and TN covers Mexican citizens too,
    so it was outright false for some users."""
    label = "Are you legally authorized to work in the United States?"
    value = resolve([q(label)], {**FAKE, "us_work_auth": status})[0].value.lower()
    for claim in ("canadian", "american", "mexican", "citizen", "nationality"):
        assert claim not in value, value


@pytest.mark.parametrize("status", ["f1_opt", "tn_eligible"])
def test_conditional_statuses_are_blank_on_a_yes_no_select(status):
    """The dangerous case: a select can only hold one of its own options, so a
    conditional status has no honest option to pick. Blank, never 'closest'."""
    ans = resolve([sel("Are you authorized to work in the US?", ["Yes", "No"])],
                  {**FAKE, "us_work_auth": status})[0]
    assert ans.source == "blank" and ans.value == ""
    assert "authorization" in ans.note.lower()


def test_select_answer_must_be_one_of_the_offered_options():
    prof = {**FAKE, "us_work_auth": "citizen"}
    ans = resolve([sel("Are you authorized to work in the US?", ["Yes", "No"])], prof)[0]
    assert ans.value == "Yes"


def test_select_with_no_matching_option_goes_blank_not_closest():
    prof = {**FAKE, "us_work_auth": "citizen"}
    options = ["Yes, I am authorized and do not need sponsorship", "No", "Prefer not to say"]
    ans = resolve([sel("Are you authorized to work in the US?", options)], prof)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "option" in ans.note.lower()


def test_canada_question_reads_the_canadian_field_not_the_us_one():
    prof = {**FAKE, "us_work_auth": "needs_sponsorship", "ca_work_auth": "citizen"}
    ans = resolve([q("Are you legally authorized to work in Canada?")], prof)[0]
    assert ans.value == "Yes"


def test_lowercase_pronoun_us_is_not_read_as_the_united_states():
    """'work with us' must not be read as the country: the bare abbreviation
    is matched case-sensitively (the trick locations.py uses for state codes)."""
    prof = {**FAKE, "us_work_auth": "citizen"}
    ans = resolve([q("Do you have authorization to work with us?")], prof)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "country" in ans.note.lower()


def test_question_naming_two_countries_is_not_answered_from_one_field():
    prof = {**FAKE, "us_work_auth": "citizen"}
    ans = resolve([q("Are you authorized to work in the US or Canada?")], prof)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "country" in ans.note.lower()


# ------------------------------------------ needs_sponsorship 0/1 semantics

def test_needs_sponsorship_zero_on_an_empty_profile_means_unset_not_no():
    """The int defaults to 0, so a bare 0 with no work-auth status typed is
    'the user never told us', not 'the user does not need sponsorship'."""
    ans = resolve([q("Will you require visa sponsorship in the US?")],
                  {**FAKE, "needs_sponsorship": 0})[0]
    assert ans.source == "blank" and ans.value == ""
    assert "authorization" in ans.note.lower()


def test_the_global_flag_never_answers_a_country_specific_question():
    """`needs_sponsorship` is ONE country-agnostic checkbox. Answering "will
    you require sponsorship to work in Canada?" from it while ca_work_auth is
    unset is cross-wiring: the tick may only have been about the US."""
    for label in ("Will you require visa sponsorship in the US?",
                  "Will you require sponsorship to work in Canada?"):
        ans = resolve([q(label)], {**FAKE, "needs_sponsorship": 1})[0]
        assert (ans.source, ans.value) == ("blank", ""), label


@pytest.mark.parametrize("flag", ["", " ", "0", 0, False, None, "no", "off", 2, "banana"])
def test_needs_sponsorship_truthiness_is_an_allowlist(flag):
    """Only the values a deliberate tick produces count. A denylist read a
    whitespace-only value as "yes, I need sponsorship"; here anything outside
    the allowlist cannot even raise a conflict against a typed status."""
    ans = resolve([q("Will you require visa sponsorship in the US?")],
                  {**FAKE, "us_work_auth": "citizen", "needs_sponsorship": flag})[0]
    assert (ans.source, ans.value) == ("profile", "No"), flag


@pytest.mark.parametrize("flag", [1, True, "1", "true", "True", "yes"])
def test_needs_sponsorship_truthy_values_do_raise_the_conflict(flag):
    ans = resolve([q("Will you require visa sponsorship in the US?")],
                  {**FAKE, "us_work_auth": "citizen", "needs_sponsorship": flag})[0]
    assert ans.source == "blank" and "conflict" in ans.note.lower(), flag


def test_needs_sponsorship_contradicting_the_status_goes_blank():
    ans = resolve([q("Will you require visa sponsorship in the US?")],
                  {**FAKE, "us_work_auth": "citizen", "needs_sponsorship": 1})[0]
    assert ans.source == "blank" and ans.value == ""
    assert "conflict" in ans.note.lower()


# ----------------------------------------------------------- name splitting

def test_multi_word_given_name_survives_the_split():
    prof = {**FAKE, "full_name": "Mary Jane Watson"}
    a = {x.question.label: x.value for x in resolve([q("First Name"), q("Last Name")], prof)}
    assert a["First Name"] == "Mary Jane"
    assert a["Last Name"] == "Watson"


def test_single_token_name_yields_no_invented_surname():
    prof = {**FAKE, "full_name": "Testy"}
    a = {x.question.label: x for x in resolve([q("First Name"), q("Last Name")], prof)}
    assert a["First Name"].value == "Testy"
    assert a["Last Name"].source == "blank" and a["Last Name"].value == ""
    assert "profile" in a["Last Name"].note.lower()


def test_extra_whitespace_in_a_name_does_not_produce_empty_parts():
    prof = {**FAKE, "full_name": "  Testy   McTestface  "}
    a = {x.question.label: x.value for x in resolve([q("First Name"), q("Last Name")], prof)}
    assert a["First Name"] == "Testy" and a["Last Name"] == "McTestface"


# ------------------------------------------------- the sparse/real profile

def test_a_nearly_empty_profile_invents_nothing():
    questions = [q("First Name"), q("Last Name"), q("Full Name"), q("Email"), q("Phone"),
                 q("LinkedIn Profile"), q("Are you authorized to work in the US?"),
                 q("Will you need sponsorship in the US?")]
    for ans in resolve(questions, SPARSE):
        assert ans.source == "blank", ans.question.label
        assert ans.value == ""
        assert ans.note, "a blank answer must always say why"


def test_a_missing_profile_key_is_treated_as_unset_not_an_error():
    ans = resolve([q("Email"), q("Are you authorized to work in the US?")], {})
    assert [x.source for x in ans] == ["blank", "blank"]


# -------------------------------------------------------- shape / contracts

def test_resolve_returns_one_answer_per_question_in_order():
    questions = [q("Email"), q("Phone"), q("First Name")]
    out = resolve(questions, FAKE)
    assert [a.question.label for a in out] == [x.label for x in questions]
    assert all(isinstance(a, Answer) for a in out)


def test_the_resolver_never_emits_the_drafted_source():
    labels = ["Email", "Why do you want to work here?", "Resume/CV", "Anything else?"]
    out = resolve([q(x, kind="textarea") for x in labels], FAKE)
    assert {a.source for a in out} <= {"profile", "blank"}
    assert "drafted" in SOURCES


def test_resolve_does_not_mutate_the_profile():
    before = dict(FAKE)
    resolve([q("First Name"), q("Are you authorized to work in the US?")], FAKE)
    assert FAKE == before


# ------------------------------------------------ against the real fixture

def _fixture_questions():
    return parse_questions(json.loads(FIXTURE.read_text()))


def test_real_greenhouse_form_never_gets_an_off_menu_value():
    for ans in resolve(_fixture_questions(), FAKE):
        if ans.question.kind in ("select", "checkbox") and ans.value:
            assert ans.value in ans.question.options, ans.question.label


def test_real_greenhouse_form_leaves_uploads_and_consent_to_the_human():
    by_kind = {a.kind for a in resolve(_fixture_questions(), FAKE)}
    assert "file_upload" in by_kind and "consent" in by_kind
    for ans in resolve(_fixture_questions(), FAKE):
        if ans.kind in ("file_upload", "consent"):
            assert ans.source == "blank" and ans.value == ""
            assert ans.kind in BLOCKING_KINDS


# ===========================================================================
# Phrasing corpora + invariants
# ===========================================================================
# The lesson from this module's first review: every safety test pinned the one
# phrasing its author thought of, so eight unthought-of phrasings leaked real
# values through a green suite. These corpora exist so the assertions are
# INVARIANTS over a family of phrasings rather than examples. Adding a phrasing
# to a corpus and watching the invariant fail is the test doing its job.

# Work-eligibility phrasings: both spellings (authoriz/authoris), ALL-CAPS /
# Title Case / lowercase, with and without a trailing "?", US / Canada / UK /
# no country, and the whole vocabulary — authorized to work, require
# sponsorship, visa status, right to work, work permit, citizenship, residency.
ELIGIBILITY_CORPUS = [
    "Are you legally authorized to work in the United States?",
    "ARE YOU LEGALLY AUTHORIZED TO WORK WITH US?",
    "are you legally authorized to work in the us?",
    "Are you legally authorised to work in the US?",
    "Are you legally authorised to work in the UK?",
    "ARE YOU AUTHORISED TO WORK IN CANADA",
    "Describe your work authorisation",
    "Describe your work authorization",
    "Tell us about your visa situation",
    "Work Authorization",
    "work authorisation status",
    "WORK AUTHORIZATION STATUS",
    "Do you require sponsorship?",
    "DO YOU REQUIRE SPONSORSHIP TO WORK WITH US?",
    "Will you require visa sponsorship now or in the future?",
    "do you now or will you in the future require immigration sponsorship?",
    "Do you have the right to work in Canada?",
    "DO YOU HAVE THE RIGHT TO WORK IN THE UNITED STATES",
    "Do you hold a valid US work permit?",
    "What is your current immigration status?",
    "What is your visa status",
    "Are you a US citizen?",
    "ARE YOU A CANADIAN CITIZEN?",
    "Nationality",
    "Do you have permanent residency in Canada?",
    "Do you hold a green card?",
    "Are you a citizen or permanent resident of Canada?",
    "Are you eligible to work in the United States without restriction?",
    "Do you have work authorization in Canada?",
    "What is your status to work in the US",
    "Do you now or will you in the future require immigration sponsorship to work at Cloudflare?",
    "Is your employment eligibility restricted in any way?",
    "Are you authorized to work in the US or Canada?",
    "Are you authorized to work anywhere in North America?",
]

# Boolean-phrased questions that merely MENTION a topic the profile stores.
# Every one must be blank — the profile value is not the answer, however much
# it looks like one. Run against a profile with every field filled, so a blank
# cannot come from missing data.
BOOLEAN_MENTION_CORPUS = [
    "Do you currently live in Milwaukee",
    "Do you currently live in Milwaukee?",
    "Will you be graduating before June 2027?",
    "Have you ever applied under a different last name?",
    "Have you ever used a different first name?",
    "Is your degree accredited?",
    "Did you attend a university outside your home country?",
    "Do you have a LinkedIn profile?",
    "Is this your current phone number?",
    "Was your email address different when you last applied?",
    "Are you located in the same city as this role?",
    "Does your school offer a co-op program?",
    "Must your degree be from an accredited institution?",
    "Should we contact your school directly?",
    "Has your address changed in the last year?",
    "Are you known by any other full name",
]

# Near-misses that must STILL be answered: the value-prompt allowlist has to
# stay useful, or the resolver fills nothing at all.
VALUE_PROMPT_CORPUS = [
    ("First Name", "Testy"),
    ("Last Name", "McTestface"),
    ("Full Name", "Testy McTestface"),
    ("Email", "testy.mctestface@example.invalid"),
    ("Phone", "+1-555-0100"),
    ("What is your email address?", "testy.mctestface@example.invalid"),
    ("Would you like to include your LinkedIn profile?", FAKE["linkedin_url"]),
    ("Would you like to include your LinkedIn profile, personal website or blog?",
     FAKE["linkedin_url"]),
    ("Can you provide your phone number?", "+1-555-0100"),
    ("Please provide your email address", "testy.mctestface@example.invalid"),
    ("School", "University of Waterloo"),
    ("Expected graduation date", "2027-04"),
]

# EVERY field filled, work-auth statuses included, and `needs_sponsorship`
# left at 0 so the profile does not contradict itself. Both details matter: an
# earlier version of this constant left the statuses unset, which made every
# eligibility assertion below pass for the weak reason "nothing was typed"
# rather than because a guard held. A mutation run caught that.
RICH = {**FAKE, "github_url": "https://github.com/example-invalid",
        "portfolio_url": "https://example.invalid",
        "location": "Toronto, ON", "summary": "x",
        "us_work_auth": "citizen", "ca_work_auth": "citizen",
        "needs_sponsorship": 0}

# Corpus entries that name NO single country: a pronoun ("with us"), no country
# at all, a third country, or two at once. None may be answered from either
# work-auth field however complete the profile is — one field cannot answer a
# question that isn't about one country.
NO_SINGLE_COUNTRY_CORPUS = [
    "ARE YOU LEGALLY AUTHORIZED TO WORK WITH US?",
    "DO YOU REQUIRE SPONSORSHIP TO WORK WITH US?",
    "Are you legally authorised to work in the UK?",
    "Do you require sponsorship?",
    "Will you require visa sponsorship now or in the future?",
    "do you now or will you in the future require immigration sponsorship?",
    "Do you now or will you in the future require immigration sponsorship to work at Cloudflare?",
    "Is your employment eligibility restricted in any way?",
    "Are you authorized to work in the US or Canada?",
    "Are you authorized to work anywhere in North America?",
]


@pytest.mark.parametrize("label", ELIGIBILITY_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_no_eligibility_phrasing_is_ever_free_text(label, kind):
    """A work-eligibility question must never reach the drafting node, in any
    spelling, casing or widget. This is what makes Task 5's "refuse to draft a
    BLOCKING_KINDS question" rule load-bearing."""
    assert classify(q(label, kind=kind)) != "free_text"


@pytest.mark.parametrize("label", ELIGIBILITY_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_every_eligibility_phrasing_is_blocking(label, kind):
    assert classify(q(label, kind=kind)) in BLOCKING_KINDS


@pytest.mark.parametrize("label", ELIGIBILITY_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_no_eligibility_phrasing_is_answered_from_an_unset_field(label, kind):
    ans = resolve([q(label, kind=kind)], SPARSE)[0]
    assert ans.source == "blank" and ans.value == ""
    assert ans.note


@pytest.mark.parametrize("label", ELIGIBILITY_CORPUS)
def test_no_eligibility_phrasing_ever_lands_off_menu_on_a_select(label):
    """Across every WORK_AUTH value and both widget kinds, a select may only
    ever hold one of its own options."""
    for status in _ALL_STATUSES:
        for widget in ("select", "checkbox"):
            prof = {**RICH, "us_work_auth": status, "ca_work_auth": status}
            ans = resolve([sel(label, ["Yes", "No"], kind=widget)], prof)[0]
            assert ans.value in ("", "Yes", "No")
            if ans.value:
                assert ans.value in ans.question.options, (label, status)


@pytest.mark.parametrize("label", NO_SINGLE_COUNTRY_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea", "select"])
def test_no_single_country_means_no_answer_however_full_the_profile(label, kind):
    assert label in ELIGIBILITY_CORPUS, "keep this list a subset of the corpus"
    question = (sel(label, ["Yes", "No"]) if kind == "select" else q(label, kind=kind))
    ans = resolve([question], RICH)[0]
    assert ans.source == "blank" and ans.value == ""
    assert "country" in ans.note.lower() or "yes/no" in ans.note.lower()


@pytest.mark.parametrize("label", BOOLEAN_MENTION_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_a_boolean_question_mentioning_a_profile_topic_is_always_blank(label, kind):
    ans = resolve([q(label, kind=kind)], RICH)[0]
    assert ans.source == "blank", f"{label!r} was answered {ans.value!r}"
    assert ans.value == ""
    assert ans.note


@pytest.mark.parametrize("label,expected", VALUE_PROMPT_CORPUS)
def test_a_value_prompt_is_still_answered(label, expected):
    ans = resolve([q(label)], FAKE)[0]
    assert ans.source == "profile", ans.note
    assert ans.value == expected


# The literal reproduction set from this module's first review: every one of
# these returned a real value (or reached the drafting node) through a green
# suite. Kept verbatim and run against a FULLY populated profile, so nothing
# here can pass by accident of missing data.
REVIEW_LEAK_CORPUS = [
    "ARE YOU LEGALLY AUTHORIZED TO WORK WITH US?",
    "DO YOU REQUIRE SPONSORSHIP TO WORK WITH US?",
    "Describe your work authorisation",
    "Tell us about your visa situation",
    "Do you currently live in Milwaukee",
    "Will you be graduating before June 2027?",
    "Have you ever applied under a different last name?",
    "Do you hold a valid US work permit?",
    "Legal first name (if different than above)",
    "Legal name (if applicable)",
    "Other Legal Name",
]


@pytest.mark.parametrize("label", REVIEW_LEAK_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_no_reviewed_leak_phrasing_returns_a_value(label, kind):
    ans = resolve([q(label, kind=kind)], RICH)[0]
    assert ans.source == "blank", f"{label!r} leaked {ans.value!r}"
    assert ans.value == ""
    assert classify(q(label, kind=kind)) != "free_text"


# The British-spelling findings whose bug was MISclassification, not a leak:
# these are well-formed, country-named yes/no questions, so answering them
# "Yes" from a typed status is correct. What must never happen is the original
# behaviour — classifying as free_text and handing a work-authorization answer
# to a model — or inventing prose instead of the mapped Yes/No.
REVIEW_ORTHOGRAPHY_CORPUS = [
    "Are you legally authorised to work in the US?",
    "ARE YOU AUTHORISED TO WORK IN CANADA",
    "Are you legally authorized to work in the United States?",
]


@pytest.mark.parametrize("label", REVIEW_ORTHOGRAPHY_CORPUS)
@pytest.mark.parametrize("kind", ["text", "textarea"])
def test_both_spellings_are_handled_identically_and_never_drafted(label, kind):
    question = q(label, kind=kind)
    assert classify(question) != "free_text"
    assert classify(question) in BLOCKING_KINDS
    ans = resolve([question], RICH)[0]
    assert ans.value in ("", "Yes"), ans.value
    assert resolve([question], SPARSE)[0].value == ""


# ----------------------------------------------------------------- blocking()

def test_blocking_lists_every_blocking_kind_and_every_required_blank():
    questions = [
        q("Email"),                                          # filled, not blocking
        q("Are you authorized to work in the US?"),           # blocking kind
        q("Are you a US citizen?"),                           # blocking kind
        q("Do you hold a valid US work permit?"),             # blocking kind
        q("Will you require sponsorship in the US?"),          # blocking kind
        q("I acknowledge the privacy policy", kind="checkbox"),  # blocking kind
        q("Resume/CV", kind="file"),                          # blocking kind
        q("How did you hear about this job?"),                # required + blank
        q("Desired salary", required=False),                  # optional + blank
    ]
    got = {a.question.label for a in blocking(resolve(questions, FAKE))}
    assert "Email" not in got
    assert "Desired salary" not in got
    assert got == {
        "Are you authorized to work in the US?", "Are you a US citizen?",
        "Do you hold a valid US work permit?", "Will you require sponsorship in the US?",
        "I acknowledge the privacy policy", "Resume/CV",
        "How did you hear about this job?",
    }


def test_blocking_includes_a_work_auth_answer_that_did_resolve():
    """A filled work-auth answer is still confirmed, not assumed: it decides
    whether the application is considered at all."""
    prof = {**FAKE, "us_work_auth": "citizen"}
    answers = resolve([q("Are you authorized to work in the US?")], prof)
    assert answers[0].value == "Yes"
    assert blocking(answers) == answers


def test_blocking_is_empty_when_nothing_needs_a_human():
    answers = resolve([q("Email"), q("First Name")], FAKE)
    assert blocking(answers) == []


# ------------------------------------------------------------ name suffixes

def test_a_generational_suffix_stays_with_the_surname():
    prof = {**FAKE, "full_name": "Testy McTestface Jr."}
    a = {x.question.label: x.value for x in resolve([q("First Name"), q("Last Name")], prof)}
    assert a["First Name"] == "Testy"
    assert a["Last Name"] == "McTestface Jr."


def test_real_greenhouse_form_fills_only_the_identity_fields_it_has():
    got = {a.question.label: a.value for a in resolve(_fixture_questions(), FAKE) if a.value}
    assert got == {
        "First Name": "Testy",
        "Last Name": "McTestface",
        "Email": "testy.mctestface@example.invalid",
        "Phone": "+1-555-0100",
        "Would you like to include your LinkedIn profile, personal website or blog?":
            FAKE["linkedin_url"],
    }


# =========================================================================
# A label naming a school level the profile does not store
# =========================================================================
# The principle is NOT `name_meta`'s ("a label asking ABOUT an attribute is not
# asking FOR it"). It is: **a label naming a specific INSTANCE of an attribute
# is not asking for the instance the profile happens to store.** The profile
# holds one school, with one degree and one graduation date all describing that
# same school; "High School Name" asks for a different school entirely, and the
# resolver cannot know the answer.
#
# Found through the Task 7 handoff report, which is what made it visible: both
# broken labels resolved with `source="profile"`, so they were filed under
# "filled and verified" — the collapsed band the user is least likely to open.
# A wrong school on a real application with nothing drawing her eye to it.

SCHOOL_LEVEL_CORPUS = [
    "High School Name",
    "Year of High School Graduation",
    "high-school name",
    "Highschool attended",
    "What high school did you attend?",
    "Name of your high school",
    "Secondary School",
    "Secondary education",
    "Middle School",
    "Grade school",
    "Grammar School",
    "Elementary School",
    "Primary school",
    "Junior High",
    "Junior High School",
    "Senior High School",
    "Prep School",
    "Preparatory School",
    "Sixth Form",
    "Sixth form college",
    # ABOVE tertiary as well as below it: "graduate" as an ADJECTIVE names a
    # level of study, and the profile's single school cannot vouch for a second,
    # postgraduate institution. Every one of these was MEASURED returning the
    # profile's graduation DATE before the fix.
    "Graduate School",
    "Graduate program",
    "Graduate programme",
    "Graduate degree",
    "Graduate studies",
    "Graduate coursework",
    "Graduate institution",
    "Graduate education",
    "Graduate level",
    "Graduate transcripts",
    "Graduate admissions",
    "Are you a graduate student?",
    "Post-graduate studies",
    "Postgraduate school",
    "Postgrad",
    "Post-grad degree",
    "Grad school",
    # Inflections. These were REJECTED by a hard trailing word boundary until
    # mutation testing exposed it — see `test_an_inflected_school_level_phrasing_is_still_caught`.
    "Where did you do your high schooling?",
    "Are you a high schooler?",
    "Middle schooling",
    "Primary schooling",
    "Elementary schooling",
    "Secondary educational background",
]

# Every one of these must keep resolving to the profile's school/degree/date.
# Tasks 4 and 5 both had to learn that a token is not safe because it looks
# safe: "school" inside "Schoology", "uk" inside "Milwaukee". The equivalents
# here are "School" inside "Other (School Not Listed)" — which sits inside the
# one Lever question that is CORRECTLY answered from the profile — and "high"
# inside "Highest level of education completed".
SCHOOL_KEPT_CORPUS = [
    ("School", "school"),
    ("School Name", "school"),
    ("University", "school"),
    ("College or University", "school"),
    ("Institution", "school"),
    ("Alma mater", "school"),
    ("Name of your university", "school"),
    ("Education", "school"),
    ("School of Engineering", "school"),
    ("Highest level of education completed", "school"),
    # Deliberately NOT school_level: these name a level the profile plausibly
    # DOES hold, so blanking them would break the case the resolver exists for.
    ("Undergraduate School", "school"),
    ("Undergraduate Institution", "school"),
    # The real Lever question, which must keep working — it contains the word
    # "School" and is the reason a bare `\bschool\b` denylist was not the fix.
    ('Which university are you currently attending or did you last attend? '
     'Please select "Other (School Not Listed)" if your school is not listed.', "school"),
    ("Expected graduation date from your university", "grad_date"),
    ("Degree", "degree"),
    ("Field of study", "degree"),
]


@pytest.mark.parametrize("label", SCHOOL_LEVEL_CORPUS)
def test_a_school_level_the_profile_does_not_store_is_classified_school_level(label):
    assert classify(q(label)) == "school_level"


@pytest.mark.parametrize("label", SCHOOL_LEVEL_CORPUS)
def test_a_school_level_the_profile_does_not_store_is_never_answered(label):
    """Blank, with a reason, for every phrasing — and the profile's school must
    not be the value under any of them."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.kind == "school_level"
    assert ans.source == "blank"
    assert ans.value == ""
    assert ans.note.strip()


@pytest.mark.parametrize("label", SCHOOL_LEVEL_CORPUS)
def test_a_school_level_question_explains_itself_rather_than_falling_back(label):
    """Deleting the `school_level` branch in `_resolve_one` still yields blank —
    the generic tail does that — so the safety outcome alone cannot tell a
    working rule from a deleted one. The NOTE is what distinguishes them, and
    the note is what the handoff shows: it has to say what the profile holds and
    why it was not used, not merely that nothing was filled."""
    ans = resolve([q(label)], RICH)[0]
    assert "level of schooling your profile does not store" in ans.note
    assert RICH["school"] in ans.note, "the note must say what it DOES hold"
    assert "not derivable from a typed profile field" not in ans.note


@pytest.mark.parametrize("label", SCHOOL_LEVEL_CORPUS)
def test_a_school_level_question_never_receives_any_profile_value(label):
    """Not just the school: the DEGREE and the GRADUATION DATE describe the same
    tertiary institution, so neither is an answer to a question about a
    different school either. "Year of High School Graduation" is the live case
    — it is a select whose options are years, and the profile's university
    graduation year is one of them."""
    years = [str(y) for y in range(2020, 2032)]
    question = Question(key="k", label=label, required=True, kind="select", options=years)
    ans = resolve([question], {**RICH, "grad_date": "2027"})[0]
    assert ans.value == ""
    for field in ("school", "degree", "grad_date"):
        assert not RICH.get(field) or RICH[field] not in (ans.value or "x" * 99)


@pytest.mark.parametrize("label,expected", SCHOOL_KEPT_CORPUS)
def test_the_school_questions_that_were_right_are_still_right(label, expected):
    assert classify(q(label)) == expected


@pytest.mark.parametrize("label,expected", SCHOOL_KEPT_CORPUS)
def test_the_school_questions_that_were_right_still_resolve_from_the_profile(label, expected):
    """The other half. A rule that blanks everything is safe and useless; this
    fails if the new rule is widened until the resolver stops doing its job."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.source == "profile", label
    assert ans.value


def test_the_school_level_rule_is_ordered_ahead_of_grad_date_not_merely_ahead_of_school():
    """MEASURED, and it corrects an assumption: "Year of High School Graduation"
    was NOT already resolving correctly.

    It classified as `grad_date` — which is ordered ahead of `school` — and with
    a profile `grad_date` of "2027" against the real Lever question's options
    (the years 2020-2031) it selected the UNIVERSITY graduation year for the
    HIGH SCHOOL one. It only ever looked fine with a `grad_date` like
    "2027-04"/"May 2027", which matches no option verbatim and is blanked by the
    option gate — i.e. by luck of formatting, not by the ordering.

    So placing the rule between `grad_date` and `school` fixes the name and
    leaves the year. This test fails under exactly that mis-ordering.
    """
    years = [str(y) for y in range(2020, 2032)]
    question = Question(key="k", label="Year of High School Graduation",
                        required=True, kind="select", options=years)
    ans = resolve([question], {**RICH, "grad_date": "2027"})[0]
    assert ans.kind == "school_level"
    assert ans.value == "" and ans.source == "blank"


def test_the_school_level_tokens_are_anchored_on_word_boundaries():
    r"""`agents/job_scraper/locations.py` learned this the hard way when a
    substring check for "uk" matched inside "Milwaukee".

    The last two entries are the ones that are LOAD-BEARING rather than merely
    tidy, and mutation testing is what separated them from the rest. Every
    alternative in this rule ends in "school"/"education" and therefore takes a
    `\w*` suffix — except "junior high" and "sixth form", which do not, and
    which would swallow "junior highlights" and "sixth formation" if the inline
    boundary after them were dropped. Removing either `\b` fails here.
    """
    for label in ("Highest level of education completed",
                  "Do you have a Schoology account?",
                  "Preschooling philosophy",
                  "Highlight your best work",
                  "Junior highlights of your career",
                  "Sixth formation of the team"):
        assert classify(q(label)) != "school_level", label


@pytest.mark.parametrize("label,plain", [
    ("Where did you do your high schooling?", "high school"),
    ("Are you a high schooler?", "high school"),
    ("Middle schooling", "middle school"),
    ("Primary schooling", "primary school"),
    ("Elementary schooling", "elementary school"),
    ("Secondary educational background", "secondary education"),
])
def test_an_inflected_school_level_phrasing_is_still_caught(label, plain):
    r"""A hard trailing `\b` on "school"/"education" was not protective, it was
    WRONG — measured against /usr/share/dict/words, it protected against zero
    real words while rejecting six real phrasings, every one of which asks about
    exactly the school level this rule exists to refuse. Each would therefore
    have been answered with the profile's university.

    Pinned as pairs so the test also shows the plain form still works, i.e. that
    the fix widened the rule rather than replacing one gap with another.
    """
    assert classify(q(label)) == "school_level", label
    assert classify(q(plain)) == "school_level", plain
    assert resolve([q(label)], RICH)[0].value == ""


def test_no_lever_question_is_auto_filled_with_the_profile_school_by_accident():
    """The invariant, end-to-end on the captured Lever form: the profile's school
    may only be typed into a field the resolver classified as a school REQUEST —
    never into one whose label names a level of schooling the profile does not
    store. This is the shape that catches the NEXT sibling of this bug rather
    than only this one. Pure: parses saved HTML, no browser, no network.
    """
    from agents.job_applier.locate_dom import discover_questions

    lever = pathlib.Path(__file__).parent / "fixtures" / "ats" / "lever-form.html"
    questions = discover_questions(lever.read_text())
    answers = resolve(questions, {**RICH, "grad_date": "2027"})

    # Not vacuous: the fixture really does carry both shapes.
    kinds = [a.kind for a in answers]
    assert kinds.count("school_level") == 2, "the fixture must exercise the new rule"
    assert "school" in kinds, "and must still contain a real school question"

    for ans in answers:
        if ans.value and RICH["school"] in ans.value:
            assert ans.kind == "school", (ans.question.label, ans.kind)
        if ans.kind == "school_level":
            assert ans.value == "", ans.question.label


# =========================================================================
# The same stem is two different words, and only one of them is a date
# =========================================================================
# "graduatION" is the EVENT noun — an event happens at a time, so it is always a
# date question. "graduatE" is a LEVEL OF STUDY when it modifies a noun, and a
# verb only in a clause. `graduat\w*` could not tell them apart and MEASURED
# with a real profile it answered ELEVEN adjectival phrasings with the
# graduation date, "Graduate School" and "Graduate school GPA" among them: a
# date typed into a school-name field and into a numeric one.
#
# This is the fourth instance on this branch of one token being two concepts
# (name_meta, name_alt, school_level, and now this), which is why the tests
# below are written as the INVARIANT as well as the cases.

# Every phrasing that must KEEP resolving to a date. The first five are the ones
# the fix was explicitly forbidden to regress; the rest are the neighbours a
# careless narrowing would take with them.
GRAD_DATE_CORPUS = [
    "Expected graduation date",
    "Year of Graduation",
    "Anticipated graduation",
    "Graduation year",
    "When do you expect to graduate",
    "Expected graduation date from your university",
    "Expected graduation date (degree program)",
    "Graduation month",
    "Graduation Date",
    "Date of graduation",
    "What year will you graduate?",
    "When will you graduate?",
    "What year do you graduate?",
    "Grad date",
    "Expected completion",
]

# Classified `grad_date`, but deliberately NOT answered: `_is_value_prompt`
# blanks it because it reads as a yes/no question, not a request for a value.
# This is one of the eight leaks the module docstring records ("Will you be
# graduating before June 2027?" answered with a date), so it is pinned
# separately rather than dropped from the corpus — the narrowing must not turn
# it back into a date, and must not turn it into a filled one either.
GRAD_DATE_YES_NO_CORPUS = [
    "Are you graduating before June 2027?",
    "Will you be graduating before June 2027?",
    "Have you graduated yet?",
]

GPA_CORPUS = [
    "GPA",
    "Cumulative GPA",
    "What is your GPA?",
    "G.P.A.",
    "Grade point average",
    "High School GPA",
    "Undergraduate GPA",
    "Graduate school GPA",
]


@pytest.mark.parametrize("label", GRAD_DATE_CORPUS)
def test_the_date_phrasings_that_must_not_regress(label):
    """The narrowing is easy to over-correct into breaking the common case.
    These are the common case."""
    assert classify(q(label)) == "grad_date", label
    assert resolve([q(label)], RICH)[0].value == RICH["grad_date"], label


def test_the_grad_date_corpus_is_the_size_resolver_py_says_it_is():
    """`resolver.py` cited this corpus as
    `test_the_five_date_phrasings_that_must_not_regress` — a test that has never
    existed, naming a count that was never right. It is fifteen phrasings, and the
    "first five" the comment was reaching for are a subset of them, not the whole.

    A citation to a nonexistent test is worse than none: the convention in this
    package is that each documented decision names the test that fails when it
    stops being true, and one dead name makes the whole convention unreliable.
    """
    assert len(GRAD_DATE_CORPUS) == 15
    assert len(set(GRAD_DATE_CORPUS)) == 15, "no duplicates padding the count"
    source = pathlib.Path(resolver.__file__).read_text()
    assert "test_the_date_phrasings_that_must_not_regress" in source
    assert "test_the_five_date_phrasings_that_must_not_regress" not in source
    # Every test name resolver.py cites has to exist — the same guard fill.py and
    # handoff.py already carry, now applied to the module where the dead citation
    # was found. Scanned over the WHOLE suite rather than this file: resolver.py
    # legitimately cites `test_the_resolvers_file_upload_note_does_not_contradict
    # _the_attach`, which lives in test_applier_locate.py because that is where the
    # executor's résumé path is tested. A per-file guard would have failed on a
    # citation that is perfectly good.
    cited = set(re.findall(r"\btest_[a-z0-9_]+", source))
    defined: set[str] = set()
    for path in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
        defined |= set(re.findall(r"^def (test_[a-z0-9_]+)", path.read_text(), re.M))
    assert cited, "resolver.py should name its pins"
    assert cited <= defined, f"resolver.py cites tests that do not exist: {sorted(cited - defined)}"


def test_the_high_school_year_options_are_the_ones_the_comment_measured():
    """The evidence for ordering `school_level` ahead of `grad_date`.

    The comment said the options on Lever's "Year of High School Graduation"
    question "are the years 2020-2031". They are 2020-2030 plus a literal
    "Other" — eleven years and an escape hatch, not twelve years. The point the
    comment is making needs the profile's university year to BE selectable, so the
    right range matters: 2027 is in this list, which is what made the leak real.
    """
    lever = (pathlib.Path(__file__).parent / "fixtures" / "ats" / "lever-form.html").read_text()
    control = next(
        c for c in parse_controls(lever) if c.label == "Year of High School Graduation"
    )
    assert control.options == [str(y) for y in range(2020, 2031)] + ["Other"]
    assert "2027" in control.options, "the leak needed the university year to be selectable"
    source = pathlib.Path(resolver.__file__).read_text()
    assert '2020-2030 plus "Other"' in source


@pytest.mark.parametrize("label", GRAD_DATE_YES_NO_CORPUS)
def test_a_yes_no_question_about_graduating_is_still_recognised_but_still_blank(label):
    """Both halves. It must stay `grad_date` — a narrowing that pushed it into
    `other` would lose the explanation the handoff shows — and it must stay
    unanswered, because `_is_value_prompt` is what stopped "Will you be
    graduating before June 2027?" being answered with a date in the first
    place."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.kind == "grad_date", label
    assert ans.source == "blank" and ans.value == "", label
    assert "yes/no question" in ans.note


@pytest.mark.parametrize("label", [
    "Graduate School", "Graduate program", "Graduate degree", "Graduate studies",
    "Graduate coursework", "Graduate institution", "Graduate education",
    "Graduate level", "Are you a graduate student?", "Post-graduate studies",
    "Postgraduate school", "Grad school", "Recent graduate?", "Graduate school GPA",
])
def test_graduate_the_adjective_is_never_a_date(label):
    """The whole point: none of these is asking when she finishes, so none of
    them may receive the date. Asserted on the VALUE, not only the kind — a
    future rule that reclassified these but still copied `grad_date` would pass
    a kind-only test."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.kind != "grad_date", label
    assert ans.value == "", label
    assert RICH["grad_date"] not in (ans.value or "\x00"), label


@pytest.mark.parametrize("label", GPA_CORPUS)
def test_no_gpa_field_is_ever_answered_from_a_date(label):
    """A GPA is not a date, not a school and not a degree, and the profile does
    not record one. "Graduate school GPA" was measured returning the graduation
    date into a numeric field."""
    ans = resolve([q(label)], RICH)[0]
    assert ans.kind == "gpa", label
    assert ans.value == "", label
    assert "GPA" in ans.note or "grade" in ans.note


def test_the_gpa_rule_is_ordered_ahead_of_the_school_level_rule():
    """"High School GPA" is both. GPA wins on purpose: telling her "your profile
    has no GPA" names the thing the field actually wants, where the school-level
    note would explain the wrong half of the question."""
    assert classify(q("High School GPA")) == "gpa"
    assert classify(q("Graduate school GPA")) == "gpa"
    assert classify(q("High School Name")) == "school_level"


def test_the_leading_boundary_is_what_keeps_undergraduate_working():
    r"""MEASURED, and it reverses a finding from the previous round.

    When `school_level` held only pre-tertiary vocabulary, the leading `\b` was
    measured EQUIVALENT — zero of /usr/share/dict/words' 235,976 entries embed
    any of those phrases — and the mutant removing it was reported as genuinely
    unkillable rather than papered over with a contrived negative.

    Adding "graduate school" changed that in one line: **"undergraduate school"
    contains "graduate school"**. Without the boundary, "Undergraduate School"
    matches `school_level` and goes blank, breaking a case that must keep
    resolving from the profile.

    Whether a boundary is load-bearing is a property of the VOCABULARY, not of
    the regex. An equivalent mutant is only equivalent for the alternation it
    was measured against.
    """
    for label in ("Undergraduate School", "Undergraduate program",
                  "Undergraduate Institution", "Undergraduate degree"):
        assert classify(q(label)) != "school_level", label
    # ...while the deliberately-spelled-out `post` form still lands.
    assert classify(q("Postgraduate school")) == "school_level"


# ---------------------------------------------------------------- the invariant

# What each profile field IS, so that "a value of the wrong kind" is checkable
# without an oracle for every label. Two independent vocabularies: one for
# labels that ask WHEN, one for labels that name a THING. A label that does both
# ("Year of High School Graduation") is excluded from both directions and is
# covered by `school_level`'s own tests instead.
_ASKS_WHEN = re.compile(
    r"\b(?:date|dates|year|years|month|months|day|when|term|semester|quarter)\b",
    re.IGNORECASE)
_NAMES_A_THING = re.compile(
    r"\b(?:school|schools|university|universities|college|institution|program"
    r"|programme|degree|major|gpa|discipline|concentration)\b",
    re.IGNORECASE)


def _every_known_label():
    """Every label this suite can lay hands on: all three captured boards, the
    Greenhouse JSON payload, and every corpus in this file. Deliberately wide —
    the point of an invariant is to be checked against labels nobody wrote it
    for."""
    from agents.job_applier.locate_dom import (
        discover_questions,
        excluded_eeo_questions,
        unreadable_questions,
    )

    ats = pathlib.Path(__file__).parent / "fixtures" / "ats"
    out = []
    for board in ("lever", "greenhouse", "ashby"):
        html = (ats / f"{board}-form.html").read_text()
        out += discover_questions(html) + unreadable_questions(html) + excluded_eeo_questions(html)
    out += _fixture_questions()
    out += [q(lbl) for lbl in SCHOOL_LEVEL_CORPUS + GRAD_DATE_CORPUS + GPA_CORPUS]
    out += [q(lbl) for lbl, _ in SCHOOL_KEPT_CORPUS]
    out += [q(lbl) for lbl in ("Graduate School", "Graduate school GPA",
                               "Graduate program", "Graduate degree",
                               "Recent graduate?", "Undergraduate School")]
    return out


def test_no_profile_value_lands_in_a_field_of_a_different_kind():
    """THE INVARIANT, stated once instead of case by case.

    A label that names a THING and does not ask WHEN must never receive the
    profile's graduation date; a label that asks WHEN and names no thing must
    never receive the school, the degree or the name. This is what catches the
    FIFTH instance of this family without anyone finding it by hand — it is not
    a list of the four we know about.

    Before the fix it failed on "Graduate School", "Graduate program",
    "Graduate degree" and "Graduate school GPA", each having received
    `grad_date`.
    """
    profile = {**RICH, "grad_date": "2027"}
    date_value = profile["grad_date"]
    thing_values = {profile[f] for f in ("school", "degree", "full_name") if profile.get(f)}

    checked_things = checked_dates = 0
    for question in _every_known_label():
        label = question.label or ""
        ans = resolve([question], profile)[0]
        if not ans.value:
            continue
        asks_when = bool(_ASKS_WHEN.search(label))
        names_thing = bool(_NAMES_A_THING.search(label))
        if names_thing and not asks_when:
            checked_things += 1
            assert ans.value != date_value, (
                f"a DATE was put in {label!r}, which names a thing", ans.kind)
        if asks_when and not names_thing:
            checked_dates += 1
            assert ans.value not in thing_values, (
                f"a non-date profile field was put in {label!r}, which asks when", ans.kind)

    # Non-vacuous in BOTH directions, or the assertions above prove nothing.
    assert checked_things >= 5, checked_things
    assert checked_dates >= 1, checked_dates


def test_a_profile_field_is_only_ever_copied_into_its_own_kind():
    """The mechanism half of the same invariant: whenever an answer's value IS a
    profile field verbatim, the kind the resolver assigned must be the kind that
    field belongs to. Guards `_resolve_one` growing a branch that reads the
    wrong profile key — the failure the label-level test above cannot see,
    because a wrong key with a right-looking label passes it.
    """
    from agents.job_applier.resolver import _PROFILE_FIELD_BY_KIND

    profile = {**RICH, "grad_date": "2027"}
    # `first_name`/`last_name` are derived from `full_name`, so they are named
    # explicitly rather than being absent from the map.
    allowed = {**{k: v for k, v in _PROFILE_FIELD_BY_KIND.items()},
               "first_name": "full_name", "last_name": "full_name"}
    by_value = {}
    for field, value in profile.items():
        if isinstance(value, str) and len(value) > 3:
            by_value.setdefault(value, set()).add(field)

    seen = 0
    for question in _every_known_label():
        ans = resolve([question], profile)[0]
        fields = by_value.get(ans.value or "\x00")
        if not fields:
            continue
        seen += 1
        assert allowed.get(ans.kind) in fields, (
            ans.question.label, ans.kind, allowed.get(ans.kind), fields)
    assert seen >= 5, seen


# ------------------------------------------------- co-op / study work permit
#
# Kayla is an international co-op student at Waterloo: she holds a co-op work
# permit, which authorizes work in CANADA and needs no sponsorship there. None
# of the original six WORK_AUTH values said that. Two of them — `f1_opt` and
# `tn_eligible` — are US immigration categories, and `tn_eligible` was actually
# stored in `ca_work_auth`, where it produced the note "Eligible for TN status
# under USMCA" on Canadian forms. TN is about working in the US.


def test_the_enum_offers_a_status_for_a_co_op_or_study_work_permit():
    from profile_store import WORK_AUTH

    assert "coop_permit" in WORK_AUTH


def test_a_co_op_permit_answers_canadian_work_authorization():
    prof = {**FAKE, "ca_work_auth": "coop_permit"}
    ans = resolve([sel("Are you legally authorized to work in Canada?", ["Yes", "No"])], prof)[0]
    assert ans.source == "profile"
    assert ans.value == "Yes"


def test_a_co_op_permit_needs_no_sponsorship_in_canada():
    prof = {**FAKE, "ca_work_auth": "coop_permit"}
    ans = resolve(
        [sel("Will you now or in the future require sponsorship to work in Canada?", ["Yes", "No"])],
        prof,
    )[0]
    assert ans.source == "profile"
    assert ans.value == "No"


def test_a_co_op_permit_says_nothing_about_the_united_states():
    """The permit is Canadian. A US question must stay blank even though the
    Canadian field is confidently set — the same country gate that stops
    `f1_opt` answering a Canadian question."""
    prof = {**FAKE, "ca_work_auth": "coop_permit"}
    for label in ("Are you legally authorized to work in the United States?",
                  "Will you require sponsorship to work in the US?"):
        ans = resolve([sel(label, ["Yes", "No"])], prof)[0]
        assert (ans.source, ans.value) == ("blank", ""), label


def test_a_co_op_permit_is_never_read_as_citizenship():
    """Holding a work permit is the opposite of citizenship: a citizen needs no
    permit. Inferring one from the other would put a false legal claim on a real
    application."""
    ans = resolve([sel("Are you a Canadian citizen?", ["Yes", "No"])],
                  {**FAKE, "ca_work_auth": "coop_permit"})[0]
    assert (ans.source, ans.value) == ("blank", "")


def test_a_co_op_permit_is_still_surfaced_for_the_human_to_confirm():
    """Every eligibility answer stays blocking. The value changes what is
    SUGGESTED, never what is typed."""
    prof = {**FAKE, "ca_work_auth": "coop_permit"}
    ans = resolve([sel("Are you legally authorized to work in Canada?", ["Yes", "No"])], prof)[0]
    assert ans.question.kind in ("select", "checkbox")
    assert classify(ans.question) in BLOCKING_KINDS


def test_the_enumerated_status_tests_cover_every_value_the_enum_offers():
    """These tests hardcode status lists. Deriving the guard from the enum means
    adding a seventh value cannot silently leave it untested — which is exactly
    what adding the sixth-to-seventh value did to the list in
    `test_no_eligibility_phrasing_ever_lands_off_menu_on_a_select`."""
    from profile_store import WORK_AUTH

    assert set(_ALL_STATUSES) == set(WORK_AUTH)


# ------------------------------------------------------- Kayla's 2026-08-05 rulings
# From the handoff of the first real assisted-apply run (Cloudflare, greenhouse).
# Recorded in docs/superpowers/specs/2026-08-05-applier-answer-rulings.md.

RESIDENT = {**FAKE, "location": "Waterloo, ON, Canada", "grad_date": "2028-04"}


def test_a_bare_country_field_is_answered_from_the_location():
    """Reported as "Country — not derivable from a typed profile field". It is
    derivable: the country is the last segment of `location`. The label is a bare
    "Country", which `location`'s own pattern does not match."""
    ans = resolve([q("Country")], RESIDENT)[0]
    assert ans.source == "profile"
    assert ans.value == "Canada"


def test_a_country_dropdown_gets_the_option_not_the_raw_string():
    ans = resolve([sel("Country", ["United States", "Canada", "Other"])], RESIDENT)[0]
    assert ans.value == "Canada" and ans.source == "profile"


def test_country_is_blank_when_the_location_has_no_country_in_it():
    ans = resolve([q("Country")], {**FAKE, "location": "Waterloo"})[0]
    assert (ans.source, ans.value) == ("blank", "")


def test_how_did_you_hear_picks_the_company_website_option():
    """Ruling: the scraper fetches from official ATS boards, so "company
    website" is true by construction — not a guess about her behaviour."""
    ans = resolve([sel("How did you hear about this job?",
                       ["LinkedIn", "Company website", "Referral", "Other"])], RESIDENT)[0]
    assert ans.source == "profile"
    assert ans.value == "Company website"


def test_how_did_you_hear_stays_blank_when_no_option_says_website():
    """Default-deny: no matching option, no answer. It must not fall back to
    "Other", which is a different claim."""
    ans = resolve([sel("How did you hear about this job?",
                       ["LinkedIn", "Referral", "Career fair", "Other"])], RESIDENT)[0]
    assert (ans.source, ans.value) == ("blank", "")


def test_how_did_you_hear_is_never_free_texted():
    """A text version must not be handed to the drafting model, which would
    invent a plausible-sounding origin story."""
    ans = resolve([q("How did you hear about this job?", kind="textarea")], RESIDENT)[0]
    assert ans.source == "blank"


def test_currently_enrolled_and_returning_is_answered_from_a_future_grad_date():
    """Reported as blank because it classified as `school` and a yes/no question
    is not a prompt for the school's NAME. It is answerable: a student whose
    graduation is still ahead of her is enrolled and returning."""
    ans = resolve([sel("Are you currently enrolled in a university or program and "
                       "will return to the program upon completion of internship?",
                       ["Yes", "No"])],
                  RESIDENT)[0]
    assert ans.source == "profile"
    assert ans.value == "Yes"


def test_currently_enrolled_stops_saying_yes_once_the_grad_date_has_passed():
    """Keyed on the date rather than hardcoded, so it stops answering by itself
    instead of quietly lying after she graduates."""
    ans = resolve([sel("Are you currently enrolled in a university or program and "
                       "will return to the program upon completion of internship?",
                       ["Yes", "No"])],
                  {**RESIDENT, "grad_date": "2019-04"})[0]
    assert (ans.source, ans.value) == ("blank", "")


def test_currently_enrolled_needs_a_school_as_well_as_a_date():
    ans = resolve([sel("Are you currently enrolled in a university or program and "
                       "will return to the program upon completion of internship?",
                       ["Yes", "No"])],
                  {**RESIDENT, "school": ""})[0]
    assert (ans.source, ans.value) == ("blank", "")


def test_asking_which_school_still_asks_for_the_name_not_a_yes_no():
    """The enrollment rule must not swallow the plain school question."""
    ans = resolve([q("School")], RESIDENT)[0]
    assert ans.source == "profile" and ans.value == "University of Waterloo"
