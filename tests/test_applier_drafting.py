"""The drafting node is the only part of Phase B that puts machine-written words
into a real employer's form. Two things therefore have to be true of every path
through it, and this file is where they are pinned rather than asserted in prose:

  * a model-written answer is ALWAYS marked — `source="drafted"` AND
    `DRAFT_MARKER` inside `.value`, so a caller reading only the string still
    sees it;
  * no answer ever contains a fact about the user that did not come from the
    user's own stored data. Every path that cannot ground an answer returns
    `source="blank"` with a reason, including a model that is down, empty,
    terse, or honest about not knowing.

HERMETIC BY CONSTRUCTION: `no_real_model` (autouse) points this module's `llm`
at a function that fails the test. Ollama is up on the development machine, so a
test that forgot to stub the model would otherwise pass by silently calling it —
and then break on any machine without it, or quietly assert nothing at all about
prompt content. A test that wants a model stubs one explicitly.

The free-text corpus is the REAL captured Lever form
(`tests/fixtures/ats/lever-form.html`, provenance in `test_applier_locate.py`),
not invented labels — the questions this module has to get right are the awkward
ones a human wrote, and those are hard to imagine convincingly.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import config
import profile_store
from agents.job_applier import drafting
from agents.job_applier.drafting import (
    DRAFTABLE_TOPICS,
    DRAFT_MARKER,
    TOPICS,
    build_prompt,
    draft,
    draft_one,
    is_marked,
    topic_of,
)
from agents.job_applier.locate_dom import discover_questions
from agents.job_applier.resolver import BLOCKING_KINDS, SOURCES, classify
from agents.job_applier.schema_greenhouse import Question

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ats" / "lever-form.html"

# Fake, and obviously so: `.invalid` is reserved by RFC 2606 and 555-01xx by
# NANP for fiction. Nothing here is the user's real data.
FAKE = {
    "full_name": "Testy McTestface",
    "email": "testy.mctestface@example.invalid",
    "phone": "+1-555-0100",
    "location": "Milwaukee, WI",
    "linkedin_url": "https://www.linkedin.com/in/example-invalid",
    "github_url": "https://github.example.invalid/testy",
    "portfolio_url": "https://testy.example.invalid",
    "school": "University of Waterloo",
    "degree": "Computer Engineering (3rd year)",
    "grad_date": "2027-04",
    "us_work_auth": "", "ca_work_auth": "", "needs_sponsorship": 0,
    "summary": "Third-year computer engineering student who likes data plumbing.",
}

JOB = {
    "title": "Software Engineer Intern",
    "company": "Palantir",
    "location": "Palo Alto, CA",
    "description": "UNIQUE_JD_TOKEN — you will build data integration pipelines "
                   "and work directly with forward-deployed engineers.",
}

EXPERIENCE = (
    "### MASTER RESUME\nTesty McTestface | testy.mctestface@example.invalid | "
    "+1-555-0100\nBuilt a SQLite-backed job tracker with a LangGraph agent pipeline."
)

RESEARCH = "UNIQUE_RESEARCH_TOKEN — the company builds data platforms for institutions."

# A plausible model reply: long enough to clear the minimum, no invented facts.
GOOD_DRAFT = (
    "I want to work here because the posting describes data integration work that "
    "matches what I have actually built, and I would like to do it at a larger scale."
)


def q(label, *, required=True, kind="textarea", options=()):
    return Question(
        key=label[:24].lower().replace(" ", "_") or "k",
        label=label, required=required, kind=kind, options=list(options),
    )


class ModelWasCalled(BaseException):
    """Raised by the hermeticity guard when a test reaches the model unstubbed.

    Derived from `BaseException`, NOT `Exception`, and that is the whole point:
    `draft_one` catches `Exception` deliberately (a model failure must become a
    blank, never a traceback), which means an `AssertionError` from the guard was
    being swallowed into a perfectly innocent-looking blank answer. A mutation
    that routed the "give us three numbers" question straight to the model still
    passed every refusal test in this file, because the guard's own failure was
    caught and reported as the refusal the test was looking for.
    """


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Any unstubbed model call fails the test instead of reaching Ollama."""
    def _forbidden(*args, **kwargs):
        raise ModelWasCalled(
            "this test called the real model; stub drafting.llm or pass llm_fn"
        )
    monkeypatch.setattr(drafting, "llm", _forbidden)


