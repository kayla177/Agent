"""Task 7: the handoff report — the only thing the user actually reads.

Everything upstream in Phase B is judged through this module, so the tests here
are mostly PROPERTY tests over reports built end-to-end from the three captured
boards rather than assertions about one hand-made outcome. The two habits Tasks
4-6 had to learn the hard way are applied throughout:

  * **Assert the property, not one example string.** "The report never claims a
    submission" is checked with a claim DETECTOR run over every code path, and
    the detector is itself proved non-vacuous
    (`test_the_submission_claim_detector_is_not_vacuous`). A test that greps for
    one sentence passes forever once that sentence is deleted.
  * **Use the real fixtures.** `tests/fixtures/ats/{lever,greenhouse,ashby}-form.html`
    drive `discover_questions`, the resolver, the drafting node and the fill
    executor end-to-end, and a report is built from each. Lever is the
    interesting one: 29 questions across 14 sections, blocking work-auth and
    sponsorship, two video prompts drafting refuses, two draftable free-text
    questions.

HERMETIC: no browser, no network, no model. The page is the `_Page` stub below;
the model is `_fake_llm`, injected through `drafting.draft(llm_fn=...)`. The
suite-wide `no_real_model` guard in `tests/conftest.py` is left alone.

**Test file placement.** The task brief names `tests/test_applier_graph.py`.
That name predates the split: Task 8 owns the graph, `fill.py`/`handoff.py` live
under `nodes/`, and `tests/test_applier_locate.py` is already 3,879 lines
carrying Tasks 4 and 6. A dedicated file for the report keeps the ~40 property
tests below findable.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from agents.job_applier import drafting, resolver
from agents.job_applier.drafting import DRAFT_MARKER, is_marked
from agents.job_applier.locate_dom import PageLocator, discover_questions
from agents.job_applier.nodes import handoff as handoff_mod
from agents.job_applier.nodes.draft import merge_answers
from agents.job_applier.nodes.fill import (
    ATTACHED,
    BLANK,
    CHANGED,
    FILLED,
    FillOutcome,
    FillReport,
    fill_form,
)
from agents.job_applier.nodes.handoff import (
    BLOCKING,
    DONE,
    GROUPS,
    NOT_SUBMITTED_HEADLINE,
    REASON_CHANGED,
    REASON_DRAFTED,
    REASON_EMPTY,
    REASON_REFUSED,
    REASON_UNREADABLE,
    REASON_WITHHELD_EEO,
    REVIEW,
    WITHHELD,
    HandoffReport,
    ReportItem,
    build_report,
)
from agents.job_applier.resolver import Answer, blocking as resolver_blocking
from agents.job_applier.schema_greenhouse import Question

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
HANDOFF_SOURCE = pathlib.Path(handoff_mod.__file__).read_text()


# ===========================================================================
# A writable page stub — the same shape Task 6's tests use, kept local
# ===========================================================================
# Deliberately NOT imported from `test_applier_locate.py`: a test double shared
# across files by cross-importing test modules couples two 4,000-line files
# together and breaks the moment either is reorganised. This one is 70 lines and
# only has to support the calls `fill.py` actually makes.


class _El:
    """One fake element. `swallow` reproduces the React-controlled input that
    accepts a programmatic write and silently throws it away; `transform`
    reproduces an input mask that rewrites what it is given."""

    def __init__(self, *, value="", visible=True, count=1, swallow=False,
                 transform=None, raises=None):
        self.value = value
        self.visible = visible
        self.count = count
        self.swallow = swallow
        self.transform = transform
        self.raises = raises
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

    def _write(self, value):
        if self.el.raises:
            raise self.el.raises
        if self.el.swallow:
            return
        self.el.value = self.el.transform(value) if self.el.transform else value

    def fill(self, value, timeout=None):
        self.page.writes.append(("fill", self.selector, value))
        self._write(value)

    def press_sequentially(self, value, delay=0, timeout=None):
        self.page.writes.append(("type", self.selector, value))
        self._write(value)

    def select_option(self, label=None, timeout=None):
        self.page.writes.append(("select", self.selector, label))
        if self.el.raises:
            raise self.el.raises
        self.el.value = label

    def check(self, timeout=None):
        self.page.writes.append(("check", self.selector, True))
        if self.el.raises:
            raise self.el.raises
        self.el.checked = True

    def is_checked(self, timeout=None):
        return self.el.checked

    def set_input_files(self, path, timeout=None):
        self.page.writes.append(("attach", self.selector, path))
        if self.el.raises:
            raise self.el.raises
        self.el.filename = pathlib.Path(path).name

    def evaluate(self, js, timeout=None):
        if "files" in js:
            return self.el.filename
        if "selectedOptions" in js:
            return self.el.value
        return ""


class _Page:
    def __init__(self, html, **elements):
        self._html = html
        self._elements = dict(elements)
        self.writes = []

    def element(self, selector):
        return self._elements.setdefault(selector, _El())

    def content(self):
        return self._html

    def locator(self, selector):
        return _Loc(self, selector)


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


def _fake_llm(*args, **kwargs):
    return _DRAFT_BODY


def _html(board: str) -> str:
    return (FIXTURES / f"{board}-form.html").read_text()


# The merge policy that decides what the executor types. It used to be defined
# here, because Task 7 needed it before Task 8 existed; it now LIVES in the
# graph's draft node and is imported so there is exactly one of it. The property
# it protects is still pinned here, in
# `test_the_report_never_promises_a_draft_that_was_already_declined`: the
# resolver blanks every `free_text` question with "left for the AI drafting
# step", drafting then DECLINES four of Lever's, and keeping the resolver's note
# for those would promise the user a draft that is never coming.
_merge = merge_answers


def _build(board: str, *, profile=None, resume_path="", elements=None,
           job_title="Software Engineer Intern", company="Palantir",
           browser_open=False):
    """A report built end-to-end from a real captured board. No browser."""
    page = _Page(_html(board), **(elements or {}))
    locator = PageLocator(page)
    questions = locator.questions()
    resolved = resolver.resolve(questions, PROFILE if profile is None else profile)
    drafted = drafting.draft(
        questions,
        job={"title": job_title, "company": company,
             "description": "Build data tooling that people rely on."},
        profile=PROFILE if profile is None else profile,
        experience="Built a data pipeline for a campus research lab.",
        llm_fn=_fake_llm,
    )
    fill_report = fill_form(locator, _merge(resolved, drafted), resume_path=resume_path)
    report = build_report(
        fill_report,
        page_locator=locator,
        job_title=job_title,
        company=company,
        form_url=f"https://example.invalid/{board}/apply",
        browser_open=browser_open,
    )
    return report, fill_report, questions, page


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "testy-mctestface-resume.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


def _q(label, *, key="", kind="text", required=False, options=None, section=""):
    return Question(
        key=key or (label.lower().replace(" ", "_") or "q"),
        label=label, required=required, kind=kind,
        options=list(options or []), section=section,
    )


def _outcome(label, status, **kw):
    return FillOutcome(
        key=kw.pop("key", label.lower().replace(" ", "_")),
        label=label, status=status, **kw,
    )


def _report_of(outcomes, questions=(), **kw):
    return build_report(
        FillReport(outcomes=list(outcomes)), questions=list(questions), **kw
    )


# ===========================================================================
# THE ONE RULE: the report never claims a submission, on any path
# ===========================================================================
# The DETECTOR, not one example sentence. Task 5's lesson: a test that asserts
# `"submitted" not in text` passes forever the moment the offending sentence is
# reworded, and a test that asserts one exact string passes forever the moment
# anything else claims a submission instead.
#
# Deliberately narrow around the words the report legitimately uses. The report
# says "press Submit yourself", and Lever's own section heading is literally
# "Submit your application" while its video-prompt heading says "please submit a
# URL" — none of those is a claim that anything WAS submitted, and a detector
# that flagged them would be uselessly noisy and would get deleted.

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


# The report's own first line is "NOTHING WAS SUBMITTED", which is the exact
# opposite of a claim and matches `\bsubmitted\b`. So the detector is applied
# per SENTENCE and a sentence carrying a negator is not a claim. Sentence scope
# rather than an N-character lookbehind window: "The form will not submit until
# you deal with them." and a hypothetical "Your application was submitted."
# could sit 20 characters apart, and a window would let the first excuse the
# second.
_NEGATOR_RE = re.compile(
    r"\b(?:no|not|nothing|none|never|neither|nor|without|cannot|can't|won't"
    r"|don't|doesn't|didn't|isn't|wasn't|hasn't|haven't|unsubmitted)\b",
    re.IGNORECASE,
)


def _claims_submission(text: str) -> str:
    """The claim found in `text`, or "".

    Whitespace is flattened FIRST: the renderer word-wraps at 78 columns, so a
    claim can straddle a newline and a detector run over the raw text would miss
    exactly the phrasings it exists to catch.
    """
    flat = " ".join((text or "").split())
    for sentence in re.split(r"(?<=[.!?])\s+", flat):
        match = _SUBMISSION_CLAIM_RE.search(sentence)
        if match and not _NEGATOR_RE.search(sentence):
            return match.group(0)
    return ""


def _every_code_path(resume_path: str) -> list[tuple[str, HandoffReport]]:
    """One report per materially different route through `build_report`.

    Includes the two the brief calls out by name — the all-blank case (an empty
    profile, so nothing resolves) and the total-failure case (no fill report at
    all) — because those are the paths where a "nothing to report, all done!"
    sentence is most tempting to write.
    """
    reports: list[tuple[str, HandoffReport]] = []
    for board in ("lever", "greenhouse", "ashby"):
        reports.append((f"{board}/full", _build(board, resume_path=resume_path)[0]))
        reports.append((f"{board}/no-resume", _build(board)[0]))
        reports.append((f"{board}/all-blank", _build(board, profile={})[0]))
    reports.append(("empty", build_report(None)))
    reports.append(("empty+error", build_report(
        None, error="the browser closed before the form loaded.")))
    # Both sides of the window clause, so the claim detector below covers the
    # OPEN headline and instruction too — they are prose like everything else.
    reports.append(("lever/window-open", _build("lever", resume_path=resume_path,
                                                browser_open=True)[0]))
    reports.append(("empty/window-open", build_report(None, browser_open=True)))
    reports.append(("no-outcomes", build_report(FillReport())))
    reports.append(("everything-filled", _report_of([
        _outcome("Full name", FILLED, value="Testy McTestface", source="profile"),
        _outcome("Résumé", ATTACHED, value="cv.pdf", kind="file_upload", source="file"),
    ])))
    reports.append(("withheld-only", build_report(
        None, withheld_eeo=[_q("Gender", key="gender")])))
    reports.append(("unreadable-only", build_report(
        None, unreadable=[_q("", key="u1", required=True, kind="checkbox",
                             options=["Male", "Female", "Decline"])])))
    return reports


def test_no_rendered_report_on_any_path_claims_a_submission(resume):
    for name, report in _every_code_path(resume):
        for show_filled in (True, False):
            text = report.render_text(show_filled=show_filled)
            claim = _claims_submission(text)
            assert not claim, f"{name} (show_filled={show_filled}) claims: {claim!r}"


def test_the_submission_claim_detector_is_not_vacuous():
    """A detector that never fires is worse than no detector: it makes the test
    above green forever. Every one of these is a sentence a well-meaning
    refactor could plausibly add to a "success" path."""
    for claim in (
        "Your application was submitted.",
        "We submitted the application for you.",
        "Application sent!",
        "Successfully applied to this role.",
        "The agent applied for this job on your behalf.",
        "Submission complete.",
        "Apply complete.",
        "I have sent your application.",
        "The agent pressed Submit for you.",
        "The application has been filed.",
    ):
        assert _claims_submission(claim), claim


def test_the_claim_detector_survives_word_wrapping():
    """The renderer wraps at 78 columns. A detector run over raw text would miss
    a claim split across a line break — which is most of them."""
    wrapped = "Your application\nwas\nsubmitted to Palantir."
    assert _claims_submission(wrapped)


def test_a_negated_sentence_is_not_a_claim_but_a_neighbouring_one_still_is():
    """The report's own headline is "NOTHING WAS SUBMITTED", so the detector has
    to read negation — and it must not let one negated sentence excuse the next
    sentence, which is what a fixed-width lookbehind window would do."""
    assert not _claims_submission("Nothing was submitted.")
    assert _claims_submission(
        "Nothing was submitted. Actually, your application was submitted."
    )


def test_the_detector_does_not_fire_on_what_the_report_legitimately_says(resume):
    """The other half of non-vacuity: a detector so broad that the real report
    trips it would be neutered on its first false positive. These phrasings are
    load-bearing and must stay allowed."""
    for benign in (
        NOT_SUBMITTED_HEADLINE,
        handoff_mod.HANDOFF_INSTRUCTION,
        "press Submit yourself when you are ready",
        "Submit your application",                      # Lever's section heading
        "please submit a URL to an unlisted YouTube video",  # Lever's video prompts
        "delete the marker line before you submit",
        "Amsterdam University of Applied Sciences",
        "the form will not submit until you deal with them",
    ):
        assert not _claims_submission(benign), benign


def test_every_report_says_nothing_was_submitted_first_and_last(resume):
    """Not enough to omit a false claim — the true one has to be stated, first
    and last, on every path. THIS part is unconditional and always will be."""
    for name, report in _every_code_path(resume):
        text = report.render_text()
        flat = " ".join(text.split())
        # First and last, so it is true whether she reads the report or skims to
        # the end of it. Flattened for the tail because the footer is wrapped.
        assert text.startswith(NOT_SUBMITTED_HEADLINE), name
        assert flat.endswith(report.headline()), name
        assert report.headline().startswith(NOT_SUBMITTED_HEADLINE), name
        assert "does not press Submit" in flat, name


def test_the_window_clause_is_conditional_and_not_a_standing_claim(resume):
    """It used to be part of `NOT_SUBMITTED_HEADLINE`, i.e. asserted here on every
    path — including the ones where the caller had no browser at all. Task 8's
    graph then ran this renderer after tearing the window down, and the user was
    told to work "in the open browser window" while she hunted for one that had
    been closed. So the clause now follows `browser_open`, both ways."""
    opened, _, _, _ = _build("lever", resume_path=resume, browser_open=True)
    closed, _, _, _ = _build("lever", resume_path=resume, browser_open=False)

    open_text = " ".join(opened.render_text().split())
    closed_text = " ".join(closed.render_text().split())

    assert "browser window is still open" in open_text
    assert "in the open browser window" in open_text
    assert "No browser window is open" not in open_text

    assert "browser window is still open" not in closed_text
    assert "No browser window is open" in closed_text
    assert "Open the form yourself" in closed_text

    # The invariant survives both.
    for text in (open_text, closed_text):
        assert text.startswith(NOT_SUBMITTED_HEADLINE)


def test_browser_open_defaults_to_false_so_a_window_is_never_promised_by_omission():
    """The conservative direction. A caller that forgets to say must not have the
    report invent a window for it."""
    assert build_report(None).browser_open is False
    assert "No browser window is open" in build_report(None).render_text()


def test_submitted_is_a_false_field_and_nothing_sets_it_true():
    """Task 10 renders this in a web UI and will want a flag, not prose. It is
    `False` and there is no code path in the package that changes it."""
    assert build_report(None).submitted is False
    package = pathlib.Path(handoff_mod.__file__).parent.parent
    for path in package.rglob("*.py"):
        source = path.read_text()
        assert "submitted=True" not in source, path
        assert "submitted = True" not in source, path


def test_the_handoff_writes_no_dom_code_at_all():
    """Task 7 renders text. It has no page, and it must not grow one — a report
    module that can reach the DOM is a report module that can click.

    AST, not grep: `textwrap.fill(...)` contains the substring `.fill(`, and a
    substring guard that had to whitelist that would also whitelist a
    `locator.fill(...)`. So every attribute call is checked by name, and the one
    legitimate `fill` is required to be `textwrap`'s.
    """
    banned = {
        "click", "dblclick", "tap", "press", "press_sequentially", "submit",
        "dispatch_event", "set_input_files", "check", "uncheck", "select_option",
        "goto", "evaluate", "type", "keyboard", "locator", "content",
    }
    tree = ast.parse(HANDOFF_SOURCE)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        assert attr not in banned, f"handoff.py calls .{attr}()"
        if attr == "fill":
            receiver = node.func.value
            assert isinstance(receiver, ast.Name) and receiver.id == "textwrap", (
                "the only .fill() allowed here is textwrap.fill()"
            )
    # Prose about Playwright is fine (the module explains where a leaked path
    # comes from); an import of it is not.
    assert not re.search(r"^\s*(?:import|from)\s+playwright", HANDOFF_SOURCE, re.M)
    assert "browser" not in {
        n.names[0].name.split(".")[-1]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and n.names
    }


# ===========================================================================
# Ordering: by what stops her submitting, not by what the agent did
# ===========================================================================


def test_the_report_leads_with_required_and_unanswered(resume):
    report, _, _, _ = _build("lever", resume_path=resume)
    text = report.render_text()
    positions = {
        band: text.index(handoff_mod._BAND_HEADING[band])
        for band in GROUPS if report.group(band)
    }
    assert positions[BLOCKING] < positions[REVIEW] < positions[DONE]
    # And not vacuously: Lever really does produce all three bands.
    assert report.group(BLOCKING) and report.group(REVIEW) and report.group(DONE)


def test_nothing_that_was_filled_successfully_appears_above_a_blocking_field(resume):
    """The failure this prevents is a report whose first screen is eighteen
    green ticks and whose blocking work-authorization question is on screen
    three."""
    report, _, _, _ = _build("lever", resume_path=resume)
    lines = report.render_text().splitlines()
    # By BULLET, not by substring: a filled value like "University of Wisconsin"
    # also appears inside a blocking field's note ("your profile implies …"), so
    # searching for the text finds the wrong occurrence and the test passes for
    # the wrong reason.
    bullets = [(n, line.strip()[0]) for n, line in enumerate(lines) if line.strip()]
    blocking_lines = [n for n, b in bullets if b == "!"]
    done_lines = [n for n, b in bullets if b == "✓"]
    assert blocking_lines and done_lines
    assert max(blocking_lines) < min(done_lines)
    # And the concrete version of the same thing: the first screen she sees.
    first_screen = [line.strip() for line in lines[:25]]
    assert any(line.startswith("!") for line in first_screen)
    assert not any(line.startswith("✓") for line in first_screen)


def test_the_header_opens_with_a_count_so_she_can_gauge_the_work(resume):
    report, _, questions, _ = _build("lever", resume_path=resume)
    summary = report.summary_line()
    assert summary.startswith(f"{len(report.needs_you)} of {report.total} fields need you.")
    assert f"{len(report.blocking)} of those are required and still empty" in summary
    # The count is over real fields, not over bands: 29 questions on Lever, one
    # of which (Resume/CV) the attach path speaks for instead of the resolver.
    assert report.total == len(questions) == 29
    assert summary in " ".join(report.render_text().split())


def test_the_counts_partition_every_reported_field(resume):
    for board in ("lever", "greenhouse", "ashby"):
        report, _, _, _ = _build(board, resume_path=resume)
        assert sum(report.counts().values()) == report.total
        assert len(report.needs_you) == len(report.group(BLOCKING)) + len(report.group(REVIEW))


def test_a_one_field_report_is_not_written_in_broken_english():
    report = _report_of(
        [_outcome("Email", BLANK, note="type it yourself.")],
        [_q("Email", required=True)],
    )
    assert report.summary_line() == (
        "1 of 1 field needs you. 1 of those is required and still empty, so the "
        "form will not submit until you deal with it."
    )


# ===========================================================================
# All four statuses, reported distinctly — and `superseded` honoured
# ===========================================================================


def test_all_four_statuses_get_four_different_reasons_and_four_different_tags():
    outcomes = [
        _outcome("Full name", FILLED, value="Testy McTestface", source="profile"),
        _outcome("Phone", CHANGED, value="(555) 0142", intended="5550142", source="profile"),
        _outcome("Country", BLANK, note="fill it yourself."),
        _outcome("Résumé", ATTACHED, value="cv.pdf", intended="cv.pdf",
                 kind="file_upload", source="file"),
    ]
    report = _report_of(outcomes)
    reasons = [i.reason for i in report.items]
    assert reasons == ["filled", REASON_CHANGED, REASON_EMPTY, "attached"]
    tags = {handoff_mod._REASON_TAG[r] for r in reasons}
    assert len(tags) == 4, "two statuses share one tag, so the user cannot tell them apart"


def test_a_reformatted_value_is_reported_as_changed_not_as_blank():
    """A phone mask rewriting `5550142` as `(555) 0142` genuinely accepted the
    value. Calling that `blank` — "we could not fill this" — is untrue, and it
    would send her to a field that is already correct."""
    report = _report_of(
        [_outcome("Phone", CHANGED, value="(555) 0142", intended="5550142",
                  source="profile", note="it reformatted or truncated it. Check it.")],
        [_q("Phone", required=True)],
    )
    item = report.items[0]
    assert item.reason == REASON_CHANGED and item.status == CHANGED
    text = report.render_text()
    assert "(555) 0142" in text and "5550142" in text
    assert "the page rewrote your value" in text


def test_a_changed_required_field_does_not_block_because_the_form_will_submit():
    """Ordering is by what stops her submitting. A rewritten value is wrong, not
    missing — it belongs in review, above nothing and below the empty required
    fields that actually block."""
    report = _report_of(
        [_outcome("Phone", CHANGED, value="(555) 0142", intended="5550142")],
        [_q("Phone", required=True)],
    )
    assert report.items[0].group == REVIEW
    assert not report.group(BLOCKING)


def test_a_superseded_question_is_reported_exactly_once(resume):
    """`fill_form` suppresses the resolver's answer for the résumé slot, because
    rendering both showed "Resume/CV — blank, attach it yourself" for a slot the
    agent had just attached to. The report must not resurrect it."""
    report, fill_report, _, _ = _build("lever", resume_path=resume)
    assert fill_report.superseded == ["resume"], "the fixture must exercise this"
    resume_items = [i for i in report.items if "resume" in i.label.lower()]
    assert len(resume_items) == 1
    assert resume_items[0].group == DONE and resume_items[0].status == ATTACHED
    text = report.render_text()
    assert "attach it yourself" not in text
    assert text.count("testy-mctestface-resume.pdf") == 1


def test_a_superseded_key_is_dropped_even_if_an_outcome_carries_it():
    """Belt and braces for the shape above: `fill_form` never emits an outcome
    for a key it superseded, and if that ever changes the report must still not
    print the résumé slot twice."""
    report = build_report(
        FillReport(
            outcomes=[
                _outcome("Resume/CV", BLANK, key="resume", kind="file_upload",
                         note="attach it yourself."),
                _outcome("Resume/CV", ATTACHED, key="__resume__", value="cv.pdf",
                         intended="cv.pdf", kind="file_upload", source="file"),
            ],
            superseded=["resume"],
        ),
        questions=[_q("Resume/CV", key="resume", kind="file", required=True)],
    )
    assert len(report.items) == 1
    assert report.items[0].status == ATTACHED


def test_a_failed_attach_on_a_required_resume_slot_is_blocking():
    """The résumé outcome's own key is a synthetic `__resume__` that matches no
    question, so its `required` flag has to come from the question whose answer
    was superseded for it. Without that link a failed attach on Lever's REQUIRED
    Resume/CV lands in review, below optional blanks."""
    report = build_report(
        FillReport(
            outcomes=[_outcome("Resume/CV", BLANK, key="__resume__",
                               kind="file_upload", source="file", intended="cv.pdf",
                               note="the résumé did not land.")],
            resume=None,
            superseded=["resume"],
        ),
        questions=[_q("Resume/CV", key="resume", kind="file", required=True)],
    )
    # `resume=None` on purpose: the link must come from `superseded`, not from
    # an identity check against `FillReport.resume`.
    assert report.items[0].required is True
    assert report.items[0].group == BLOCKING


def test_the_resume_slot_is_never_dressed_up_as_a_profile_suggestion():
    """`file_upload` is in BLOCKING_KINDS and the attach outcome carries the
    FILENAME in `intended`, so a suggestion rule keyed on those two alone prints
    "your profile implies “cv.pdf” — confirm it yourself" about a résumé.

    Both halves matter, and the FAILED attach is the one that bites: a
    successful attach is `ATTACHED` and never reaches the suggestion branch at
    all, while a failed one is `BLANK` with `kind="file_upload"` — reason
    `refused` — and only `source` tells it apart from a work-authorization
    answer the executor declined to type.
    """
    attached = _report_of([
        _outcome("Résumé", ATTACHED, value="cv.pdf", intended="cv.pdf",
                 kind="file_upload", source="file"),
    ])
    assert attached.items[0].suggestion == ""
    assert "SUGGESTION" not in attached.render_text()

    failed = build_report(FillReport(outcomes=[
        _outcome("Resume/CV", BLANK, key="__resume__", intended="cv.pdf",
                 kind="file_upload", source="file",
                 note="the résumé did not land — attach it yourself."),
    ]))
    assert failed.items[0].reason == REASON_REFUSED
    assert failed.items[0].suggestion == ""
    assert "SUGGESTION" not in failed.render_text()
    assert "your profile implies" not in failed.render_text()


# ===========================================================================
# The three different reasons a field was left alone (never merged)
# ===========================================================================


def test_a_refused_answer_shows_a_suggestion_she_confirms_not_an_answer():
    """The resolver DID derive "Yes" from the profile; the executor refused to
    type it because work authorization decides whether the application is
    considered at all. Rendering that as an answer would be a lie about a real
    application."""
    report = _report_of(
        [_outcome(
            "Are you authorized to work in the US?", BLANK, kind="work_auth",
            source="profile", intended="Yes",
            note="left for you deliberately. Your profile implies “Yes”, but this "
                 "question decides whether the application is considered at all.",
        )],
        [_q("Are you authorized to work in the US?", required=True)],
    )
    item = report.items[0]
    assert item.reason == REASON_REFUSED and item.suggestion == "Yes"
    assert item.value == "", "nothing is in the field, and the report must not imply otherwise"
    flat = " ".join(report.render_text().split())
    assert "SUGGESTION, NOT ENTERED" in flat
    assert "confirm it yourself" in flat


def test_a_refused_field_is_tagged_differently_from_one_that_merely_failed():
    """"The agent will never answer this" and "the agent tried and could not"
    need different actions from her, so they must not share a tag."""
    report = _report_of([
        _outcome("Do you require sponsorship?", BLANK, kind="sponsorship",
                 source="blank", note="answer it yourself."),
        _outcome("Country", BLANK, kind="other", source="blank", note="fill it in."),
    ])
    refused, empty = report.items
    assert refused.reason == REASON_REFUSED and empty.reason == REASON_EMPTY
    assert handoff_mod._REASON_TAG[REASON_REFUSED] != handoff_mod._REASON_TAG[REASON_EMPTY]
    text = " ".join(report.render_text().split())
    assert handoff_mod._REASON_TAG[REASON_REFUSED] in text
    assert handoff_mod._REASON_TAG[REASON_EMPTY] in text


def test_every_blocking_kind_is_reported_as_refused_not_as_a_failure():
    """Work auth, sponsorship, citizenship, work documents, consent and file
    uploads. Enumerated from `BLOCKING_KINDS` itself, so a kind added there
    later cannot quietly start reporting as "the agent tried and failed"."""
    outcomes = [
        _outcome(f"Question about {kind}", BLANK, key=kind, kind=kind, source="blank")
        for kind in sorted(resolver.BLOCKING_KINDS)
    ]
    report = _report_of(outcomes)
    assert {i.reason for i in report.items} == {REASON_REFUSED}
    assert len(report.items) == len(resolver.BLOCKING_KINDS) >= 6


def test_unreadable_questions_are_never_silently_dropped():
    """`discover_questions` withholds a question with no readable label because
    the EEO screen matches on the label and therefore cannot screen it. That is
    a real field on a real form; dropping it from the report would leave her
    submitting a form with an unanswered required question she never saw."""
    report = build_report(
        None,
        unreadable=[_q("", key="u1", required=True, kind="checkbox",
                       options=["Male", "Female", "Decline to self-identify"])],
    )
    item = report.items[0]
    assert item.reason == REASON_UNREADABLE and item.group == BLOCKING
    flat = " ".join(report.render_text().split())
    assert "no readable label" in flat
    assert "find this one on the page yourself" in flat
    # The label is the one thing missing, so everything else that could locate
    # the field is offered instead.
    assert "checkbox" in flat and "“Decline to self-identify”" in flat


def test_an_optional_unreadable_question_is_review_not_blocking():
    report = build_report(None, unreadable=[_q("", key="u2", required=False)])
    assert report.items[0].group == REVIEW


def test_withheld_eeo_is_stated_so_it_does_not_look_like_a_bug():
    """She will notice that the gender and veteran questions are untouched. If
    the report is silent about them she goes looking for a bug that is not
    there."""
    report = build_report(
        None, withheld_eeo=[_q("Gender", key="gender"), _q("Veteran status", key="vet")]
    )
    assert [i.group for i in report.items] == [WITHHELD, WITHHELD]
    assert [i.reason for i in report.items] == [REASON_WITHHELD_EEO] * 2
    flat = " ".join(report.render_text().split())
    assert "LEFT UNTOUCHED ON PURPOSE" in flat
    assert "this is not a bug" in flat
    assert "Gender" in flat and "Veteran status" in flat


def test_withheld_eeo_does_not_inflate_the_count_of_what_needs_her():
    """It needs a decision only if she wants to self-identify. Counting two EEO
    questions as work makes "7 of 29 need you" into "9 of 29" and the number
    stops meaning anything."""
    report = build_report(
        FillReport(outcomes=[_outcome("Full name", FILLED, value="Testy")]),
        withheld_eeo=[_q("Gender", key="g"), _q("Race", key="r")],
    )
    assert report.total == 3 and len(report.needs_you) == 0
    assert "0 of 3 fields need you" in report.summary_line()


def test_the_three_left_alone_reasons_never_collapse_into_one_tag():
    """The property, stated directly: refused / unreadable / withheld are three
    different jobs for the user and must read as three different things."""
    tags = {
        handoff_mod._REASON_TAG[r]
        for r in (REASON_REFUSED, REASON_UNREADABLE, REASON_WITHHELD_EEO)
    }
    assert len(tags) == 3
    report = build_report(
        FillReport(outcomes=[
            _outcome("Do you require sponsorship?", BLANK, kind="sponsorship"),
        ]),
        unreadable=[_q("", key="u")],
        withheld_eeo=[_q("Gender", key="g")],
    )
    text = " ".join(report.render_text().split())
    for tag in tags:
        assert tag in text


# ===========================================================================
# Drafted answers are visibly flagged, and the marker survives
# ===========================================================================


def test_a_drafted_answer_is_flagged_and_never_sits_in_the_filled_band():
    """It read back correctly, so `FILLED` is the honest status — but "filled
    and verified" is the wrong BAND for a paragraph a language model invented
    about the user's own life."""
    value = drafting.mark(_DRAFT_BODY)
    report = _report_of([
        _outcome("Why do you want to work here?", FILLED, value=value,
                 intended=value, source="drafted", kind="free_text", drafted=True),
    ])
    item = report.items[0]
    assert item.group == REVIEW and item.reason == REASON_DRAFTED
    assert "AI-DRAFTED — read every word" in report.render_text()


