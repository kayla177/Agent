"""Task 8: the graph — the first place Tasks 1-7 run as one thing.

Everything here is HERMETIC: no Chromium, no network, no model, no writes to the
real database. The browser module is monkeypatched to hand back a fake context
wrapping a captured HTML fixture; the drafting model is monkeypatched to a
function that returns a fixed paragraph; the stores run against the `temp_db`
fixture. The suite-wide `no_real_model` guard in `tests/conftest.py` is left
alone — these tests replace `drafting.llm` on top of it, which is the documented
way to stub it.

The page stub below is deliberately a LOCAL copy of the one in
`tests/test_applier_handoff.py` rather than an import: sharing a test double by
cross-importing test modules couples two large files together and breaks the
moment either is reorganised. It is 70 lines and only has to support the calls
`fetch_form` and `fill` actually make.

What this file is trying to prove, in priority order:

  * The chain runs end to end on all three captured boards and produces a
    handoff — and no path through it, including every failure path, ever claims
    a submission.
  * A run that cannot open a browser fails LOUDLY: `error`, a sentence saying
    why, and a report that says nothing was submitted. Not a silent empty one.
  * The browser is closed on every failing path, including a node raising
    mid-graph, and is deliberately left open on the successful one (see
    `graph.py`'s "Browser lifetime" section).
  * `agents/registry.py`'s `node_order` matches the graph — for EVERY agent, not
    just this one, because that drift has already happened twice.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import profile_store
from agents.job_applier import browser, drafting, resolver
from agents.job_applier import graph as graph_mod
from agents.job_applier.graph import (
    NODE_ORDER,
    build_job_applier_graph,
    release_browser,
)
from agents.job_applier.nodes import fetch_form as fetch_form_mod
from agents.job_applier.nodes import load_profile as load_profile_mod
from agents.job_applier.nodes.draft import merge_answers
from agents.job_applier.nodes.fill import ATTACHED, FILLED
from agents.job_applier.nodes.fill import fill_node as _real_fill_node
from agents.job_applier.nodes.handoff import BLOCKING, NOT_SUBMITTED_HEADLINE
from agents.job_scraper import store as jobstore
from agents.registry import REGISTRY, get_spec

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
GRAPH_SOURCE = pathlib.Path(graph_mod.__file__).read_text()


# ===========================================================================
# A writable page stub + a fake browser context
# ===========================================================================


class _El:
    def __init__(self, *, value="", visible=True, count=1):
        self.value = value
        self.visible = visible
        self.count = count
        self.checked = False
        self.filename = ""


class _Loc:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.el = page.element(selector)

    def count(self):
        return self.el.count

    def is_visible(self, timeout=None):
        return self.el.visible

    def input_value(self, timeout=None):
        return self.el.value

    def fill(self, value, timeout=None):
        self.page.writes.append(("fill", self.selector, value))
        self.el.value = value

    def press_sequentially(self, value, delay=0, timeout=None):
        self.page.writes.append(("type", self.selector, value))
        self.el.value = value

    def select_option(self, label=None, timeout=None):
        self.page.writes.append(("select", self.selector, label))
        self.el.value = label

    def check(self, timeout=None):
        self.page.writes.append(("check", self.selector, True))
        self.el.checked = True

    def is_checked(self, timeout=None):
        return self.el.checked

    def set_input_files(self, path, timeout=None):
        self.page.writes.append(("attach", self.selector, path))
        self.el.filename = pathlib.Path(path).name

    def evaluate(self, js, timeout=None):
        if "files" in js:
            return self.el.filename
        if "selectedOptions" in js:
            return self.el.value
        return ""


class _Page:
    def __init__(self, html):
        self._html = html
        self._elements: dict[str, _El] = {}
        self.writes: list[tuple] = []
        self.visited: list[str] = []
        self.waited: list[str] = []

    def element(self, selector):
        return self._elements.setdefault(selector, _El())

    def content(self):
        return self._html

    def locator(self, selector):
        return _Loc(self, selector)

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)

    def wait_for_selector(self, selector, timeout=None):
        self.waited.append(selector)


class _FakeContext:
    """Stands in for `browser.ManagedBrowserContext`.

    `closes` COUNTS rather than flags, so a test can tell "closed" from "closed
    twice" — the real wrapper is idempotent, and a graph that relies on that
    without saying so is a graph that breaks when the wrapper changes.
    """

    def __init__(self, page):
        self._page = page
        self.pages = [page]
        self.closes = 0

    def new_page(self):
        return self._page

    def close(self):
        self.closes += 1


# ===========================================================================
# Fake data. `.invalid` domains and 555-01xx numbers only — never the user's.
# ===========================================================================

PROFILE = {
    "full_name": "Testy McTestface",
    "email": "testy@example.invalid",
    "phone": "555-0142",
    "linkedin_url": "https://www.linkedin.com/in/testy-mctestface",
    "github_url": "https://github.com/testy-mctestface",
    "portfolio_url": "https://testy.invalid",
    "location": "Milwaukee, WI",
    "school": "University of Wisconsin",
    "degree": "BS Computer Science",
    "grad_date": "May 2027",
    "us_work_auth": "citizen",
}

_DRAFT_BODY = (
    "I spent two years building data tooling for a campus research lab, and "
    "what keeps pulling me back is how much leverage a well-shaped pipeline "
    "gives a small team. That is the work I want more of."
)

_BOARD_URL = {
    "lever": "https://jobs.lever.co/palantir/395a4483",
    "ashby": "https://jobs.ashbyhq.com/snowflake/41e65c6c",
    "greenhouse": "https://job-boards.greenhouse.io/cloudflare/jobs/8077075",
}


def _html(board: str) -> str:
    return (FIXTURES / f"{board}-form.html").read_text()


def _posting(board: str, **overrides) -> dict:
    rec = {
        "id": f"Palantir:{board}:1",
        "company": "Palantir",
        "title": "Software Engineer Intern",
        "location": "Palo Alto, CA",
        "url": _BOARD_URL[board],
        "ats": board,
        "country": "US",
        "description": "Build data tooling that people rely on.",
    }
    rec.update(overrides)
    return rec


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "testy-mctestface-resume.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the drafting module's `llm`. Layered ON TOP of conftest's
    `no_real_model` (which already repointed it at a raiser) — a later
    `setattr` on a monkeypatch instance wins, and the guard stays in place for
    every other module."""
    monkeypatch.setattr(drafting, "llm", lambda *a, **k: _DRAFT_BODY)