def stub(reply="", *, raises=None, record=None):
    """A fake `llm`. `record` collects the kwargs of every call for assertions."""
    def _fake(role, prompt, **kwargs):
        if record is not None:
            record.append({"role": role, "prompt": prompt, **kwargs})
        if raises is not None:
            raise raises
        return reply
    return _fake


# What an ungrounded local model actually does with "give us three numbers that
# describe you": it makes three up. Used by `calls` so a refusal test fails loudly
# if the question ever reaches a model.
FABRICATION = (
    "My three numbers are 7, 42 and 1,300: seven side projects shipped, forty-two "
    "pull requests merged last term, and 1,300 hours logged on my own compiler."
)


@pytest.fixture
def calls(monkeypatch):
    """A model that always returns a fabricated answer, plus the log of every call.

    A refusal test asserts this log stayed EMPTY. Showing the answer came back
    blank is not the same as showing the question never reached the model, and
    only the second is the safety property — `draft_one` turns any model
    exception into a blank, so a refusal that silently broke into "called the
    model, model blew up, returned blank" looks identical from the outside.
    """
    log = []
    monkeypatch.setattr(drafting, "llm", stub(FABRICATION, record=log))
    return log


# --------------------------------------------------------------- brief tests


def test_a_drafted_answer_is_marked(monkeypatch):
    """`source="drafted"` AND the marker inside the returned text. Both, because
    a caller that reads `.value` alone must still see a machine wrote it."""
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT))
    answer = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    assert answer.source == "drafted"
    assert DRAFT_MARKER in answer.value
    assert is_marked(answer.value)
    assert GOOD_DRAFT in answer.value
    assert answer.note, "a drafted answer explains itself too"


def test_the_marker_is_the_first_thing_in_the_value(monkeypatch):
    """Not merely present — FIRST. A marker buried after 200 words is a marker a
    skimming human misses, which is the whole failure this guards against."""
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT))
    answer = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    assert answer.value.startswith(DRAFT_MARKER)


def test_the_marker_literal_in_code_and_in_this_file_agree():
    """The cheap drift guard (copied from `test_applier_locate.py`'s marker-list
    test). If someone reworded the marker, every OTHER test here would still pass
    because they all read the constant — this one would not."""
    assert DRAFT_MARKER == "[AI-DRAFTED — REVIEW AND EDIT THIS BEFORE YOU SUBMIT]"
    assert "AI" in DRAFT_MARKER and "DRAFT" in DRAFT_MARKER.upper()


def test_a_model_failure_yields_a_blank_with_a_reason_never_a_fabrication(monkeypatch):
    """`shell.model_router.llm` raises on transport failure. The answer must be
    empty and explained, not a plausible paragraph written from nothing."""
    monkeypatch.setattr(drafting, "llm", stub(raises=RuntimeError("connection refused")))
    answer = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    assert (answer.value, answer.source) == ("", "blank")
    assert "connection refused" in answer.note
    assert DRAFT_MARKER not in answer.note


@pytest.mark.parametrize("reply", ["", "   ", "\n\n\t ", None])
def test_an_empty_model_reply_yields_a_blank(monkeypatch, reply):
    monkeypatch.setattr(drafting, "llm", stub(reply))
    answer = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    assert (answer.value, answer.source) == ("", "blank")
    assert answer.note


def test_a_one_word_model_reply_yields_a_blank(monkeypatch):
    """"N/A" typed into a real application is worse than an empty box, and it
    would otherwise be typed WITH the marker glued to it."""
    monkeypatch.setattr(drafting, "llm", stub("N/A"))
    answer = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    assert (answer.value, answer.source) == ("", "blank")
    assert "N/A" in answer.note


def test_the_model_saying_it_cannot_ground_an_answer_yields_a_blank(monkeypatch):
    """The system prompt gives the model an explicit escape hatch. Taking it must
    produce a blank, not a form field containing the word NOT_ENOUGH_INFORMATION."""
    monkeypatch.setattr(drafting, "llm", stub("NOT_ENOUGH_INFORMATION"))
    answer = draft_one(
        q("What has been your favorite project or proudest accomplishment? Why?"),
        job=JOB, profile=FAKE, experience=EXPERIENCE,
    )
    assert (answer.value, answer.source) == ("", "blank")