def test_a_drafted_value_keeps_its_marker_even_when_excerpted():
    """A 4,000-character draft is excerpted for display. If the marker only
    survived because it happens to fit inside the cap, a future cap change would
    silently un-flag an AI draft in the one place the human looks."""
    huge = drafting.mark("x " * 4000)
    report = _report_of([
        _outcome("Cover letter", FILLED, value=huge, source="drafted",
                 kind="free_text", drafted=True),
    ])
    text = report.render_text()
    assert is_marked(text)
    assert len(text) < len(huge), "the draft was not excerpted at all"


def test_the_marker_survives_even_if_it_is_not_at_the_front():
    """`_excerpt` re-attaches the marker rather than relying on `mark()` putting
    it first. Pins the property, not today's layout."""
    trailing = ("y " * 4000) + DRAFT_MARKER
    assert is_marked(handoff_mod._excerpt(trailing, 100))


def test_the_marker_is_detected_through_is_marked_not_a_second_hardcoded_copy():
    """Two copies of the literal drift apart, and the copy that drifts is the
    one that stops flagging AI text on a real application."""
    assert "[AI-DRAFTED" not in HANDOFF_SOURCE
    assert "is_marked(" in HANDOFF_SOURCE


def test_the_draft_marker_is_not_stripped_from_the_value_shown(resume):
    report, _, _, _ = _build("lever", resume_path=resume)
    drafted = [i for i in report.items if i.drafted]
    assert len(drafted) == 2, "Lever has exactly two draftable free-text questions"
    for item in drafted:
        assert is_marked(item.value)


