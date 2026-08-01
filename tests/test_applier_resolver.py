"""The resolver is the safety boundary: it may only answer from typed profile
fields. Anything it cannot answer becomes source='blank' with a reason, which
the handoff shows the user. It must never guess a name, an email, or — above
all — a work-authorization answer."""
from __future__ import annotations

import json
import pathlib

import pytest

from agents.job_applier.resolver import (
    BLOCKING_KINDS,
    SOURCES,
    Answer,
    blocking,
    classify,
    resolve,
)
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
    ("High School Name", "school"),
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
    """Across all six WORK_AUTH values and both widget kinds, a select may only
    ever hold one of its own options."""
    for status in ("", "citizen", "permanent_resident", "f1_opt", "tn_eligible",
                   "needs_sponsorship"):
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