def test_the_prompt_carries_the_jd_and_the_research(monkeypatch):
    record = []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=record))
    draft_one(
        q("Why do you want to work at Palantir?"),
        job=JOB, profile=FAKE, company_research=RESEARCH,
    )
    prompt = record[0]["prompt"]
    assert "UNIQUE_JD_TOKEN" in prompt
    assert "UNIQUE_RESEARCH_TOKEN" in prompt
    assert "Palantir" in prompt and "Software Engineer Intern" in prompt


def test_the_prompt_never_carries_the_phone_or_the_email(monkeypatch):
    record = []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=record))
    draft_one(
        q("What has been your favorite project or proudest accomplishment? Why?"),
        job=JOB, profile=FAKE, experience=EXPERIENCE, company_research=RESEARCH,
    )
    prompt = record[0]["prompt"]
    assert FAKE["email"] not in prompt
    assert FAKE["phone"] not in prompt
    assert "555" not in prompt, "nor a re-punctuated form of the same number"
    assert "@" not in prompt, "no email in any shape, from any source"


def test_the_experience_pool_is_scrubbed_not_just_the_profile(monkeypatch):
    """The experience pool IS the master résumé, whose header is a name, an email
    and a phone number. A profile-field allowlist alone leaks both, which is why
    the assembled prompt gets a second pass."""
    record = []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=record))
    resume = "Jane Doe\njane.doe@example.invalid\n555-0142\nBuilt a compiler."
    draft_one(
        q("What has been your favorite project or proudest accomplishment? Why?"),
        job=JOB, profile={**FAKE, "phone": "555-0142"}, experience=resume,
    )
    prompt = record[0]["prompt"]
    assert "jane.doe@example.invalid" not in prompt
    assert "555-0142" not in prompt
    assert "Built a compiler." in prompt, "the grounding text itself survives"


def test_scrubbing_does_not_eat_ordinary_numbers():
    """Over-redaction destroys the grounding text the feature depends on. Salary
    ranges, dates and headcounts are digits-with-separators too."""
    text = "raised $100,000 - 150,000 on 2026-07-31 with a team of 5,000 in 2027"
    assert drafting._scrub_contact(text, FAKE) == text


def test_no_profile_field_outside_the_allowlist_reaches_the_prompt():
    """Sentinel every field in `profile_store.FIELDS`, so a field ADDED to the
    profile later is caught by this test rather than silently prompted."""
    sentinels = {f: f"SENTINELXX{f.upper()}" for f in profile_store.FIELDS}
    prompt = build_prompt(
        q("Why do you want to work at Palantir?"), "motivation",
        job=JOB, profile=sentinels, experience=EXPERIENCE, company_research=RESEARCH,
    )
    for field, sentinel in sentinels.items():
        if field in drafting._PROMPT_PROFILE_FIELDS:
            assert sentinel in prompt, f"allowlisted {field} should be grounded"
        else:
            assert sentinel not in prompt, f"{field} leaked into the model prompt"


def test_the_allowlist_cannot_contain_a_contact_or_eligibility_field():
    """Structural half of the same guarantee: even before any scrubbing, the
    fields drafting is allowed to read exclude contact details outright — and
    exclude work eligibility, which a model must never see, let alone restate."""
    forbidden = {
        "email", "phone", "full_name", "location",
        "linkedin_url", "github_url", "portfolio_url",
        "us_work_auth", "ca_work_auth", "needs_sponsorship",
    }
    assert not forbidden & set(drafting._PROMPT_PROFILE_FIELDS)
    assert set(drafting._PROMPT_PROFILE_FIELDS) <= set(profile_store.FIELDS)


# ------------------------------------------------- the router refuses things


BLOCKING_EXAMPLES = {
    # All textareas on purpose: absent the resolver's eligibility quarantine
    # every one of these would classify as `free_text` and land here.
    "work_auth": "Are you legally authorized to work in the United States?",
    "sponsorship": "Will you now or in the future require visa sponsorship?",
    "citizenship": "Are you a citizen of the United States?",
    "work_document": "Do you hold a valid US work permit?",
    "consent": "I consent to the privacy policy and terms and conditions",
    "file_upload": "Resume/CV",
}


def test_the_blocking_examples_cover_every_blocking_kind():
    """If a new kind joins `BLOCKING_KINDS`, the refusal test below must grow
    with it rather than silently skipping it."""
    assert set(BLOCKING_EXAMPLES) == set(BLOCKING_KINDS)