# ===========================================================================
# Sections: grouped where the board publishes them, silent where it does not
# ===========================================================================


def test_lever_is_grouped_under_its_real_section_headings(resume):
    report, _, questions, _ = _build("lever", resume_path=resume)
    text = report.render_text()
    assert len({q.section for q in questions}) == 14, "the fixture must have sections"
    for heading in ("Work Authorization", "Links", "Supplementary Questions",
                    "An Inflection Point", "AU Clearance Confirmation"):
        assert f"\n  {heading}\n" in text, heading


def test_boards_without_sections_get_no_section_headings_at_all(resume):
    """MEASURED: Greenhouse and Ashby publish no heading element the locator can
    attribute a field to, so every `Question.section` is "". A naive grouping
    prints "(no section)" on all 31 of their rows."""
    for board in ("greenhouse", "ashby"):
        report, _, questions, _ = _build(board, resume_path=resume)
        assert {q.section for q in questions} == {""}, board
        text = report.render_text()
        assert "(no section)" not in text
        assert handoff_mod._HEADING_FALLBACK not in text
        assert "\n  \n" not in text, "an empty heading line was printed"


def test_a_board_with_some_sections_puts_the_rest_under_one_fallback_heading():
    report = _report_of(
        [_outcome("A", BLANK), _outcome("B", BLANK), _outcome("C", BLANK)],
        [_q("A", section="Work Authorization"), _q("B"), _q("C", section="Links")],
    )
    text = report.render_text()
    assert "  Work Authorization\n" in text
    assert "  Links\n" in text
    # And the sectionless one is last, not interleaved.
    assert text.index(handoff_mod._HEADING_FALLBACK) > text.index("  Links")


