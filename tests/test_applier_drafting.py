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

HERMETIC BY CONSTRUCTION, and NOT from this file. The `no_real_model` guard
lives in `tests/conftest.py` so it covers the whole suite; it used to live here as
a file-local autouse fixture, and that was a bug — see the fixture's docstring for
the measured consequence. It is deliberately NOT redefined here, because a
same-named fixture in this file would SHADOW the suite-wide one and quietly switch
off the `litellm.completion` backstop for exactly the file that needs it most. A
test that wants a model stubs one explicitly (`stub`/`calls` below).

The free-text corpus is the REAL captured Lever form
(`tests/fixtures/ats/lever-form.html`, provenance in `test_applier_locate.py`),
not invented labels — the questions this module has to get right are the awkward
ones a human wrote, and those are hard to imagine convincingly.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
from dataclasses import replace

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
    mark,
    topic_of,
)
from agents.job_applier.locate_dom import discover_questions
from agents.job_applier.nodes import draft as draft_node_mod
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


@pytest.mark.parametrize("reply", ["", "   ", None])
def test_an_empty_reply_says_nothing_came_back_not_that_something_short_did(
    monkeypatch, reply
):
    """The `if not text:` branch is redundant with `_MIN_DRAFT_CHARS` for the
    SAFETY property — deleting it still yields a blank — which is why a mutation
    removing it survived 89 tests. What it is not redundant for is the note: the
    too-short branch quotes what came back, and with nothing to quote it reads
    "the AI drafter returned only “”", which looks like a bug in the tool rather
    than a fact about the model. So the distinction is pinned here, deliberately
    as a message property and not as a safety one."""
    monkeypatch.setattr(drafting, "llm", stub(reply))
    note = draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE).note
    assert "nothing at all" in note
    assert "returned only" not in note


def test_a_short_reply_quotes_what_came_back():
    """The other side of the same distinction, so the pair cannot collapse."""
    note = draft_one(
        q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE,
        llm_fn=lambda *a, **k: "N/A",
    ).note
    assert "returned only" in note and "N/A" in note
    assert "nothing at all" not in note


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


VIDEO_PREFIXES = ("Prompt 1: Show us something you", "Prompt 2: Tell us about a piece")


def video_prompts():
    return [
        next(x for x in lever_free_text() if x.label.startswith(prefix))
        for prefix in VIDEO_PREFIXES
    ]


def test_the_video_prompts_are_refused_and_the_reason_cites_the_section(calls):
    """The authoritative evidence is `Question.section`: "Video Prompts: After
    recording your clips, please submit a URL to an unlisted YouTube video…". The
    note quotes the heading AND the token it matched, so the user can check the
    claim rather than take it on faith."""
    for question in video_prompts():
        assert question.section.startswith("Video Prompts:"), "premise: the heading is there"
        answer = draft_one(question, job=JOB, profile=FAKE, experience=EXPERIENCE)
        assert (answer.value, answer.source) == ("", "blank")
        assert "section heading" in answer.note
        assert "unlisted YouTube video" in answer.note
        assert "paragraph" in answer.note
    assert calls == [], "a box that wants a YouTube URL must not reach the model"


def test_the_video_prompts_are_still_refused_with_the_duration_stripped(calls):
    """The half of the evidence chain the section closes. "(90 seconds max)" is the
    label's entire contribution; delete it and the labels read as ordinary
    experience prompts ("Show us something you've built", "Tell us about a piece
    of Palantir coverage") — which is exactly what they drafted as before
    `Question.section` existed."""
    for question in video_prompts():
        bare = replace(question, label=question.label.replace(" (90 seconds max)", ""))
        assert "second" not in bare.label, "premise: no duration left in the label"
        assert drafting.not_prose_reason(bare.label, "") == "", (
            "premise: the label alone no longer gives it away"
        )
        assert topic_of(bare) == "not_prose"
        answer = draft_one(bare, job=JOB, profile=FAKE, experience=EXPERIENCE)
        assert (answer.value, answer.source) == ("", "blank")
    assert calls == []