@pytest.fixture
def board(temp_db, monkeypatch, fake_model):
    """Seed a posting + the fake profile, and stub the browser. Returns a
    factory: `board("lever")` -> (final_state, page, context)."""

    def run(name, *, resume_path="", posting=None, page=None, **inputs):
        jobstore.upsert_records([posting or _posting(name)])
        profile_store.upsert_profile(**PROFILE)
        the_page = page if page is not None else _Page(_html(name))
        context = _FakeContext(the_page)
        monkeypatch.setattr(browser, "is_available", lambda: True)
        monkeypatch.setattr(browser, "launch_context", lambda: context)
        state = build_job_applier_graph().invoke(
            {"job_id": (posting or _posting(name))["id"],
             "resume_path": resume_path, **inputs}
        )
        return state, the_page, context

    return run


# ===========================================================================
# The whole chain, on all three captured boards
# ===========================================================================


@pytest.mark.parametrize("name", ["lever", "greenhouse", "ashby"])
def test_the_whole_graph_runs_against_a_captured_board(board, resume, name):
    """No Chromium, no network, no model — the fixture IS the page."""
    state, page, context = board(name, resume_path=resume)

    assert not state.get("error"), state.get("message")
    assert state["page"] is page
    assert page.visited, "the graph never navigated anywhere"
    assert state["questions"], f"{name} produced no questions"
    # One answer per question, all the way through.
    assert len(state["resolved"]) == len(state["questions"])
    assert len(state["drafted"]) == len(state["questions"])
    assert len(state["answers"]) == len(state["questions"])
    assert state["fill_report"].outcomes
    assert state["report"].total > 0
    assert state["message"].startswith(NOT_SUBMITTED_HEADLINE)
    assert state["report"].submitted is False


def test_the_graph_actually_fills_the_form(board, resume):
    """Not just "it ran": the profile's own values reach the page, the résumé is
    attached, and the run's output is the report the user reads."""
    state, page, _ = board("lever", resume_path=resume)

    written = " ".join(str(w[2]) for w in page.writes)
    assert PROFILE["full_name"] in written
    assert PROFILE["email"] in written
    assert any(w[0] == "attach" for w in page.writes), "the résumé was never attached"
    assert state["fill_report"].resume.status == ATTACHED
    assert any(o.status == FILLED for o in state["fill_report"].outcomes)
    # The résumé attach is the LAST page mutation (an ATS parse-and-prefill on
    # upload must not clobber what was typed).
    assert page.writes[-1][0] == "attach"


def test_the_drafted_answers_are_marked_and_the_declined_ones_are_not_promised(
    board, resume
):
    """The merge policy, seen from the graph. Lever has two draftable free-text
    questions and two video prompts drafting declines; the drafts are marked
    AI-drafted, and the declined ones must not carry the resolver's "left for
    the AI drafting step" promise."""
    state, _, _ = board("lever", resume_path=resume)

    drafted = [a for a in state["answers"] if a.source == "drafted"]
    assert drafted, "nothing was drafted on a board with two open questions"
    assert all(drafting.is_marked(a.value) for a in drafted)

    free_text_blanks = [
        a for a in state["answers"] if a.kind == "free_text" and a.source == "blank"
    ]
    assert free_text_blanks, "Lever's video prompts should be declined"
    for answer in free_text_blanks:
        assert "left for the AI drafting step" not in answer.note
    assert "left for the AI drafting step" not in state["message"]


def test_a_profile_answer_is_never_overwritten_by_a_draft():
    """`merge_answers` claims "a question the resolver ANSWERED from the profile
    is never overwritten by a draft", and NOTHING proved it: on all three
    captured boards there are ZERO questions where `resolved.source != "blank"`
    and `drafted.kind == "free_text"`, so the `r.source == "blank"` guard was a
    fixture accident. Dropping it left the whole suite green.

    The form that exercises it: a textarea the resolver matches to a profile
    field ("Where are you based?" → `location`) that drafting also classifies as
    free text. Without the guard the real value is replaced by drafting's refusal
    and the field goes out EMPTY.
    """
    question = _q("Where are you based?", kind="textarea", key="based")
    from_profile = resolver.Answer(
        question=question, value="Milwaukee, WI", source="profile", kind="location",
    )
    declined = resolver.Answer(
        question=question, value="", source="blank",
        note="this box reads as a request for a video recording.", kind="free_text",
    )

    kept = merge_answers([from_profile], [declined])
    assert kept == [from_profile]
    assert kept[0].value == "Milwaukee, WI"


def test_a_blank_the_resolver_stepped_aside_from_is_overwritten_by_the_draft():
    """The other direction, so the guard above cannot be satisfied by a merge
    that simply always keeps the resolver's answer — which is the whole point of
    the policy and is what Task 7's report test depends on."""
    question = _q("Why do you want this job?", kind="textarea", key="why")
    stepped_aside = resolver.Answer(
        question=question, value="", source="blank",
        note="free-text answer left for the AI drafting step, which marks its "
             "output as AI-drafted for you to review.",
        kind="free_text",
    )
    written = resolver.Answer(
        question=question, value=f"{drafting.DRAFT_MARKER}\n\nBecause the work is real.",
        source="drafted", kind="free_text",
    )
    declined = resolver.Answer(
        question=question, value="", source="blank",
        note="this box reads as a request for a video or audio recording.",
        kind="free_text",
    )

    assert merge_answers([stepped_aside], [written]) == [written]
    # A refusal wins too — that is ruling 1, and the report depends on it.
    assert merge_answers([stepped_aside], [declined]) == [declined]


def test_the_merge_is_positional_over_equal_length_lists():
    """Both inputs are one-per-question in the questions' own order. A merge that
    silently truncated to the shorter list would drop the tail of the form."""
    questions = [_q(f"Q{i}", key=f"q{i}") for i in range(3)]
    resolved = [
        resolver.Answer(question=q, value=f"v{i}", source="profile", kind="other")
        for i, q in enumerate(questions)
    ]
    drafted = [
        resolver.Answer(question=q, value="", source="blank", kind="other")
        for q in questions
    ]
    merged = merge_answers(resolved, drafted)
    assert [a.question.key for a in merged] == ["q0", "q1", "q2"]
    assert [a.value for a in merged] == ["v0", "v1", "v2"]