def test_a_paragraph_length_section_heading_is_truncated():
    """Lever's video-prompt section heading is 400+ characters of instructions.
    It is a heading in name only, and printing it whole buries the two questions
    under it."""
    report, _, questions, _ = _build("lever")
    long_section = max((q.section for q in questions), key=len)
    assert len(long_section) > 300
    text = report.render_text()
    assert long_section not in text
    assert "Video Prompts: After recording your clips" in text
    for line in text.splitlines():
        assert len(line) <= 110, line


# ===========================================================================
# Structure and text (Task 10 wants the structure)
# ===========================================================================


def test_build_report_returns_structured_data_as_well_as_text(resume):
    report, _, _, _ = _build("lever", resume_path=resume)
    assert isinstance(report, HandoffReport)
    assert all(isinstance(i, ReportItem) for i in report.items)
    assert all(i.group in GROUPS for i in report.items)
    assert all(i.reason in handoff_mod.REASONS for i in report.items)
    assert isinstance(report.render_text(), str)
    # Frozen, so a UI cannot mutate the record of what happened.
    with pytest.raises(Exception):
        report.items[0].group = DONE


def test_the_filled_band_is_collapsible_without_losing_its_count(resume):
    report, _, _, _ = _build("lever", resume_path=resume)
    full = report.render_text()
    folded = report.render_text(show_filled=False)
    assert len(folded) < len(full)
    heading = f"{handoff_mod._BAND_HEADING[DONE]}  ({len(report.group(DONE))})"
    assert heading in folded and heading in full
    assert "Testy McTestface" in full and "Testy McTestface" not in folded
    # The band is still last, and the structure still carries every item.
    assert len(report.group(DONE)) == 8