def test_the_video_prompts_are_still_refused_with_the_section_stripped(calls):
    """The other half. Greenhouse and Ashby publish no section headings at all, so
    on those boards the label heuristic is the only line of defence and has to
    keep working on its own."""
    for question in video_prompts():
        bare = replace(question, section="")
        assert topic_of(bare) == "not_prose"
        assert "90 seconds" in drafting.not_prose_reason(bare.label, bare.section)
        assert draft_one(bare, job=JOB, profile=FAKE).source == "blank"
    assert calls == []


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
    # ~4 chars/token for English. At a pessimistic 2 chars/token the worst case is
    # 24000/2 + 700 = 12,700 tokens against 32,768 — 2.58x headroom, not the "4x"
    # an earlier version of this comment claimed. The arithmetic below is what is
    # actually asserted; the number in the prose now matches it.
    assert len(prompt) / 2 + drafting._MAX_TOKENS < config.OLLAMA_NUM_CTX
    assert config.OLLAMA_NUM_CTX / (len(prompt) / 2 + drafting._MAX_TOKENS) > 2.5


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


def test_the_number_of_free_text_declines_draft_py_cites_is_the_measured_one(monkeypatch):
    """`nodes/draft.py`'s merge policy is justified by a count, so the count is
    measured here rather than remembered there.

    It said drafting "declines four" of the captured Lever form's free-text
    questions, and the same paragraph closed with "on four fields of one real
    form". It is FIVE of seven: both video prompts (where the box wants a YouTube
    URL and drafting's own note says so), plus three the drafter cannot ground in
    a profile — "Give us three numbers that describe you", "something you know an
    unreasonable amount about", and "Additional information".

    The count is the whole argument for the merge rule: if drafting's refusal did
    not win over the resolver's "left for the AI drafting step" note, the handoff
    would promise the user a draft that is never coming on each of those fields.
    Undercounting it understates how often the rule fires.
    """
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT))
    questions = discover_questions(FIXTURE.read_text(encoding="utf-8"))
    answers = draft(questions, job=JOB, profile=FAKE, experience=EXPERIENCE)
    free_text = [a for a in answers if a.kind == "free_text"]
    declined = [a for a in free_text if a.source == "blank"]
    assert len(free_text) == 7
    assert len(declined) == 5
    # And WHICH five, because "five declines" would also be satisfied by declining
    # five it should have written. Both video prompts must be among them.
    video = [a for a in declined if "video or audio recording" in a.note]
    assert len(video) == 2
    assert all(a.value == "" and a.note for a in declined)
    source = pathlib.Path(draft_node_mod.__file__).read_text()
    assert "declines **five**" in source
    assert "on\nfive fields of one real form" in source


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


# --------------------------------------------- the "record" vocabulary, checked

# Phrasings that ARE asking for a recording. "Record your answer." is the one the
# label rules missed before Task 5's fix round: appended to any question it turns
# a prose prompt into a video prompt, and nothing in the old vocabulary saw it.
RECORD_POSITIVES = [
    "Record your answer.",
    "Record a video of yourself explaining it.",
    "Please record yourself answering this question.",
    "Submit a recording of your response.",
    "Send us a prerecorded clip.",
    "Upload a self-tape.",
    "Re-record if you are not happy with the first take.",
]

# Phrasings that contain "record" and are NOT. Task 4's lesson applied: a token is
# not safe in isolation, so the negatives get pinned as hard as the positives. A
# note telling the user "What is your academic record?" wants a video would be
# confidently, checkably false — and `resolver.py` records what one wrong note
# costs the user's trust in every other note the handoff shows.
RECORD_NEGATIVES = [
    "What is your academic record?",
    "Do you have a track record of shipping?",
    "Do you have a criminal record? Please explain.",
    "Describe your record of employment.",
    "For the record, are you over 18?",
    "Tell us about a time you broke a record.",
    "Our record of achievement matters; why do you want to work here?",
]


@pytest.mark.parametrize("label", RECORD_POSITIVES)
def test_a_request_to_record_something_is_refused(label, calls):
    assert "a video or audio recording" in drafting.not_prose_reason(label)
    assert topic_of(q(label)) == "not_prose"
    assert draft_one(q(label), job=JOB, profile=FAKE).source == "blank"
    assert calls == []


@pytest.mark.parametrize("label", RECORD_NEGATIVES)
def test_the_word_record_alone_does_not_make_a_question_a_video_prompt(label):
    assert drafting.not_prose_reason(label) == ""
    assert topic_of(q(label)) != "not_prose"