def test_the_graph_uses_the_one_merge_policy_and_does_not_reinvent_it(board, resume):
    """`merge_answers` lives in the draft node and is imported by the Task 7
    tests. This asserts the GRAPH's output is that function's output, so a
    second, subtly different merge cannot appear inside the node."""
    state, _, _ = board("lever", resume_path=resume)
    assert state["answers"] == merge_answers(state["resolved"], state["drafted"])


def test_no_model_is_reached_for_a_question_the_resolver_owns(board, resume, monkeypatch):
    """The model must never see a work-authorization question. Recorded at the
    seam rather than asserted about the output, because a blank answer looks the
    same whether the model refused or was never asked."""
    seen: list[str] = []

    def spy(_provider, prompt, **kwargs):
        seen.append(prompt)
        return _DRAFT_BODY

    monkeypatch.setattr(drafting, "llm", spy)
    state, _, _ = board("lever", resume_path=resume)

    assert seen, "the model was never called at all — this test proves nothing"
    blocking_labels = {
        a.question.label for a in state["resolved"]
        if a.kind in resolver.BLOCKING_KINDS and a.question.label
    }
    assert blocking_labels
    for prompt in seen:
        for label in blocking_labels:
            assert label not in prompt, f"a blocking question reached the model: {label}"


# ===========================================================================
# THE ONE RULE: nothing anywhere in the graph claims a submission
# ===========================================================================
# The DETECTOR, not one example sentence — the same instrument
# `tests/test_applier_handoff.py` proves the report with, applied here to the
# messages the GRAPH's own nodes write, which that file never sees.

_SUBMISSION_CLAIM_RE = re.compile(
    r"\b(?:submitted|resubmitted)\b"
    r"|\bapplication\s+(?:was|is|has\s+been|been)\s+(?:sent|submitted|filed|complete|completed)\b"
    r"|\bapplication\s+(?:sent|submitted|filed)\b"
    r"|\b(?:we|i|it|the\s+agent|the\s+application)\s+(?:have\s+|has\s+|was\s+)?"
    r"(?:submitted|applied|sent)\b"
    r"|\bsuccessfully\s+(?:submitted|applied|sent|completed)\b"
    r"|\bapplied\s+(?:for|to)\s+(?:this|the)\b"
    r"|\bsubmission\s+(?:complete|completed|successful|confirmed)\b"
    r"|\b(?:apply|submit)\s+(?:complete|completed|succeeded|done)\b"
    r"|\bsent\s+(?:your|the|this)\s+application\b"
    r"|\bpressed\s+submit\b",
    re.IGNORECASE,
)
_NEGATOR_RE = re.compile(
    r"\b(?:no|not|nothing|none|never|neither|nor|without|cannot|can't|won't"
    r"|don't|doesn't|didn't|isn't|wasn't|hasn't|haven't|unsubmitted)\b",
    re.IGNORECASE,
)


def _claims_submission(text: str) -> str:
    flat = " ".join((text or "").split())
    for sentence in re.split(r"(?<=[.!?])\s+", flat):
        match = _SUBMISSION_CLAIM_RE.search(sentence)
        if match and not _NEGATOR_RE.search(sentence):
            return match.group(0)
    return ""


def test_the_claim_detector_is_not_vacuous():
    """A detector that never fires makes every test using it green forever."""
    for claim in (
        "Your application was submitted.",
        "We submitted the application for you.",
        "Successfully applied to this role.",
        "The agent pressed Submit for you.",
    ):
        assert _claims_submission(claim), claim
    assert not _claims_submission(NOT_SUBMITTED_HEADLINE)


def test_no_message_the_graph_can_produce_claims_a_submission(board, resume, tmp_path):
    """Every message on every route: the three boards, and every failure the
    graph knows how to produce."""
    messages: list[tuple[str, str]] = []
    for name in ("lever", "greenhouse", "ashby"):
        messages.append((f"{name}/full", board(name, resume_path=resume)[0]["message"]))
        messages.append((f"{name}/no-resume", board(name)[0]["message"]))

    for label, message in messages:
        claim = _claims_submission(message)
        assert not claim, f"{label} claims: {claim!r}"


def test_no_failure_message_claims_a_submission(failure_messages):
    for label, message in failure_messages:
        claim = _claims_submission(message)
        assert not claim, f"{label} claims: {claim!r}"


@pytest.fixture
def failure_messages(temp_db, monkeypatch, fake_model):
    """Every distinct way this graph can stop, and what it says. Built as a
    fixture so the claim test and the "always says nothing was submitted" test
    run over the SAME list and cannot drift apart."""
    out: list[tuple[str, str]] = []
    graph = build_job_applier_graph()

    out.append(("no_job_id", graph.invoke({})["message"]))
    out.append(("unknown_job", graph.invoke({"job_id": "nope"})["message"]))

    jobstore.upsert_records([_posting("lever", id="x:workday:1", ats="workday")])
    out.append(("unsupported_ats", graph.invoke({"job_id": "x:workday:1"})["message"]))

    jobstore.upsert_records([_posting("lever", id="x:lever:nourl", url="")])
    out.append(("no_url", graph.invoke({"job_id": "x:lever:nourl"})["message"]))

    jobstore.upsert_records([_posting("lever")])
    job_id = _posting("lever")["id"]

    monkeypatch.setattr(browser, "is_available", lambda: False)
    out.append(("no_browser", graph.invoke({"job_id": job_id})["message"]))

    monkeypatch.setattr(browser, "is_available", lambda: True)

    def boom():
        raise RuntimeError("chromium is not installed")

    monkeypatch.setattr(browser, "launch_context", boom)
    out.append(("launch_failed", graph.invoke({"job_id": job_id})["message"]))

    class _DeadPage(_Page):
        def goto(self, url, wait_until=None, timeout=None):
            raise TimeoutError("navigation timed out")

    monkeypatch.setattr(
        browser, "launch_context", lambda: _FakeContext(_DeadPage("<html></html>"))
    )
    out.append(("nav_failed", graph.invoke({"job_id": job_id})["message"]))

    def exploding_fill(state):
        raise ValueError("the page went away")

    monkeypatch.setattr(
        browser, "launch_context", lambda: _FakeContext(_Page(_html("lever")))
    )
    monkeypatch.setattr(graph_mod, "fill_node", exploding_fill, raising=True)
    out.append(("node_raised", build_job_applier_graph().invoke({"job_id": job_id})["message"]))

    # The route two mutants slipped through: a node that RETURNS `error` without
    # raising, while the browser is open. The exception path clears the handle on
    # its way out, so it is the only route where a stale live-looking context
    # reaches the handoff — i.e. the only one that can produce a false "the window
    # is open" claim. It was missing from this catalogue.
    monkeypatch.setattr(graph_mod, "fill_node", _real_fill_node, raising=True)
    monkeypatch.setattr(
        graph_mod, "resolve_node",
        lambda state: {"error": "resolve_failed", "message": "the resolver gave up."},
    )
    out.append((
        "node_returned_error_with_browser_open",
        build_job_applier_graph().invoke({"job_id": job_id})["message"],
    ))
    return out