def test_every_test_the_module_docstring_cites_actually_exists():
    """Eight findings across Tasks 4-6 were prose asserting a property the code
    did not have. `handoff.py` answers that by naming the test that pins each
    claim — which is worth nothing if the names rot, so the citations are
    themselves checked. This fails when a test is renamed or deleted and the
    docstring is not updated with it."""
    cited = set(re.findall(r"\b(test_[a-z0-9_]+)", HANDOFF_SOURCE))
    assert len(cited) >= 12, "the docstring stopped citing its tests"
    defined = set(re.findall(r"^def (test_[a-z0-9_]+)", pathlib.Path(__file__).read_text(), re.M))
    assert cited <= defined, f"cited but missing: {sorted(cited - defined)}"


def test_the_module_level_render_text_matches_the_method(resume):
    report, _, _, _ = _build("greenhouse", resume_path=resume)
    assert handoff_mod.render_text(report) == report.render_text()


# ===========================================================================
# Basenames, never absolute paths
# ===========================================================================


def test_absolute_paths_are_reduced_to_basenames(tmp_path):
    """The leak is not a path this module chose to print: Playwright's exception
    message quotes the path it was handed, and `fill.attach_resume` puts that
    message verbatim into the note."""
    report = _report_of([
        _outcome("Résumé", BLANK, kind="file_upload", source="file",
                 note="the résumé could not be attached (Error: ENOENT: no such file, "
                      "open '/Users/kayla.li/Documents/résumé final v3.pdf') — "
                      "attach it yourself."),
    ])
    text = report.render_text()
    assert "/Users/kayla.li" not in text
    assert "Documents" not in text
    assert "résumé final v3.pdf" in text