def test_a_prose_question_with_a_record_instruction_appended_is_refused(calls):
    """The exact regression: "Why do you want to work at Palantir?" is the one
    question on the real form that drafts, so appending "Record your answer." to it
    is the shape where a missed rule costs a paragraph in a video box."""
    label = "Why do you want to work at Palantir? Record your answer."
    assert topic_of(q(label)) == "not_prose", "and NOT motivation"
    assert draft_one(q(label), job=JOB, profile=FAKE, company_research=RESEARCH).source == "blank"
    assert calls == []


# ------------------------------------------------- section-heading evidence


def test_a_section_heading_asking_for_a_url_refuses_a_prose_looking_question(calls):
    """The whole point of `Question.section`: the label reads as a perfectly
    ordinary motivation prompt, and only the heading says what the box wants."""
    question = replace(
        q("Why do you want to work at Palantir?"),
        section="Video Prompts: please submit a URL to an unlisted YouTube video",
    )
    assert topic_of(q("Why do you want to work at Palantir?")) == "motivation", "premise"
    assert topic_of(question) == "not_prose"
    answer = draft_one(question, job=JOB, profile=FAKE, company_research=RESEARCH)
    assert (answer.value, answer.source) == ("", "blank")
    assert "section heading" in answer.note
    assert calls == []


@pytest.mark.parametrize("section", [
    "Video Prompts",
    "Please submit a link to your work",
    "Attach the following documents",
    "Upload your materials",
])
def test_the_four_section_safe_categories_refuse_everything_under_them(section):
    """URL, video, recording, file — the four the section rules cover."""
    assert topic_of(replace(q("Why do you want to work here?"), section=section)) == "not_prose"


@pytest.mark.parametrize("section", [
    "Additional Questions (you have 30 minutes to complete this application)",
    "Answer how many of the following apply to you",
    "An Inflection Point",
    "Supplementary Questions",
    "Work Authorization",
])
def test_a_heading_does_not_refuse_a_section_on_a_duration_or_a_count(section):
    """Deliberate scope limit, not an oversight. One heading match refuses EVERY
    question in the section, and a duration or a "how many" is the kind of thing a
    heading says in passing — at that blast radius an incidental match silently
    blanks a whole page. Both rules stay ON at label scope, where the radius is
    one box. `_SECTION_NOT_PROSE_RULES` is the subset; this test is what fails if
    someone "simplifies" it to the full list."""
    assert topic_of(replace(q("Why do you want to work here?"), section=section)) == "motivation"


def test_the_section_rule_subset_is_exactly_the_four_documented_categories():
    """Derived from `_NOT_PROSE_RULES`, never hand-copied — but the DERIVATION is
    what this pins, so the two lists cannot silently diverge in either direction."""
    section_safe = {wants for wants, _ in drafting._SECTION_NOT_PROSE_RULES}
    all_rules = {wants for wants, _, _ in drafting._NOT_PROSE_RULES}
    assert section_safe == {
        "a video or audio recording",
        "a URL or a link",
        "a file or an attachment",
    }
    assert section_safe < all_rules
    assert all_rules - section_safe == {
        "a recording or another timed answer, not a paragraph",
        "a number, a date or a single figure",
    }


def test_the_label_heuristic_still_runs_when_there_is_no_section():
    """Greenhouse and Ashby publish no section headings at all, so a regression
    that made the section pass mandatory would silently disable the whole rule on
    two of three boards."""
    for board in ("greenhouse-form.html", "ashby-form.html"):
        questions = discover_questions(
            (pathlib.Path(__file__).parent / "fixtures" / "ats" / board).read_text()
        )
        assert all(x.section == "" for x in questions), "premise: no headings there"
    assert topic_of(q("Share a link to a video")) == "not_prose"


def test_the_lever_sections_that_must_not_refuse_their_questions(calls):
    """Read from the real form: the two draftable questions sit under "Additional
    Questions", and the three-numbers pair under "An Inflection Point". Neither
    heading may trip the section rules, or the section signal would have cost more
    than it bought."""
    by_label = {x.label: x for x in lever_free_text()}
    draftable = by_label["Why do you want to work at Palantir?"]
    assert draftable.section == "Additional Questions"
    assert topic_of(draftable) == "motivation"
    three = next(x for x in by_label.values() if x.label.startswith("Give us three numbers"))
    assert three.section == "An Inflection Point"
    assert topic_of(three) == "unknown", "unknown for its own reasons, not the section's"