def test_every_failure_still_tells_the_user_nothing_was_submitted(failure_messages):
    """The failure paths are where a bare traceback or an empty string is most
    tempting. Every one of them renders the handoff, and the "nothing was
    submitted" guarantee is UNCONDITIONAL — unlike the window clause below, it is
    true on every path and must stay first."""
    assert len(failure_messages) >= 7, "the failure catalogue stopped covering the paths"
    for label, message in failure_messages:
        assert message.startswith(NOT_SUBMITTED_HEADLINE), label
        assert "THE AGENT STOPPED EARLY" in message, label


def test_a_failed_run_never_claims_a_browser_window_is_open(failure_messages):
    """The other half, and the half that was WRONG. Task 7's headline says "the
    browser window is still open on this form, waiting for you" and its footer
    says to work through the list "in the open browser window". Task 8 ran that
    renderer on paths where the graph had just closed the window (a node raised)
    or where none had ever been opened (Playwright missing) — and the test above
    asserted the false sentence across all eight routes, so a guard was
    protecting the bug.

    Failure scenario it was hiding: `fill` raises on a real Lever form and Kayla
    reads that the window is open and waiting, while she hunts for a window the
    graph destroyed.
    """
    for label, message in failure_messages:
        assert "browser window is still open" not in message, label
        assert "in the open browser window, fix anything" not in message, label
        # And it says what IS true instead, rather than going quiet about it.
        assert "No browser window is open" in message, label


def test_a_successful_run_does_say_the_window_is_open(board, resume):
    """The true case, asserted separately so "never claims it" cannot be
    satisfied by never saying it at all."""
    state, _, _ = board("lever", resume_path=resume)
    assert state["report"].browser_open is True
    assert "browser window is still open" in state["message"]
    assert "No browser window is open" not in state["message"]


def test_the_report_exposes_browser_open_as_a_field_not_only_as_prose(
    temp_db, monkeypatch, fake_model, resume
):
    """Task 10 renders this in a web UI and must not have to grep the prose. The
    flag has to agree with the text on both sides."""
    jobstore.upsert_records([_posting("lever")])
    profile_store.upsert_profile(**PROFILE)
    context = _FakeContext(_Page(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    def _flat(text):
        return " ".join(text.split())  # the renderer wraps at 78 columns

    good = build_job_applier_graph().invoke(
        {"job_id": _posting("lever")["id"], "resume_path": resume}
    )
    assert good["report"].browser_open is True
    assert good["report"].headline() in _flat(good["message"])

    monkeypatch.setattr(graph_mod, "fill_node", lambda state: 1 / 0)
    bad = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})
    assert bad["report"].browser_open is False
    assert bad["report"].headline() in _flat(bad["message"])
    # The state agrees with the report: a closed handle is cleared, so a UI
    # cannot mistake it for a live window either.
    assert bad.get("browser") is None


# ===========================================================================
# Failing loudly when the browser is unavailable
# ===========================================================================


def test_a_run_that_cannot_open_a_browser_still_produces_a_handoff(
    temp_db, monkeypatch, fake_model
):
    """Ruling 4. `is_available()` is False -> `error`, a message naming both
    install commands, and a report — not a silent empty one."""
    jobstore.upsert_records([_posting("lever")])
    monkeypatch.setattr(browser, "is_available", lambda: False)
    # `launch_context` performs its own availability check, so a graph that
    # skipped `is_available()` would still end up with an error and would still
    # look fine here. Failing the launch outright is what makes this test about
    # the CHEAP pre-check the ruling asked for, rather than about the fallback.
    monkeypatch.setattr(
        browser, "launch_context", lambda: pytest.fail("a launch was attempted")
    )

    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})

    assert state["error"] == "no_browser"
    assert "the browser is not available on this machine" in state["report"].error
    assert state["report"].error, "the report does not say the run stopped early"
    assert state["report"].submitted is False
    assert ".venv/bin/pip install playwright" in state["report"].error
    assert "playwright install chromium" in state["report"].error
    assert "uv" not in state["report"].error, "uv is not installed on this machine"
    assert state["message"].startswith(NOT_SUBMITTED_HEADLINE)
    # And it never got as far as opening anything.
    assert state.get("browser") is None


def test_an_unavailable_browser_never_reaches_the_later_nodes(
    temp_db, monkeypatch, fake_model
):
    """The short-circuit, asserted at the seam: `resolve`, `draft` and `fill`
    write nothing at all, rather than writing empty lists that look like a form
    with no questions on it."""
    jobstore.upsert_records([_posting("lever")])
    monkeypatch.setattr(browser, "is_available", lambda: False)
    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})
    for key in ("questions", "resolved", "drafted", "answers", "fill_report"):
        assert key not in state, key


# ===========================================================================
# Browser lifetime
# ===========================================================================


def test_a_successful_run_leaves_the_window_open_for_the_human(board, resume):
    """Deliberate, and the opposite of the failure paths below. The product is
    "the agent fills the form and stops so you press Submit yourself" — closing
    the window on success would throw away every field it just typed."""
    state, _, context = board("lever", resume_path=resume)
    assert not state.get("error")
    assert context.closes == 0
    # …and the caller is given the means to end that session.
    assert release_browser(state) is True
    assert context.closes == 1


def test_release_browser_is_idempotent_and_total():
    """It runs in `finally`-shaped positions, so it must never be the thing that
    raises, and never care how many times it is called."""
    assert release_browser({}) is False
    assert release_browser(None) is False

    class _Exploding:
        def close(self):
            raise RuntimeError("driver already gone")

    assert release_browser({"browser": _Exploding()}) is False

    context = _FakeContext(_Page(""))
    assert release_browser({"browser": context}) is True
    assert release_browser({"browser": context}) is True
    assert context.closes == 2