def test_urls_are_not_mistaken_for_paths():
    """The path rule has to leave every URL alone — half the fields it reports
    are LinkedIn, GitHub and portfolio links, and `linkedin.com/in/name` reduced
    to `name` would be a wrong value shown as a right one."""
    for url in (
        "https://www.linkedin.com/in/testy-mctestface",
        "https://github.com/testy/some/deep/path",
        "http://example.invalid/a/b/c?d=e",
        "jobs.lever.co/palantir/1234/apply",
    ):
        assert handoff_mod._basenames(url) == url
    assert handoff_mod._basenames("~/Documents/cv.pdf") == "cv.pdf"
    assert handoff_mod._basenames("/tmp") == "/tmp"


def test_no_absolute_path_reaches_the_user_on_the_real_boards(resume):
    """`resume` is a real `tmp_path` file, so `fill.attach_resume` handles a
    genuine absolute path end-to-end."""
    for board in ("lever", "greenhouse", "ashby"):
        report, _, _, _ = _build(board, resume_path=resume)
        text = report.render_text()
        assert str(pathlib.Path(resume).parent) not in text
        assert "testy-mctestface-resume.pdf" in text


# ===========================================================================
# Robustness: this is the last thing that runs
# ===========================================================================


def test_a_total_failure_still_renders_a_report():
    report = build_report(
        None,
        error="Playwright timed out loading /Users/kayla.li/tmp/form.html after 30s.",
        job_title="Software Engineer Intern",
        company="Palantir",
    )
    text = report.render_text()
    assert text.startswith(NOT_SUBMITTED_HEADLINE)
    assert "THE AGENT STOPPED EARLY" in text
    assert "treat the form as unfilled" in " ".join(text.split())
    assert "/Users/kayla.li" not in text, "even the error message is basenamed"
    assert not _claims_submission(text)