@pytest.mark.parametrize("kind,label", sorted(BLOCKING_EXAMPLES.items()))
def test_every_blocking_kind_is_refused_by_the_router(kind, label, calls):
    question = q(label, kind="file" if kind == "file_upload" else "textarea")
    assert classify(question) == kind, "the example must actually be this kind"
    assert topic_of(question) == "blocked"
    answer = draft_one(question, job=JOB, profile=FAKE, experience=EXPERIENCE)
    assert (answer.value, answer.source) == ("", "blank")
    assert answer.kind == kind
    assert answer.note
    assert calls == [], "a blocking question must never reach the model at all"


def test_work_authorization_is_refused_in_the_casings_that_once_slipped_through():
    """The two phrasings `resolver.py` records as having leaked. Pinned here too,
    because drafting is the layer that would have written an answer to them."""
    for label in (
        "ARE YOU LEGALLY AUTHORIZED TO WORK WITH US?",
        "Are you legally authorised to work in the US?",
    ):
        assert topic_of(q(label)) == "blocked"


def test_a_question_the_resolver_owns_is_not_drafting_s_business(calls):
    for label in ("First Name", "Email Address", "How did you hear about this job?"):
        question = q(label, kind="text")
        assert topic_of(question) == "not_free_text"
        assert draft_one(question, job=JOB, profile=FAKE).source == "blank"
    assert calls == []


def test_name_meta_still_beats_free_text_so_it_never_reaches_the_model(calls):
    """`name_meta` is first in the resolver's `_LABEL_RULES` precisely so this
    textarea phrasing cannot be handed to a model, which would invent a
    pronunciation. Pinned from this side too — reordering those rules breaks a
    property of THIS module."""
    question = q("Please describe how you pronounce your name", kind="textarea")
    assert classify(question) == "name_meta"
    assert topic_of(question) == "not_free_text"
    assert draft_one(question, job=JOB, profile=FAKE).source == "blank"
    assert calls == []


# ------------------------------------------- the real captured Lever corpus


def lever_free_text():
    questions = discover_questions(FIXTURE.read_text(encoding="utf-8"))
    return [x for x in questions if classify(x) == "free_text"]


# label prefix -> expected topic. Written against the seven `free_text` questions
# the real Palantir form actually yields.
LEVER_EXPECTED = [
    ("Give us three numbers that describe you", "unknown"),
    ("What's something you know an unreasonable amount about", "unknown"),
    ("What has been your favorite project or proudest accomplishment", "experience"),
    ("Why do you want to work at Palantir?", "motivation"),
    ("Prompt 1: Show us something you", "not_prose"),
    ("Prompt 2: Tell us about a piece of Palantir coverage", "not_prose"),
    ("Additional information", "unknown"),
]


def test_the_fixture_still_yields_the_seven_free_text_questions_these_tests_assume():
    labels = [x.label for x in lever_free_text()]
    assert len(labels) == 7
    for prefix, _ in LEVER_EXPECTED:
        assert any(label.startswith(prefix) for label in labels), prefix


@pytest.mark.parametrize("prefix,expected", LEVER_EXPECTED)
def test_each_real_lever_free_text_question_routes_as_expected(prefix, expected):
    question = next(x for x in lever_free_text() if x.label.startswith(prefix))
    assert topic_of(question) in TOPICS
    assert topic_of(question) == expected


def test_the_three_numbers_question_is_never_answered(calls):
    """"Give us three numbers that describe you — a stat from your life, a score,
    a streak…" is unanswerable from anything this system stores, and a model asked
    it will invent three. It must not reach the model at all."""
    question = next(
        x for x in lever_free_text() if x.label.startswith("Give us three numbers")
    )
    answer = draft_one(question, job=JOB, profile=FAKE, experience=EXPERIENCE,
                       company_research=RESEARCH)
    assert (answer.value, answer.source) == ("", "blank")
    assert answer.note
    assert calls == [], "it must not reach the model, which would invent three"


@pytest.mark.parametrize("prefix", [p for p, t in LEVER_EXPECTED if t == "unknown"])
def test_an_unrecognised_free_text_question_never_reaches_the_model(prefix, calls):
    """Default-deny, stated as a property rather than as three anecdotes. These
    are the real Lever prompts nothing recognises — three numbers from your life,
    a non-technical obsession, a blank "Additional information" box. A model handed
    any of them writes something plausible and false about the applicant."""
    question = next(x for x in lever_free_text() if x.label.startswith(prefix))
    answer = draft_one(question, job=JOB, profile=FAKE, experience=EXPERIENCE,
                       company_research=RESEARCH)
    assert (answer.value, answer.source) == ("", "blank")
    assert answer.note
    assert calls == []