def test_a_node_raising_mid_graph_closes_the_browser(
    temp_db, monkeypatch, fake_model, resume
):
    """Ruling 7. A headed Chromium plus an invisible driver subprocess, leaked
    once per crash, in a server process that runs for weeks."""
    jobstore.upsert_records([_posting("lever")])
    profile_store.upsert_profile(**PROFILE)
    context = _FakeContext(_Page(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    def boom(state):
        raise ValueError("the page went away")

    monkeypatch.setattr(graph_mod, "fill_node", boom)
    state = build_job_applier_graph().invoke(
        {"job_id": _posting("lever")["id"], "resume_path": resume}
    )

    assert context.closes >= 1, "a raising node leaked the browser"
    assert state["error"] == "fill_failed"
    assert "the page went away" in state["report"].error
    assert state["message"].startswith(NOT_SUBMITTED_HEADLINE)


def test_a_raise_in_the_final_node_still_closes_the_browser(
    temp_db, monkeypatch, fake_model, resume
):
    """The one case the handoff's own teardown cannot cover — it is the thing
    that raised. Without the release inside the guard's `except`, this run ends
    with a visible window and a driver subprocess owned by nobody."""
    jobstore.upsert_records([_posting("lever")])
    profile_store.upsert_profile(**PROFILE)
    context = _FakeContext(_Page(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    def boom(state):
        raise RuntimeError("the renderer blew up")

    monkeypatch.setattr(graph_mod, "handoff_node", boom)
    state = build_job_applier_graph().invoke(
        {"job_id": _posting("lever")["id"], "resume_path": resume}
    )

    assert state["error"] == "handoff_failed"
    assert context.closes >= 1, "the last node raised and leaked the browser"


def test_the_browser_is_closed_when_a_node_after_it_sets_an_error(
    temp_db, monkeypatch, fake_model
):
    """The other failing shape: no exception, just a node that returns `error`.
    The short-circuit skips the rest, and the handoff still has to release the
    window it never got to hand over."""
    jobstore.upsert_records([_posting("lever")])
    profile_store.upsert_profile(**PROFILE)
    context = _FakeContext(_Page(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)
    monkeypatch.setattr(
        graph_mod, "resolve_node",
        lambda state: {"error": "resolve_failed", "message": "the resolver gave up."},
    )

    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})

    assert state["error"] == "resolve_failed"
    assert context.closes >= 1
    assert "the resolver gave up." in state["report"].error
    # This is the ONLY route where a live-looking handle reaches the handoff (the
    # exception path clears it on the way out), so it is the only one that can
    # produce a false "the window is open" claim — and two mutants slipped
    # through here because nothing asserted it.
    assert state["report"].browser_open is False
    assert "browser window is still open" not in state["message"]
    assert state.get("browser") is None, "a closed handle was left in the state"


def test_a_page_that_cannot_be_read_closes_the_browser_it_opened(
    temp_db, monkeypatch, fake_model
):
    """The one close the graph guard CANNOT do: the context exists but is not in
    the state yet, so `fetch_form` has to clean up after itself."""
    jobstore.upsert_records([_posting("lever")])

    class _DeadPage(_Page):
        def wait_for_selector(self, selector, timeout=None):
            raise TimeoutError("no form ever rendered")

    context = _FakeContext(_DeadPage("<html></html>"))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})

    assert state["error"] == "form_unreachable"
    assert context.closes == 1
    assert state.get("browser") is None, "a closed context must not be handed on"


def test_a_ctrl_c_while_the_form_loads_closes_the_browser_fetch_form_opened(
    temp_db, monkeypatch, fake_model
):
    """`fetch_form`'s `except BaseException` branch, which was untested — the
    `except Exception` test above covers only the other half.

    Reachable for real: `KeyboardInterrupt` or `SystemExit` during `goto` /
    `wait_for_selector`, i.e. Ctrl-C while a slow board loads. The graph's guard
    cannot help, because the context is not in the state yet, so deleting this
    branch leaks a headed Chromium plus a driver subprocess on every Ctrl-C.
    """
    jobstore.upsert_records([_posting("lever")])

    class _Interrupted(_Page):
        def goto(self, url, wait_until=None, timeout=None):
            raise KeyboardInterrupt("ctrl-c while the board loaded")

    context = _FakeContext(_Interrupted(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    with pytest.raises(KeyboardInterrupt):
        build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})
    assert context.closes == 1, "Ctrl-C during navigation leaked the browser"


def test_a_teardown_failure_does_not_replace_the_actionable_message(
    temp_db, monkeypatch, fake_model
):
    """`ManagedBrowserContext.close()` PROPAGATES a context-close error, so a bare
    `.close()` at a cleanup site turns "the application form at <url> could not
    be read" into a generic teardown traceback — and the graph's guard cannot
    retry it, because the context is not in the state yet."""
    jobstore.upsert_records([_posting("lever")])

    class _Unreadable(_Page):
        def wait_for_selector(self, selector, timeout=None):
            raise TimeoutError("no form ever rendered")

    class _StickyContext(_FakeContext):
        def close(self):
            self.closes += 1
            raise RuntimeError("the driver had already gone away")

    context = _StickyContext(_Unreadable("<html></html>"))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})

    assert state["error"] == "form_unreachable", state.get("message")
    assert "could not be read" in state["report"].error
    assert context.closes == 1


def test_a_base_exception_closes_the_browser_and_is_not_swallowed(
    temp_db, monkeypatch, fake_model
):
    """`ModelCalledInTest` and `KeyboardInterrupt` mean "stop", not "degrade".
    Converting them into a tidy report is how a test that reached the real model
    passes anyway — the exact bug conftest's guard was written for."""
    jobstore.upsert_records([_posting("lever")])
    context = _FakeContext(_Page(_html("lever")))
    monkeypatch.setattr(browser, "is_available", lambda: True)
    monkeypatch.setattr(browser, "launch_context", lambda: context)

    class _Stop(BaseException):
        pass

    def boom(state):
        raise _Stop("stop")

    monkeypatch.setattr(graph_mod, "resolve_node", boom)
    with pytest.raises(_Stop):
        build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})
    assert context.closes >= 1