# ------------------------------------------------------------ fix-round-2 pins


def test_is_marked_says_no_to_text_a_human_wrote():
    """`is_marked` is the helper Tasks 6 and 7 are told to use INSTEAD of the
    literal, so an over-permissive version mislabels every hand-typed answer as
    AI-drafted — and a mutation replacing its body with `return True` passed all 89
    tests, because every existing caller only ever asked about a marked value."""
    assert is_marked(mark("anything")) is True
    for value in ["", "   ", "I wrote this myself.",
                  "AI", "draft", "[AI-DRAFTED]", DRAFT_MARKER[:-1], None]:
        assert is_marked(value) is False, repr(value)


def test_is_marked_finds_the_marker_anywhere_not_only_at_the_start():
    """`mark` puts it first and a test pins that; `is_marked` is deliberately
    laxer, because a human who edits the draft may leave the marker mid-text and it
    must still be detected."""
    assert is_marked(f"Some preamble\n{DRAFT_MARKER}\nbody") is True


def test_the_experience_corpus_reaches_an_experience_prompt_and_nothing_else():
    """The `if pool and topic == "experience"` gate governs how much of the user's
    own data reaches the model, and the docstring says the corpus is an
    `experience`-topic input. Dropping the topic condition sends the whole master
    résumé into every motivation prompt too; the mutation survived because no test
    looked."""
    marker = "UNIQUE_EXPERIENCE_TOKEN"
    pool = f"{marker} — built a SQLite-backed job tracker."
    experience_prompt = build_prompt(
        q(PROJECT_Q), "experience", job=JOB, profile=FAKE, experience=pool)
    motivation_prompt = build_prompt(
        q("Why do you want to work at Palantir?"), "motivation",
        job=JOB, profile=FAKE, experience=pool)
    assert marker in experience_prompt
    assert marker not in motivation_prompt


def test_llm_fn_is_used_instead_of_the_module_level_llm(monkeypatch):
    """`llm_fn` is the injected-model seam Task 8 will use. It had zero callers and
    zero coverage: every test patched the module global, so a regression that
    ignored the parameter would have gone unnoticed. Proven by making the module
    global explode — if `llm_fn` were ignored, this raises instead of drafting."""
    def boom(*args, **kwargs):
        raise AssertionError("llm_fn was ignored; the module-level llm was called")
    monkeypatch.setattr(drafting, "llm", boom)
    record = []
    answer = draft_one(
        q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE,
        llm_fn=stub(GOOD_DRAFT, record=record),
    )
    assert answer.source == "drafted" and is_marked(answer.value)
    assert len(record) == 1 and record[0]["role"] == "local"


def test_llm_fn_receives_the_same_call_shape_as_the_module_level_llm(monkeypatch):
    """Two seams, one contract — otherwise swapping between them changes behaviour."""
    via_global, via_param = [], []
    monkeypatch.setattr(drafting, "llm", stub(GOOD_DRAFT, record=via_global))
    draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE)
    draft_one(q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE,
              llm_fn=stub(GOOD_DRAFT, record=via_param))
    assert via_global == via_param


def test_a_failing_llm_fn_is_caught_like_any_other_model_failure():
    answer = draft_one(
        q("Why do you want to work at Palantir?"), job=JOB, profile=FAKE,
        llm_fn=stub(raises=RuntimeError("injected model is down")),
    )
    assert (answer.value, answer.source) == ("", "blank")
    assert "injected model is down" in answer.note


# ------------------------------------------------- the scrubber, corpus-checked