def test_the_video_prompts_are_refused_and_the_reason_says_why(calls):
    """The section heading ("submit a URL to an unlisted YouTube video") is not
    part of the label, so the only evidence is "(90 seconds max)". The note quotes
    what it matched, so the user can check the claim."""
    for prefix in ("Prompt 1: Show us something you", "Prompt 2: Tell us about a piece"):
        question = next(x for x in lever_free_text() if x.label.startswith(prefix))
        answer = draft_one(question, job=JOB, profile=FAKE, experience=EXPERIENCE)
        assert (answer.value, answer.source) == ("", "blank")
        assert "90 seconds" in answer.note
        assert "paragraph" in answer.note
    assert calls == [], "a box that wants a YouTube URL must not reach the model"


def test_not_prose_is_checked_before_the_draftable_topics():
    """"Show us something you've built" matches the `experience` rule outright, so
    if the ordering in `topic_of` flipped, a paragraph would be drafted into a box
    that wants a YouTube URL. This is the test that fails when it flips."""
    label = next(
        x.label for x in lever_free_text() if x.label.startswith("Prompt 1:")
    )
    assert drafting._EXPERIENCE_RE.search(drafting._normalize(label)), (
        "premise: this label DOES look like an experience question"
    )
    assert topic_of(q(label)) == "not_prose"


@pytest.mark.parametrize("label,wants", [
    ("Please share a link to something you made", "a URL or a link"),
    ("Record a video introduction and tell us about yourself",
     "a video or audio recording"),
    ("Upload a writing sample and describe your process", "a file or an attachment"),
    ("How many years of Python experience do you have? Describe them.",
     "a number, a date or a single figure"),
])
def test_other_non_prose_shapes_are_refused(label, wants):
    assert topic_of(q(label)) == "not_prose"
    assert wants in drafting.not_prose_reason(label)


@pytest.mark.parametrize("label", [
    "Tell us about a time when you disagreed with a teammate",
    "Describe a project you are proud of in 200 words",
    "Do you have a criminal record? Please explain.",
])
def test_a_prose_question_is_not_mistaken_for_a_recording(label):
    """Three near-misses of the non-prose rules: a bare "time", a WORD budget
    (not a time budget), and "record" as a noun. A note telling the user any of
    these wants a video would simply be false."""
    assert drafting.not_prose_reason(label) == ""


# --------------------------------------------------- grounding an experience


PROJECT_Q = "What has been your favorite project or proudest accomplishment? Why?"


def test_a_project_question_is_blank_when_there_is_no_experience_to_draw_on(calls):
    """Same call `resume_generator`'s gather node makes: with an empty pool the
    only possible answer is a fabricated one."""
    answer = draft_one(q(PROJECT_Q), job=JOB, profile=FAKE, experience="   ")
    assert (answer.value, answer.source) == ("", "blank")
    assert "experience pool" in answer.note
    assert calls == []


def test_a_project_question_is_drafted_when_the_experience_pool_has_content(monkeypatch):
    record = []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=record))
    answer = draft_one(q(PROJECT_Q), job=JOB, profile=FAKE, experience=EXPERIENCE)
    assert answer.source == "drafted" and is_marked(answer.value)
    assert "SQLite-backed job tracker" in record[0]["prompt"]


def test_a_motivation_question_does_not_need_the_experience_pool(monkeypatch):
    """It is grounded in the posting and the company research, which is what makes
    it the one topic answerable with no facts about the applicant at all."""
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT))
    answer = draft_one(
        q("Why do you want to work at Palantir?"),
        job=JOB, profile={}, experience="", company_research=RESEARCH,
    )
    assert answer.source == "drafted"


# ------------------------------------------------------------ the model call


def test_the_call_uses_the_local_role_and_a_bounded_budget(monkeypatch):
    record = []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=record))
    draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    call = record[0]
    assert call["role"] == "local" and call["role"] in config.MODEL_ROLES
    assert call["max_tokens"] == drafting._MAX_TOKENS
    assert 0 <= call["temperature"] <= 0.5
    assert call["system"] == drafting.SYSTEM_PROMPT


def test_the_system_prompt_forbids_invention_and_offers_a_way_out():
    system = drafting.SYSTEM_PROMPT.lower()
    for word in ("never invent", "only facts", "not_enough_information"):
        assert word in system, word