def test_an_all_blank_form_still_renders_something_honest():
    report, _, _, _ = _build("lever", profile={})
    assert not report.group(DONE)
    assert report.total == 29
    text = report.render_text()
    assert not _claims_submission(text)
    assert "need you" in text


def test_a_missing_resume_is_said_once_in_the_header_not_as_a_ghost_field():
    """With no résumé path, `attach_resume` still returns an outcome and
    `fill_form` still appends it — but nothing was superseded, so the form's own
    Resume/CV question is reported too. Rendering both put two contradictory
    résumé rows in front of the user ("the agent attaches your résumé itself,
    last" and "no résumé file was given") and inflated Lever's 29 fields to 30.
    """
    report, fill_report, questions, _ = _build("lever")
    assert fill_report.superseded == [] and fill_report.resume.status == BLANK
    assert report.total == len(questions) == 29
    assert "no résumé file was given" in report.resume_note
    flat = " ".join(report.render_text().split())
    assert flat.count("no résumé file was given") == 1
    assert "RÉSUMÉ: no résumé file was given" in flat
    # The form's own résumé question is still a row, and still hers to do.
    resume_rows = [i for i in report.items if i.label == "Resume/CV"]
    assert len(resume_rows) == 1 and resume_rows[0].group == BLOCKING


def test_a_page_locator_whose_accessors_raise_does_not_break_the_report():
    """A handoff that throws leaves the user with a filled browser window and no
    idea what is in it."""

    class _Exploding:
        def questions(self):
            raise RuntimeError("boom")

        def unreadable(self):
            raise RuntimeError("boom")

        def withheld_eeo(self):
            raise RuntimeError("boom")

    report = build_report(
        FillReport(outcomes=[_outcome("Full name", FILLED, value="Testy")]),
        page_locator=_Exploding(),
    )
    assert report.total == 1
    assert report.render_text().startswith(NOT_SUBMITTED_HEADLINE)


def test_an_outcome_for_a_question_the_report_never_saw_is_still_reported():
    """Missing metadata degrades to "optional, no section" — it never drops the
    row, because a dropped row is a field she never checks."""
    report = _report_of([_outcome("Mystery field", BLANK, note="fill it in.")])
    assert report.total == 1
    assert report.items[0].section == "" and report.items[0].required is False