# Figures that really do appear in a résumé, and MUST come through byte-identical.
# Every entry marked (#) contains the profile phone's digit run 5550100 in some
# rendering — that is the shape the previous test corpus missed entirely, and the
# reason three real metrics were being destroyed:
#     "$5,550,100 in ARR"        -> "$[redacted] in ARR"
#     "Processed 5 550 100 rows" -> "Processed [redacted] rows"
#     "Team of 5550100"          -> "Team of [redacted]"
# `_digit_run_re` joined the phone's digits with `\D{0,2}`, i.e. any two non-digits
# between every digit, while its own comment claimed to be "conservative".
RESUME_NUMBERS_THAT_MUST_SURVIVE = [
    "$5,550,100 in ARR",                                  # (#) thousands separators
    "Processed 5 550 100 rows",                            # (#) space-grouped count
    "Team of 5550100",                                     # (#) bare digit run
    "Scaled to 5,550,100 events/day",                      # (#)
    "Cut latency 555 ms to 100 ms",                        # (#) split by words
    "Employee #5550100",                                   # (#)
    "Grew revenue from $1.2M to $5.5M (2024-2026)",
    "Reduced p99 from 1,250 ms to 310 ms",
    "Shipped v2.10.3 on 2026-07-31",
    "Coverage 98.6%; 1,204 tests",
    "GPA 3.95/4.00",
    "Range: 100-150 hours",
    "ISO 27001 audit",
    "2019-2023 B.Sc. Computer Engineering",
]

# Renderings of the profile's OWN number (+1-555-0100) that must be redacted.
OWN_NUMBER_RENDERINGS = [
    "+1-555-0100", "+1 555 0100", "+1.555.0100", "+15550100",
    "555-0100", "555.0100", "(555) 0100", "555 0100",
    "Tel: +1 (555) 0100",
]


@pytest.mark.parametrize("text", RESUME_NUMBERS_THAT_MUST_SURVIVE)
def test_a_real_resume_figure_survives_the_scrubber_byte_identical(text):
    assert drafting._scrub_contact(text, FAKE) == text


@pytest.mark.parametrize("text", OWN_NUMBER_RENDERINGS)
def test_every_rendering_of_the_users_own_number_is_redacted(text):
    assert "[redacted]" in drafting._scrub_contact(text, FAKE)
    assert "555" not in drafting._scrub_contact(text, FAKE)


def test_at_least_one_surviving_figure_shares_the_phones_digit_run():
    """The property the old one-string test lacked. Without this, the corpus above
    could drift into shapes that never collide with the phone and the whole
    over-redaction defect would be invisible again."""
    digits = re.sub(r"\D", "", FAKE["phone"])[-7:]
    colliding = [
        t for t in RESUME_NUMBERS_THAT_MUST_SURVIVE
        if digits in re.sub(r"\D", "", t)
    ]
    assert len(colliding) >= 3, colliding


def test_a_unicode_dash_no_longer_hides_a_phone_number():
    """`_normalize` folds these dashes for label matching; `_scrub_contact` did not,
    and `[\\s.\\-]` is ASCII-only — so a résumé header written with U+2011 or an en
    dash walked straight through the NANP rule."""
    for dash in ("‑", "–", "—", "−"):
        text = f"416{dash}555{dash}9999"
        assert "[redacted]" in drafting._scrub_contact(text, FAKE), repr(dash)


def test_the_scrubber_does_not_collapse_the_newlines_of_the_grounding_text():
    """It runs over a whole prompt. Folding punctuation must not fuse the résumé's
    lines together, which is why `_fold_punctuation` is not `_normalize`."""
    text = "Line one\nLine two\n\nLine three"
    assert drafting._scrub_contact(text, FAKE) == text


@pytest.mark.parametrize("phone", ["", "   ", "12-34", "555", None])
def test_a_profile_with_no_usable_phone_produces_no_own_number_pattern(phone):
    """Too few digits to be a phone number, so matching it would fire on prose."""
    assert drafting._own_number_res(phone or "") == []
    assert drafting._scrub_contact("Team of 5550100", {"phone": phone}) == "Team of 5550100"


def test_a_separatorless_profile_phone_is_matched_exactly():
    """With no separators there are no group boundaries to reason about, so the
    value is matched literally rather than expanded into guessed groupings."""
    profile = {"phone": "4165559999"}
    assert "[redacted]" in drafting._scrub_contact("call 4165559999 now", profile)
    assert drafting._scrub_contact("call 416-555-9998 now", profile) == "call [redacted] now"
    assert drafting._scrub_contact("Team of 41655599990", profile) == "Team of 41655599990"