def test_the_worst_case_prompt_fits_the_pinned_context():
    """`config.OLLAMA_NUM_CTX` is pinned at 32768 tokens and this prompt is capped
    in characters, so the relationship is arithmetic and worth stating. A prompt
    that overran it would not error — Ollama would silently drop the end of it."""
    # Measured 2026-07-31: config.OLLAMA_NUM_CTX is 32768 here. Asserted against
    # the LIVE value rather than that literal, because it is env-overridable and a
    # suite that fails on a memory-tighter machine teaches nobody anything;
    # config's own documented floor is ~10,000.
    assert config.OLLAMA_NUM_CTX >= 10_000
    huge = "x" * 100_000
    prompt = build_prompt(
        q(huge), "experience",
        job={"title": huge, "company": huge, "location": huge, "description": huge},
        profile={f: huge for f in profile_store.FIELDS},
        experience=huge, company_research=huge,
    )
    assert len(prompt) <= drafting._MAX_PROMPT_CHARS
    # ~4 chars/token for English; 4x headroom even at a pessimistic 2.
    assert len(prompt) / 2 + drafting._MAX_TOKENS < config.OLLAMA_NUM_CTX


def test_draft_answers_the_whole_question_list_and_never_leaks_an_unmarked_draft(monkeypatch):
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT))
    questions = discover_questions(FIXTURE.read_text(encoding="utf-8"))
    answers = draft(questions, job=JOB, profile=FAKE, experience=EXPERIENCE,
                    company_research=RESEARCH)
    assert len(answers) == len(questions)
    assert [a.question for a in answers] == questions
    for a in answers:
        assert a.source in SOURCES
        assert a.source in ("drafted", "blank"), "drafting never claims 'profile'"
        if a.source == "drafted":
            assert is_marked(a.value)
        else:
            assert a.value == "" and a.note
    assert any(a.source == "drafted" for a in answers), "premise: some are draftable"


def test_draft_tolerates_an_empty_question_list():
    assert draft([], job=JOB, profile=FAKE) == []


def test_every_topic_is_reachable_and_declared():
    """`TOPICS` is documentation the rest of Phase B will read; keep it honest."""
    seen = {topic_of(q(label, kind=kind)) for label, kind in [
        ("Why do you want to work at Palantir?", "textarea"),
        ("Tell us about a project you shipped", "textarea"),
        ("Share a link to a video", "textarea"),
        ("Are you legally authorized to work in the United States?", "textarea"),
        ("First Name", "text"),
        ("Anything else?", "textarea"),
    ]}
    assert seen == set(TOPICS)
    assert DRAFTABLE_TOPICS < set(TOPICS)


# --------------------------------------------------------------- the module


def _module_imports(module):
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    return names, tree


def test_drafting_touches_no_browser_no_dom_and_no_database():
    """Drafting talks to exactly one outside thing: the model router. Everything else —
    the questions, the profile, the JD, the research — is passed in by the caller,
    which is what keeps this module testable without a browser or a database."""
    imported, tree = _module_imports(drafting)
    forbidden = {
        "httpx", "requests", "urllib", "socket", "http",       # network
        "playwright", "selenium",                               # browser
        "sqlite3", "store_db", "server", "profile_store",       # database
        "os", "pathlib", "shutil", "subprocess", "tempfile",   # filesystem
        "litellm", "openai", "anthropic", "ollama",             # models, directly
    }
    leaked = imported & forbidden
    assert not leaked, f"drafting must stay thin; it imports {sorted(leaked)}"
    for module in imported:
        assert "locate_dom" not in module, "drafting must not import DOM code"
        assert "browser" not in module, "drafting must not import browser code"


def test_drafting_writes_no_dom_code_at_all():
    """THE ONE RULE for Phase B: no code path may ever click a submit button.
    Checked on docstring-stripped source, because the prose above legitimately
    discusses forms and submitting."""
    _, tree = _module_imports(drafting)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(tree))
    for call in (".click(", ".fill(", ".check(", ".press(", "set_input_files(",
                 "query_selector", "goto(", "open(", "connect("):
        assert call not in code, f"drafting must write no DOM/IO code; found {call}"


def test_the_resolver_does_not_import_drafting():
    """The resolver is PURE and must stay so; the dependency runs one way only."""
    import agents.job_applier.resolver as resolver
    imported, _ = _module_imports(resolver)
    assert not any("drafting" in name for name in imported)