# ===========================================================================
# Cross-checks against the interfaces this module is built on
# ===========================================================================


def test_everything_fill_calls_needs_review_lands_in_blocking_or_review(resume):
    """`FillReport.needs_review` is the executor's own view of what the human
    must look at. The report may order and split it, but it may not lose any of
    it into the collapsed "filled and verified" band."""
    for board in ("lever", "greenhouse", "ashby"):
        report, fill_report, _, _ = _build(board, resume_path=resume)
        done_keys = {i.key for i in report.group(DONE)}
        for outcome in fill_report.needs_review:
            if outcome.key in fill_report.superseded:
                continue
            assert outcome.key not in done_keys, (board, outcome.key)
        assert fill_report.needs_review, board


def test_the_blocking_band_is_what_the_page_holds_not_what_the_resolver_intended():
    """`resolver.blocking()` answers the question over INTENTS, before anything
    was typed. A required field whose write the page swallowed still has a
    non-empty `Answer.value`, so `blocking()` omits it — while in fact the form
    will not submit. That divergence is why the band is computed from the
    outcome, and this test fails if someone "simplifies" it back."""
    question = _q("Full name", required=True)
    answer = Answer(question=question, value="Testy McTestface",
                    source="profile", kind="full_name")
    assert resolver_blocking([answer]) == [], "the resolver sees a perfectly good answer"

    swallowed = _outcome("Full name", BLANK, key=question.key, source="profile",
                         intended="Testy McTestface",
                         note="the field would not accept its value.")
    report = _report_of([swallowed], [question])
    assert [i.key for i in report.group(BLOCKING)] == [question.key]


def test_blocking_kinds_are_read_from_the_resolver_not_re_listed():
    """One vocabulary. A kind added to `BLOCKING_KINDS` must start reporting as
    "yours to answer" here with no edit to this module."""
    assert "BLOCKING_KINDS" in HANDOFF_SOURCE
    for kind in ("work_auth", "sponsorship", "citizenship", "consent", "file_upload"):
        assert kind in resolver.BLOCKING_KINDS
        assert f'"{kind}"' not in HANDOFF_SOURCE, f"{kind} is re-listed in handoff.py"
    # Same rule for the résumé outcome's synthetic key: imported from `fill`,
    # never a second copy of the literal.
    assert '"__resume__"' not in HANDOFF_SOURCE
    assert "RESUME_KEY" in HANDOFF_SOURCE


# ===========================================================================
# The real boards, end to end
# ===========================================================================


@pytest.mark.parametrize("board,expected", [("lever", 29), ("greenhouse", 15), ("ashby", 16)])
def test_a_report_is_built_from_every_real_board(board, expected, resume):
    report, _, questions, _ = _build(board, resume_path=resume)
    assert len(questions) == expected
    assert report.total == expected
    text = report.render_text()
    assert not _claims_submission(text)
    assert report.group(DONE), f"{board} filled nothing at all"
    assert report.group(BLOCKING) or report.group(REVIEW)


def test_the_lever_report_surfaces_its_blocking_work_authorization_questions(resume):
    report, _, _, _ = _build("lever", resume_path=resume)
    blocking_labels = " | ".join(i.label for i in report.group(BLOCKING))
    assert "legally authorized to work" in blocking_labels
    assert "require sponsorship" in blocking_labels
    refused = [i for i in report.group(BLOCKING) if i.reason == REASON_REFUSED]
    assert len(refused) == 2


def test_the_report_never_promises_a_draft_that_was_already_declined(resume):
    """The resolver blanks every free-text question with "left for the AI
    drafting step, which marks its output as AI-drafted for you to review". On
    Lever's two video prompts drafting then DECLINES — the box wants a YouTube
    URL, not prose — and if the merge keeps the resolver's note the report tells
    her a draft is coming that never will.

    This pins the merge policy the Task 8 graph has to implement (see `_merge`),
    and it pins the report property that depends on it.
    """
    report, _, _, _ = _build("lever", resume_path=resume)
    prompts = [i for i in report.items if i.label.startswith("Prompt ")]
    assert len(prompts) == 2
    for item in prompts:
        assert "left for the AI drafting step" not in item.note
        assert "video or audio recording" in item.note
    assert "left for the AI drafting step" not in report.render_text()


def test_no_wrong_school_reaches_the_collapsed_filled_band(resume):
    """The report is how this bug was found, so the invariant is pinned at the
    report level too, not only in `test_applier_resolver.py`.

    "High School Name" resolved to the profile's UNIVERSITY with
    `source="profile"` — which put it in `DONE`, the band rendered last, folded
    and one line per field. A false statement about her education, in the place
    she is least likely to look. It is now empty — and because Lever marks it
    required, it has moved all the way to the FRONT of the report, into the band
    that leads: the honest position for a required field nobody has answered.
    """
    report, _, _, _ = _build("lever", resume_path=resume)
    high_school = next(i for i in report.items if i.label == "High School Name")
    assert high_school.required and high_school.group == BLOCKING
    assert high_school.value == ""
    assert PROFILE["school"] not in high_school.value
    assert "level of schooling your profile does not store" in high_school.note

    # The invariant, over the whole board: the profile's school appears only
    # where a school was actually asked for.
    for item in report.group(DONE):
        assert PROFILE["school"] not in item.value, item.label
    # Not vacuous — the real school question is still answered, it is just not
    # on this fixture's happy path (its options list no plain "University of
    # Wisconsin"), so assert the rule fired rather than that nothing exists.
    assert sum(1 for i in report.items if "High School" in i.label) == 2


def test_the_greenhouse_report_keeps_the_cover_letter_slot_as_hers(resume):
    """The résumé slot is superseded by the attach; the OTHER file field is not,
    and must still be reported as something she has to do herself."""
    report, _, _, _ = _build("greenhouse", resume_path=resume)
    labels = {i.label: i for i in report.items}
    assert labels["Resume/CV"].status == ATTACHED
    assert labels["Cover Letter"].reason == REASON_REFUSED
    assert labels["Cover Letter"].group == REVIEW