# ===========================================================================
# Ruling 2: the posting's country decides WHICH work-auth field applies
# ===========================================================================


def _q(label, kind="text", **kw):
    from agents.job_applier.schema_greenhouse import Question

    return Question(key=kw.pop("key", "q"), label=label, required=kw.pop("required", False),
                    kind=kind, options=list(kw.pop("options", []) or []),
                    section=kw.pop("section", ""))


_UNNAMED_COUNTRY = "Are you legally authorized to work in the country for which you are applying?"


def test_a_question_naming_no_country_is_answered_from_the_postings_country():
    """Carried debt from Task 4: the resolver blanked this real Lever question
    for not naming a country, while the posting row knew it all along."""
    question = _q(_UNNAMED_COUNTRY)
    without = resolver.resolve([question], {"us_work_auth": "citizen"})[0]
    assert without.value == "" and "does not name a single country" in without.note

    with_country = resolver.resolve(
        [question], {"us_work_auth": "citizen"}, default_country="us"
    )[0]
    assert with_country.value == "Yes"
    assert with_country.source == "profile"
    # It says WHICH way it guessed, because the guess picked the profile field.
    assert "posting's own country (US)" in with_country.note


def test_the_postings_country_picks_the_matching_profile_field_not_a_default():
    """A Canadian posting reads `ca_work_auth`. Answering it from `us_work_auth`
    is exactly the cross-wiring the resolver exists to prevent."""
    question = _q(_UNNAMED_COUNTRY)
    profile = {"us_work_auth": "citizen", "ca_work_auth": ""}
    answer = resolver.resolve([question], profile, default_country="ca")[0]
    assert answer.value == ""
    assert "ca_work_auth" in answer.note


def test_the_default_country_never_overrides_a_country_the_label_names():
    question = _q("Are you legally authorized to work in Canada?")
    profile = {"us_work_auth": "citizen", "ca_work_auth": "needs_sponsorship"}
    answer = resolver.resolve([question], profile, default_country="us")[0]
    assert answer.value == "No", "the US posting country overrode a Canadian question"
    assert "posting's own country" not in answer.note


def test_a_label_naming_two_countries_is_still_blank_whatever_the_posting_says():
    """"North America", or the US and Canada in one sentence: one profile field
    cannot answer it, and the posting's country would silently pick a side."""
    for label in (
        "Are you authorized to work anywhere in North America?",
        "Are you authorized to work in the United States and Canada?",
    ):
        for country in ("us", "ca"):
            answer = resolver.resolve(
                [_q(label)], {"us_work_auth": "citizen", "ca_work_auth": "citizen"},
                default_country=country,
            )[0]
            assert answer.value == "", f"{label} / {country}"
            assert "does not name a single country" in answer.note


@pytest.mark.parametrize("country", ["", "UNKNOWN", "OTHER", "unknown", "GB", None])
def test_a_posting_country_that_names_no_profile_field_changes_nothing(country):
    """`jobs.country` holds "UNKNOWN" and "OTHER" too. Neither names a field, so
    the honest outcome is the pre-Task-8 one: blank, with the reason."""
    answer = resolver.resolve(
        [_q(_UNNAMED_COUNTRY)], {"us_work_auth": "citizen"},
        default_country=country or "",
    )[0]
    assert answer.value == ""
    assert "does not name a single country" in answer.note


def test_the_posting_country_does_not_weaken_the_work_authorization_refusal():
    """The whole point of the carve-out. Even with the country supplied, the
    eligibility kinds stay blocking and an UNSET status is still never guessed."""
    question = _q(_UNNAMED_COUNTRY, required=True)
    answer = resolver.resolve([question], {}, default_country="us")[0]
    assert answer.kind in resolver.BLOCKING_KINDS
    assert answer.value == ""
    assert "never guessed" in answer.note
    # And even a RESOLVED one is still surfaced for the human to confirm.
    resolved = resolver.resolve(
        [question], {"us_work_auth": "citizen"}, default_country="us"
    )
    assert resolver.blocking(resolved) == resolved


def test_the_report_tells_the_user_when_the_posting_picked_the_country(board):
    """The disclosure has to survive all the way to the text she reads.

    It nearly did not: the executor writes its own note for a blocking answer it
    refuses to type ("your profile implies “Yes”…"), which REPLACED the
    resolver's — so the two questions where the agent had guessed which
    country's field to read were the only two that did not say so.
    """
    state, _, _ = board("lever", posting=_posting("lever", country="US"))
    text = state["message"]
    assert text.count("posting's own country (US)") == 2, text
    for item in state["report"].items:
        if "profile implies" in item.note and item.kind in resolver.BLOCKING_KINDS:
            assert "posting's own country" in item.note, item.label


def test_the_jobs_country_column_maps_to_a_profile_field():
    for column, expected in (("US", "us"), ("CA", "ca"), ("OTHER", ""), ("UNKNOWN", ""),
                             ("", ""), ("us", "us")):
        posting = _posting("lever", id=f"c:{column or 'blank'}", country=column)
        assert load_profile_mod.default_country_of(posting) == expected
    assert load_profile_mod.default_country_of({}) == ""


def test_the_graph_feeds_the_postings_country_into_the_resolver(board):
    """End to end on the REAL captured Lever form, which is where this debt came
    from: two of its questions ("...authorized to work in the country for which
    you are applying?" and the sponsorship one) name no country at all.

    Asserted on the answers, not on `state["default_country"]`: a graph that
    carried the country in its state and then forgot to pass it to `resolve`
    would satisfy the weaker check and change nothing on the form.
    """
    state, _, _ = board("lever", posting=_posting("lever", country="US"))
    from_posting = [a for a in state["resolved"] if "posting's own country" in a.note]
    assert len(from_posting) == 2, [a.question.label for a in from_posting]
    assert {a.value for a in from_posting} == {"Yes", "No"}
    assert all("(US)" in a.note for a in from_posting)

    # And with a posting whose country names no profile field, the same two
    # questions go back to being blank with the reason — nothing is invented.
    other = _posting("lever", id="Palantir:lever:other", country="OTHER")
    state, _, _ = board("lever", posting=other)
    assert state["default_country"] == ""
    unnamed = [
        a for a in state["resolved"] if "does not name a single country" in a.note
    ]
    assert len(unnamed) == 2 and all(a.value == "" for a in unnamed)


# ===========================================================================
# The apply URL
# ===========================================================================


def test_the_apply_url_is_derived_per_board():
    """MEASURED from `scripts/capture_ats_fixtures.py`'s own targets — the URLs
    the committed fixtures were captured from."""
    lever = {"ats": "lever", "url": "https://jobs.lever.co/palantir/395a4483"}
    assert fetch_form_mod.apply_url(lever).endswith("/apply")
    # Idempotent: a URL that already points at the form is left alone.
    already = {"ats": "lever", "url": "https://jobs.lever.co/palantir/395a4483/apply"}
    assert fetch_form_mod.apply_url(already) == already["url"]

    ashby = {"ats": "ashby", "url": "https://jobs.ashbyhq.com/snowflake/41e6"}
    assert fetch_form_mod.apply_url(ashby).endswith("/application")

    gh = {"ats": "greenhouse", "url": "https://job-boards.greenhouse.io/cf/jobs/1"}
    assert fetch_form_mod.apply_url(gh) == gh["url"]


def test_a_company_redirector_url_is_never_rewritten():
    """A real row in this database points at
    `databricks.com/company/careers/open-positions/job?gh_jid=...`. Bolting
    "/apply" onto that produces a 404 instead of a form, so the suffix is applied
    only on the board's own host."""
    row = {
        "ats": "greenhouse",
        "url": "https://databricks.com/company/careers/open-positions/job?gh_jid=7586263002",
    }
    assert fetch_form_mod.apply_url(row) == row["url"]
    lever_mirror = {"ats": "lever", "url": "https://careers.acme.invalid/jobs/42"}
    assert fetch_form_mod.apply_url(lever_mirror) == lever_mirror["url"]


def test_an_explicit_form_url_wins_and_is_what_the_graph_opens(board, resume):
    state, page, _ = board(
        "lever", resume_path=resume, form_url="https://example.invalid/custom/apply"
    )
    assert page.visited == ["https://example.invalid/custom/apply"]
    assert state["form_url"] == "https://example.invalid/custom/apply"


# ===========================================================================
# Ruling 3: the registry's node_order matches the graph — for EVERY agent
# ===========================================================================


def _real_nodes(compiled) -> list[str]:
    """The graph's own nodes, in declaration order, without START/END."""
    return [n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")]


def _backward_edges(compiled, order: tuple[str, ...]) -> list[tuple[str, str]]:
    """Edges that run against `order`. Empty means `order` is a valid execution
    order for this graph — which is what `node_order` claims to be, and is a
    stronger claim than "the same set of names"."""
    index = {name: i for i, name in enumerate(order)}
    bad = []
    for edge in compiled.get_graph().edges:
        if edge.source in index and edge.target in index:
            if index[edge.source] >= index[edge.target]:
                bad.append((edge.source, edge.target))
    return bad


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_the_registry_node_order_matches_every_graph(key):
    """Phase A found TWO registries that had drifted from their graphs, so this
    is written once, over the whole registry, rather than for `job_applier`
    alone. Three claims, because set equality alone would let the order rot:

      1. `node_order` is exactly the graph's nodes when `send=True` (the maximal
         graph — `deliver` is conditional on that flag in three agents).
      2. It is a valid EXECUTION order: no edge runs backwards through it.
      3. The `send=False` graph is a subset of it, so the flag can only remove
         nodes, never introduce one the UI has never heard of.
    """
    spec = get_spec(key)
    assert spec.node_order, f"{key} declares no node_order"

    full = spec.build_graph(send=True)
    assert list(spec.node_order) == _real_nodes(full), key
    assert _backward_edges(full, spec.node_order) == [], key

    quiet = spec.build_graph(send=False)
    assert set(_real_nodes(quiet)) <= set(spec.node_order), key


def test_the_node_order_check_is_not_vacuous():
    """Proved in both directions on a synthetic graph, because a check over
    seven graphs that all happen to pass is indistinguishable from one whose
    accessor silently returns nothing."""
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(dict)
    g.add_node("a", lambda s: {})
    g.add_node("b", lambda s: {})
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    compiled = g.compile()

    assert _real_nodes(compiled) == ["a", "b"]
    assert _backward_edges(compiled, ("a", "b")) == []
    # A registry that listed them the wrong way round, or dropped one, fails.
    assert _backward_edges(compiled, ("b", "a")) == [("a", "b")]
    assert _real_nodes(compiled) != ["a", "b", "deliver"]


def test_the_job_applier_is_registered_and_reachable():
    spec = get_spec("job_applier")
    assert spec.output_key == "message"
    assert tuple(spec.node_order) == NODE_ORDER
    assert spec.to_meta()["node_order"] == list(NODE_ORDER)
    # It never claims to submit, in the copy the UI shows.
    assert not _claims_submission(f"{spec.display_name}. {spec.description}")
    assert "never submits" in spec.description


def test_the_registry_still_imports_lazily():
    """`agents/registry.py` imports builders inside their factories so a run
    doesn't pull litellm/httpx into every process. Adding an entry must not
    change that."""
    import agents.registry as registry_mod

    tree = ast.parse(pathlib.Path(registry_mod.__file__).read_text())
    top_level = {
        node.module for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agents.")
    } | {
        alias.name for node in tree.body
        if isinstance(node, ast.Import) for alias in node.names
        if alias.name.startswith("agents.")
    }
    assert top_level == set(), f"registry.py imports agents modules at import time: {top_level}"
    # Non-vacuous: the builders ARE there, just nested inside their factories.
    nested = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agents.")
    }
    assert "agents.job_applier.graph" in nested


# ===========================================================================
# Ruling 6: the module's prose is pinned to tests, and the code stays honest
# ===========================================================================


def test_every_test_the_graph_docstring_cites_actually_exists():
    """Nine findings across Tasks 4-7 were prose asserting a property the code
    did not have. `graph.py` names the test that pins each claim, which is worth
    nothing if the names rot."""
    cited = set(re.findall(r"\b(test_[a-z0-9_]+)", GRAPH_SOURCE))
    assert len(cited) >= 5, "the docstring stopped citing its tests"
    defined = set(re.findall(r"^def (test_[a-z0-9_]+)", pathlib.Path(__file__).read_text(), re.M))
    assert cited <= defined, f"cited but missing: {sorted(cited - defined)}"


def test_the_graph_touches_the_page_only_to_close_the_browser():
    """**The ONE-RULE scan for `graph.py` and `state.py` is NOT here.** It is the
    four-rule AST guard in `tests/test_applier_locate.py`, which now globs the
    whole package recursively; `test_every_applier_module_is_scanned` there names
    both files so the coverage cannot quietly lapse.

    This test used to BE that scan, re-implemented with a flat banned-attribute
    list — and it was strictly weaker than the guard it stood in for:
    `add_init_script`, `add_script_tag`, `evaluate_handle`, `eval_on_selector`,
    `query_selector`, `locator` and `get_by_role` were all missing from the list,
    so `page.add_init_script("document.forms[0].submit()")` in `graph.py` left the
    whole suite green.

    What is left here is the complement the shared guard cannot express: this
    module's ONLY interaction with a browser object is closing it. `state.py`
    interacts with nothing at all.
    """
    graph_path = pathlib.Path(graph_mod.__file__)
    state_path = graph_path.parent / "state.py"

    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(graph_path.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    # Everything else here is dict/graph/string plumbing, named explicitly so a
    # new browser-shaped call has to be added to this list to pass.
    allowed = {
        "close", "close_quietly", "get", "add_node", "add_edge", "compile",
        "join", "strip", "items", "format", "upper", "lower",
    }
    assert calls - allowed == set(), f"graph.py calls {sorted(calls - allowed)}"
    assert calls & {"close", "close_quietly"}, (
        "non-vacuous: the one browser call it IS allowed to make"
    )

    assert not [
        node for node in ast.walk(ast.parse(state_path.read_text()))
        if isinstance(node, ast.Call)
    ], "state.py should be a TypedDict and nothing else"


def test_the_graph_declares_the_nodes_it_documents():
    """The chain in the docstring, the `NODE_ORDER` constant, the `add_node`
    calls and the registry are four copies of one list. This pins the first
    three to each other; the registry is pinned above."""
    assert "load_profile -> fetch_form -> resolve -> draft -> fill -> handoff" in GRAPH_SOURCE
    declared = re.findall(r'g\.add_node\(\s*"([a-z_]+)"', GRAPH_SOURCE)
    assert tuple(declared) == NODE_ORDER
    edges = re.findall(r'g\.add_edge\(\s*"([a-z_]+)",\s*"([a-z_]+)"\)', GRAPH_SOURCE)
    assert edges == list(zip(NODE_ORDER, NODE_ORDER[1:]))


def test_the_graph_pulls_in_no_browser_or_network_library_at_import_time():
    """Importing the graph must not require Playwright. `browser.py` imports it
    lazily inside the functions that need it, and the graph must not undo that
    by importing Playwright itself."""
    for path in (
        pathlib.Path(graph_mod.__file__),
        pathlib.Path(fetch_form_mod.__file__),
        pathlib.Path(load_profile_mod.__file__),
    ):
        source = path.read_text()
        assert not re.search(r"^\s*(?:import|from)\s+playwright", source, re.M), path.name
        assert not re.search(r"^\s*(?:import|from)\s+httpx", source, re.M), path.name


# ===========================================================================
# Short-circuiting, the resume_generator way
# ===========================================================================


def test_an_unknown_job_stops_before_anything_opens(temp_db, monkeypatch, fake_model):
    opened = []
    monkeypatch.setattr(browser, "is_available", lambda: opened.append("checked") or True)
    monkeypatch.setattr(
        browser, "launch_context", lambda: pytest.fail("a browser was opened")
    )
    state = build_job_applier_graph().invoke({"job_id": "does-not-exist"})
    assert state["error"] == "no_job"
    assert opened == [], "availability was probed for a job that does not exist"


def test_a_board_the_locator_was_never_built_for_is_refused(
    temp_db, monkeypatch, fake_model
):
    """Three captured forms is what `locate_dom` was measured against. A fourth
    board is a guess about a DOM nobody has looked at, typed into a real
    employer's form."""
    jobstore.upsert_records([_posting("lever", id="x:workday:1", ats="workday")])
    monkeypatch.setattr(
        browser, "launch_context", lambda: pytest.fail("a browser was opened")
    )
    state = build_job_applier_graph().invoke({"job_id": "x:workday:1"})
    assert state["error"] == "unsupported_ats"
    assert "workday" in state["message"]
    for supported in load_profile_mod.SUPPORTED_ATS:
        assert supported in state["message"]


def test_a_missing_resume_is_not_an_error(board):
    """An empty `resume_path` is normal input, not a failure: everything else is
    still filled and the handoff says once that no résumé was attached."""
    state, page, _ = board("lever")
    assert not state.get("error")
    assert not any(w[0] == "attach" for w in page.writes)
    assert state["report"].resume_note
    assert state["report"].total > 0
    assert "no résumé file" in state["fill_report"].resume.note


def test_a_whitespace_only_resume_path_is_treated_as_no_resume(board):
    """A path of spaces is not a file. Passed through unnormalised it becomes
    `os.path.isfile("   ")` and the user is told their résumé "“   ” was not
    found" — a confusing report about a file they never chose."""
    state, page, _ = board("lever", resume_path="   ")
    assert not any(w[0] == "attach" for w in page.writes)
    note = state["fill_report"].resume.note
    assert "no résumé file" in note, note
    assert "not found" not in note, note


def test_an_empty_profile_fills_nothing_and_blocks_on_the_required_fields(
    temp_db, monkeypatch, fake_model
):
    """The state the user's REAL profile is in today. The run must complete and
    report the missing required fields as blocking, not proceed as if it had
    them."""
    jobstore.upsert_records([_posting("lever")])
    monkeypatch.setattr(browser, "is_available", lambda: True)
    page = _Page(_html("lever"))
    monkeypatch.setattr(browser, "launch_context", lambda: _FakeContext(page))

    state = build_job_applier_graph().invoke({"job_id": _posting("lever")["id"]})

    assert not state.get("error")
    assert state["report"].group(BLOCKING), "nothing was reported as blocking"
    # Not one profile-derived value, because there are none to derive.
    assert not [a for a in state["answers"] if a.source == "profile"]
    # The only thing typed is an AI draft, which is grounded in the POSTING and
    # is marked as such — never a fact about a person the profile does not hold.
    for _kind, _selector, value in page.writes:
        assert drafting.is_marked(str(value)), value
