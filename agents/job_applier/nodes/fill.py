"""Type resolved answers into a live application form, verifying every one.

This is the first module in the project that MUTATES a page. Everything before
it reads: `schema_greenhouse` parses JSON, `locate_dom` parses HTML, `resolver`
is pure, `drafting` calls a model. So the safety properties that were previously
"nothing here can go wrong because nothing here acts" have to be enforced
explicitly from here on.

THE ONE RULE for all of Phase B — no code path may ever click a submit button —
is enforced four ways in this module, not one:

  1. **Nothing here clicks, and nothing here scripts the page.** There is no
     `.click()`, `.dblclick()`, `.tap()`, `.submit()`, `.press()`,
     `.dispatch_event()` and no `page.keyboard` in this file. Filling,
     selecting, checking and attaching are the only four things it does.
     (`test_the_fill_executor_never_clicks_a_submit_control`.)
  2. **A source scan, whose exact guarantee is narrower than it looks.** It
     rejects: any click-family call at all; any `evaluate`-family call whose
     JavaScript mentions submitting or clicking, or whose JavaScript is not a
     plain literal; and any submit-shaped string literal *passed to a selector
     lookup*. It does NOT reject the word "submit" appearing anywhere else —
     which is why `_SUBMITISH_RE` below can contain it. What the scan cannot
     see at all is written down in `_UNSCANNABLE` in the test module rather
     than glossed over. Proved in BOTH directions — it accepts a filling module
     and rejects nine separate clicking ones — by
     `test_the_submit_guard_accepts_filling_and_rejects_clicking`, because a
     guard that never fails on anything is worse than no guard.
  3. **At runtime**, `_is_submitish` refuses to act on any control whose label,
     name, id or selector reads like a submit control, even though
     `locate_dom` already declines to discover `type=submit`. The check lives
     inside `_single_locator`, so it is impossible to obtain a locator for such
     a control at all rather than merely impolite to.
     (`test_a_control_that_looks_like_submit_is_refused_at_runtime`.)
  4. **No Enter keystroke can reach a single-line field.** HTML's *implicit
     submission* means Enter in a text input inside a `<form>` submits it — THE
     ONE RULE broken with no click anywhere, and with the source scan green.
     The character-by-character retry is therefore refused for a value
     containing a newline in anything but a `<textarea>`.

     "Newline" here means `\\n` **or `\\r`**, and that pair is exhaustive rather
     than a guess: Playwright's driver holds ONE character→key alias map
     (`lib/coreBundle.js`, `aliases`), whose only character entry is
     ``["Enter", ["\\n", "\\r"]]``. Every other character goes through
     `insertText` and presses no key. A first version checked `\\n` alone, so a
     classic-Mac or stray `\\r` would have typed Enter into a single-line input.
     See `_may_type_character_by_character`; this is the one hazard a
     click-scanning source guard would not have caught.

Design decisions, and why
=========================

**1. Locate by `Question.label`, never a literal string.** `find_control` does
token-prefix matching, so a short query can resolve — with no ambiguity to
detect and therefore no warning — to the wrong field: on the captured Lever form
`find_control(controls, "Name")` returns the *pronunciation* box. Every lookup
here is driven by the `Question` object the resolver was given, so it hits the
exact-match tier. The Greenhouse fallback is `find_by_key`, which is an exact
identifier match (the schema's key IS the DOM `id`/`name`), not prose matching.
A token cap on queries was considered and rejected: it would break the legitimate
`"Resume"` → `"Resume/CV and supporting documents"` match. The rule is
structural instead — the executor is never handed a bare string to look up.
(`test_every_lookup_goes_through_a_question_object`.)

**2. Read the value back after every write, and treat a silent no-op as a
failure.** React-controlled inputs routinely swallow programmatic input:
`fill()` sets `.value` and dispatches one `input` event, and a component that
re-renders from its own state simply overwrites it. Nothing throws. So every
write is followed by a read, and the three outcomes are distinguished:

  * read-back equals the value       -> `filled`
  * read-back is empty               -> the write did nothing -> retry, then `blank`
  * read-back is non-empty but different -> `changed`, flagged for review

`changed` exists because "the field will not accept your value" and "the field
reformatted your value" are different facts and reporting the second as `blank`
is simply untrue — a phone mask turning `5550123` into `(555) 0123` did accept
it. Retrying a `changed` field would double-type into it, so it is never
retried. (`test_a_reformatting_field_is_reported_changed_not_blank`.)

**3. The retry policy is: at most ONE retry, and it must use a DIFFERENT
strategy.** `MAX_ATTEMPTS = 2`. Attempt 1 is `fill()`; attempt 2 clears the
field and types it character by character with `press_sequentially`, which
produces the real `keydown`/`keypress`/`input`/`keyup` sequence a controlled
component is listening for. Repeating `fill()` would repeat the same failure at
the same cost, so it is not done. Bounded at two attempts because an unbounded
loop against a field that structurally cannot accept the value costs the user
their whole session, and a field the agent cannot fill is a field the human
fills in ten seconds — `blank` with the reason is the cheap, honest outcome.
Selects and checkboxes get **no** retry: the only meaningfully different second
strategy for them is clicking the option, and this module clicks nothing.
(`test_a_swallowed_fill_is_retried_once_with_a_different_strategy`,
`test_a_field_that_never_accepts_its_value_is_not_retried_forever`.)

**4. Nothing the resolver refused is ever typed.** Two independent gates, both
checked before a locator is even requested: the answer's `kind` must not be in
`resolver.BLOCKING_KINDS`, and its `source` must be `profile` or `drafted`.
Work-authorization answers are refused **even when the resolver did produce a
value from the profile** — those are the answers that decide whether an
application is considered at all, so they are confirmed by the human, never
typed by the agent. The suggested value is carried into the note so the handoff
can show it. This is proved by a spy that records every write and asserts the
list is empty, NOT by asserting the outcome is blank: Task 5 shipped a refusal
test that asserted the outcome and a mutation routing a refused question
straight through passed all 56 tests.
(`test_a_blocking_answer_never_reaches_a_write_call`.)

**5. The AI-draft marker is never stripped.** Drafted values arrive already
carrying `drafting.DRAFT_MARKER`; it is meant to be typed in, so that a
recruiter sees it if the human skips the review step. This module uses the
exported `is_marked()` to annotate the outcome and does not re-hardcode the
literal, and the exact string it was handed is the exact string it types.
(`test_a_drafted_value_is_typed_marker_and_all`.)

**6. Radio and checkbox groups have no single element.** `find_control` on a
group heading correctly returns `None` — there is no element that *is* the
group. `find_group_options` returns the members, each with its own option label
and its own selector, and the one whose label matches the answer is checked.
An answer matching no option is `blank`; an answer matching more than one is
`blank` too. (`test_a_radio_group_is_answered_through_its_options`.)

**7. File inputs are EXEMPT from the visibility gate.** Every other control is
checked with `is_visible()` first, so a hidden duplicate produces an immediate
`blank` with a reason instead of a 5-second actionability timeout. File inputs
skip that check entirely and deliberately: Ashby and Greenhouse both keep the
real `<input type=file>` visually hidden behind a styled button (`locate_dom`
documents the same trade for `_is_hidden`), and Playwright's `set_input_files`
does not require visibility. Gating the attach on `is_visible()` would make
résumé upload impossible on two of the three boards.
(`test_a_hidden_file_input_is_still_attached_to`,
`test_a_hidden_text_input_is_not_filled`.)

**8. The résumé is attached by the agent, and it goes LAST.** (Kayla's ruling,
2026-08-01.) Greenhouse and Lever frequently run a parse-and-prefill on upload
that overwrites fields already filled, so attaching last means the agent's
values win. Four constraints on it:

  * it is an **explicit executor path** driven by a résumé path the caller
    passes in, never a resolver answer — `resolver.py` keeps classifying
    `file_upload` as blocking and stays pure;
  * **label-matched**: only a file input whose label or group heading reads as a
    résumé/CV is a candidate, and a label naming a *different* document as well
    ("Cover letter or resume") disqualifies itself;
  * **refuse rather than guess**: zero candidates or more than one, and nothing
    is attached — a résumé in the transcript slot is worse than an empty slot;
  * **verified by filename read-back**.

Attaching a file is not submitting. (`test_the_resume_is_attached_last`,
`test_an_ambiguous_file_input_gets_nothing_attached`.)

**9. Filename read-back does not use `input_value()` alone.** Measured on real
headed Chromium against a `file://` fixture on macOS: after
`set_input_files("/tmp/…/fake-resume.pdf")`, `input_value()` returns
``'C:\\fakepath\\fake-resume.pdf'`` — the spec-mandated fake path, Windows
separator and all, on every platform. So the primary read-back is
`el.files[0].name` via `evaluate`, and the `input_value()` route is a fallback
that strips both separators. A naive `input_value() == path` comparison would
have reported every successful attach as failed.
(`test_the_fakepath_form_of_a_filename_read_back_is_accepted`.)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from agents.job_applier.drafting import is_marked
from agents.job_applier.locate_dom import (
    Control,
    find_by_key,
    find_control,
    find_group_options,
    normalize_label,
)
from agents.job_applier.resolver import BLOCKING_KINDS, Answer
from agents.job_applier.schema_greenhouse import Question

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Read-back matched what we wrote.
FILLED = "filled"
#: Nothing is in the field — either we never wrote (refused / no value) or the
#: write was silently swallowed and the retry was too.
BLANK = "blank"
#: The field holds something, but not what we wrote. It reformatted, truncated
#: or normalized the value. Not a failure, but the human must look at it.
CHANGED = "changed"
#: The résumé file landed in a file input and the filename read back correctly.
ATTACHED = "attached"

STATUSES: tuple[str, str, str, str] = (FILLED, BLANK, CHANGED, ATTACHED)

#: The key on the résumé attach outcome. It is deliberately NOT a form
#: question's key: the attach is an explicit executor path, not an answer, so
#: there is no `Question` for it. Exported because the handoff has to recognise
#: that outcome to map it back to the question whose answer it superseded — and
#: a second hardcoded copy of the literal in another module is exactly the kind
#: of duplication that drifts.
RESUME_KEY = "__resume__"

#: Two attempts, never more. See decision 3.
MAX_ATTEMPTS = 2

#: Per-action Playwright timeout. Short on purpose: a field that is not
#: actionable within this is one the human fills in themselves, and the default
#: 30s × N fields turns a stuck form into a hung agent.
DEFAULT_TIMEOUT_MS = 5_000

#: Only these two answer sources are ever typed. `blank` is the resolver saying
#: "I refuse"; anything else is a source this module has never seen and does not
#: understand, which is not a reason to type it.
_TYPEABLE_SOURCES = frozenset({"profile", "drafted"})


@dataclass(frozen=True)
class FillOutcome:
    """What happened to one field. One per answer, plus one for the résumé.

    `value` is what the page ACTUALLY holds afterwards (the read-back), not what
    we intended to write — `intended` is that. When they differ the status says
    so. Task 7's handoff renders these directly, which is why the note is
    written for a human rather than for a log.
    """

    key: str
    label: str
    status: str
    value: str = ""
    intended: str = ""
    source: str = ""
    kind: str = ""
    note: str = ""
    strategy: str = ""
    attempts: int = 0
    drafted: bool = False


@dataclass
class FillReport:
    """Every outcome, in the order the executor produced them.

    Ordering is load-bearing, not cosmetic: the résumé attach is last, so that an
    ATS parse-and-prefill triggered by the upload cannot clobber values the agent
    already typed. `resume` is the same object as `outcomes[-1]` when an attach
    was attempted, exposed by name so the handoff does not have to index.
    """

    outcomes: list[FillOutcome] = field(default_factory=list)
    resume: FillOutcome | None = None
    #: Question keys whose answer was dropped because the résumé attach path
    #: speaks for that field instead. Recorded rather than silently discarded so
    #: a caller can tell "we chose not to report this" from "we lost it".
    superseded: list[str] = field(default_factory=list)

    def by_status(self, status: str) -> list[FillOutcome]:
        return [o for o in self.outcomes if o.status == status]

    @property
    def needs_review(self) -> list[FillOutcome]:
        """Everything the human must personally look at before submitting:
        anything left blank, anything the page altered, and every AI draft."""
        return [
            o for o in self.outcomes
            if o.status in (BLANK, CHANGED) or o.drafted
        ]


# ---------------------------------------------------------------------------
# THE ONE RULE, enforced at runtime as well as by the source scan
# ---------------------------------------------------------------------------
# `locate_dom` never discovers `type=submit`, `type=button` or `<button>` at
# all, so a submit control should be unreachable from here. This is the second
# lock on the same door: if a board ever ships a `type=text` input whose label
# is "Submit application", or a future refactor loosens discovery, the executor
# still refuses to touch it. Cheap, and the failure it prevents is the only
# unrecoverable one in Phase B.
# MEASURED against the three captured fixtures before choosing the boundaries:
# ZERO discovered controls on Lever, Ashby or Greenhouse match this vocabulary
# in their label, group label, name or id. Every real occurrence
# (`id="btn-submit"`, `id="hcaptchaSubmitBtn"`, `class="…template-btn-submit"`,
# `class="application--submit"`) is on a `<button>` or a `<div>`, neither of
# which `locate_dom` ever discovers as fillable. So this pattern currently costs
# nothing in false positives on real forms, and the boundary question is about
# what it would do to a form we have not seen.
#
# `(?![a-z])` on the right, and nothing on the left. Checked term by term:
#   * "submitter_name" / "submittal"  -> NOT matched (a lowercase letter
#     follows), which is the false positive the Task 4 review would have
#     flagged: a field asking for the submitter's name is not a submit button.
#   * "submit-application", "submit_form", "Submit Application" -> matched.
#   * "resubmit" -> matched, deliberately: no left boundary, because a
#     "resubmit" control still sends the application.
# The trade-off taken knowingly: "submitApplication" in camelCase is NOT matched
# under `re.IGNORECASE` (which makes `[a-z]` match `A` too). Accepting that miss
# is safe because this regex is the THIRD lock, behind `locate_dom` never
# discovering a button and the source scan never letting one be addressed.
# Widening it to catch camelCase would re-admit "submitter", which is a live
# field name and a real form.
_SUBMITISH_RE = re.compile(
    r"submit(?![a-z])|apply\s*now|send\s+(?:my\s+)?application",
    re.IGNORECASE,
)


def _is_submitish(*texts: str) -> bool:
    """True if any of `texts` reads like a control that sends the application."""
    return any(_SUBMITISH_RE.search(t or "") for t in texts)


def _refuse_submitish(control: Control) -> bool:
    return _is_submitish(
        control.label, control.group_label, control.name,
        control.element_id, control.selector or "",
    )


# ---------------------------------------------------------------------------
# Which answers may be typed at all
# ---------------------------------------------------------------------------


def is_typeable(answer: Answer) -> bool:
    """True if this answer may be written to the page. PURE — no page, no I/O.

    Both gates are here rather than inline so the rule can be tested on its own
    and so there is exactly one place that decides it. Decision 4.
    """
    if answer.kind in BLOCKING_KINDS:
        return False
    if answer.source not in _TYPEABLE_SOURCES:
        return False
    return bool((answer.value or "").strip())


def _refusal_outcome(answer: Answer) -> FillOutcome:
    """The outcome for an answer this module will not type, and why."""
    if answer.kind in BLOCKING_KINDS:
        if (answer.value or "").strip():
            # The resolver's own note is APPENDED, not discarded. It carries
            # things this sentence cannot know — most importantly that the
            # profile field was chosen from the POSTING's country because the
            # question named none, which is an inference the user has to be able
            # to check. Dropping it left a bare "your profile implies Yes" on
            # exactly the two questions where the agent had guessed which
            # country's field to read.
            note = (
                f"left for you deliberately. Your profile implies “{answer.value}”, "
                f"but this question decides whether the application is considered "
                f"at all, so the agent does not answer it — choose it yourself."
            )
            if (answer.note or "").strip():
                note = f"{note} {answer.note.strip()}"
        else:
            note = answer.note or "left for you — the agent does not answer this kind."
        return FillOutcome(
            key=answer.question.key, label=answer.question.label, status=BLANK,
            intended=answer.value, source=answer.source, kind=answer.kind, note=note,
        )
    return FillOutcome(
        key=answer.question.key, label=answer.question.label, status=BLANK,
        intended="", source=answer.source, kind=answer.kind,
        note=answer.note or "nothing was available to fill this with.",
    )


# ---------------------------------------------------------------------------
# Locating one question's control(s)
# ---------------------------------------------------------------------------


def _control_for(controls: list[Control], question: Question) -> Control | None:
    """The single control this question addresses, or `None`.

    Label first (decision 1: the FULL label, from the `Question` object, which
    hits `find_control`'s exact tier), then the Greenhouse identifier fallback.
    Never a literal string, and never a positional guess.
    """
    control = find_control(controls, question.label)
    if control is not None:
        return control
    return find_by_key(controls, question.key)


def _single_locator(page: Any, control: Control, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Any | None:
    """A Playwright locator for `control`, or `None`.

    Three refusals, in order:

      * no unique selector — `locate_dom` already decided the control cannot be
        safely addressed;
      * **submit-shaped** — the runtime half of THE ONE RULE lives HERE rather
        than at each call site, so it is impossible to obtain a locator for such
        a control at all. Every write in this module goes through this function,
        so there is no path that forgets to ask;
      * the selector resolves to anything other than exactly one live element —
        two hits is a miss, never "take the first" (the rule
        `PageLocator._single` applies).

    Never raises: `page.locator()` itself is inside the `try`, because a caller
    can hand us any object at all and `fill_one`/`attach_resume` promise not to
    raise.
    """
    if not control.selector:
        return None
    if _refuse_submitish(control):
        return None
    try:
        locator = page.locator(control.selector)
        if locator.count() != 1:
            return None
    except Exception:
        return None
    return locator


# ---------------------------------------------------------------------------
# Visibility — and the file-input exemption (decision 7)
# ---------------------------------------------------------------------------


def _needs_visibility_check(control: Control) -> bool:
    """False for file inputs, True for everything else.

    The exemption is the whole point of this function existing rather than the
    check being written inline: Ashby and Greenhouse both hide their real
    `<input type=file>` behind a styled button, and `set_input_files` does not
    need the element to be visible. Gating on visibility would make résumé
    upload impossible on two of three boards.
    """
    return control.kind != "file"


def _is_visible(locator: Any, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
    try:
        return bool(locator.is_visible(timeout=timeout_ms))
    except Exception:
        # A page that cannot answer the question is not evidence of visibility.
        return False


def _gate(control: Control, locator: Any, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    """`""` if `control` may be written to, else the reason it may not.

    BOTH write paths — the per-answer one and the résumé attach — go through
    this single gate, so the file-input exemption is structural rather than "we
    happened not to write the check in the other branch". A future edit that
    adds a visibility check to the attach path has to come through here and will
    hit `_needs_visibility_check` returning False.

    NOTE on radios, measured against the three captured fixtures: Lever styles
    its radio/checkbox inputs `appearance:none; width:17-20px; height:17-20px`,
    so they have a real box and pass `is_visible()`; Greenhouse's single
    checkbox has no hiding rule in its 16 KB of inline CSS; **Ashby's carry a
    build-hashed class whose rules live in an external CDN stylesheet the
    fixture does not contain, so it cannot be determined from what we have.**
    If Ashby does hide them, this gate reports blank with a reason — but so
    would Playwright: `check()` performs its own actionability wait and cannot
    tick a hidden element either. The gate turns a 5-second timeout into an
    instant, explained refusal; it does not make anything unfillable that would
    otherwise have been filled. Ticking a genuinely hidden radio would need
    `dispatch_event`/`evaluate`, both of which THE ONE RULE guard forbids.
    """
    if _needs_visibility_check(control) and not _is_visible(locator, timeout_ms):
        return (
            "this field is not visible on the page, so nothing was written to it "
            "— fill it in yourself."
        )
    return ""


# ---------------------------------------------------------------------------
# Read-back
# ---------------------------------------------------------------------------


# EVERY read-back passes an explicit timeout. Playwright's default is 30 s, and
# a read is exactly as capable of hanging as a write: `input_value()`,
# `evaluate()` and `is_checked()` all wait for the element to be attached, so a
# detached or re-rendering node costs the full default for ONE field. Bounding
# the writes alone and leaving the reads at 30 s would have defeated the whole
# point of `DEFAULT_TIMEOUT_MS` — and the hung-agent scenario that constant
# exists to prevent is caused by whichever call waits longest, not by whichever
# one writes.
#
# `_read_selected_label` can spend up to 2x on a select (evaluate, then the
# `input_value()` fallback). That is the accepted worst case: the fallback only
# runs when the primary read already failed, and 2x a bounded number is still
# bounded.


def _read_text(locator: Any, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    try:
        return str(locator.input_value(timeout=timeout_ms) or "")
    except Exception:
        return ""


def _read_selected_label(locator: Any, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    """The selected `<option>`'s visible text.

    `input_value()` on a `<select>` returns the option's `value` ATTRIBUTE,
    which on real boards is routinely a numeric id while the answer we were
    given is the option's text. Comparing those would report every successful
    selection as failed, so the label is read directly and `input_value()` is
    only the fallback.
    """
    try:
        text = locator.evaluate(
            "el => el.selectedOptions && el.selectedOptions.length"
            " ? el.selectedOptions[0].label : ''",
            timeout=timeout_ms,
        )
        if text:
            return str(text)
    except Exception:
        pass
    return _read_text(locator, timeout_ms)


def _same(intended: str, actual: str) -> bool:
    return (intended or "").strip() == (actual or "").strip()


# ---------------------------------------------------------------------------
# Writers — the only four page mutations in this package
# ---------------------------------------------------------------------------


# Every character Playwright's driver resolves to a KEY PRESS rather than to
# `insertText`. This is not a guess and not a denylist of characters that looked
# dangerous: the driver holds exactly one character→key alias map
# (`playwright/driver/package/lib/coreBundle.js`, `aliases`), and its only
# character entry is
#
#     ["Enter", ["\n", "\r"]]
#
# so this set is complete by construction. Everything else — including U+2028
# LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR, which are not on the US layout
# — is inserted as text and presses no key.
#
# The first version of this rule checked "\n" alone. A value carrying a
# classic-Mac or stray CR would then have been typed character by character into
# a single-line input, pressing Enter and triggering implicit submission, with
# the source scan green throughout. `\r\n` was always caught (it contains "\n");
# a lone `\r` was not.
_ENTER_CHARS = frozenset({"\n", "\r"})


def _presses_enter(value: str) -> bool:
    """True if typing `value` character by character would press Enter."""
    return any(ch in _ENTER_CHARS for ch in (value or ""))


def _may_type_character_by_character(control: Control, value: str) -> bool:
    """Whether the attempt-2 typing strategy is safe for this control.

    An Enter keypress in a text input inside a `<form>` triggers HTML's
    **implicit submission** — which would submit the application without
    anything in this module ever calling a click. That is THE ONE RULE broken by
    a keystroke, so the typing retry is refused outright for a value containing
    any `_ENTER_CHARS` character in anything but a `<textarea>` (where Enter
    only inserts a newline and submits nothing).

    `fill()` — attempt 1 — is unaffected: it sets the value directly and
    dispatches no key events at all, so multi-line values are filled normally.
    (`test_a_multiline_value_is_never_typed_into_a_single_line_input`,
    `test_every_enter_producing_character_is_refused_not_just_newline`.)
    """
    return not _presses_enter(value) or control.tag == "textarea"


def _typing_timeout(value: str, timeout_ms: int) -> int:
    """Per-character typing of a long drafted answer legitimately takes longer
    than a one-shot `fill()`, so the action timeout is scaled by length rather
    than left at the flat default (which a 1,500-character draft would blow
    through, turning a working retry into a spurious failure)."""
    return max(timeout_ms, 1_000 + len(value or "") * 20)


def _write_text(
    control: Control, locator: Any, value: str, attempt: int, timeout_ms: int
) -> str:
    """Attempt `attempt` (1-based) at getting `value` into a text control.

    Attempt 1 sets the value outright. Attempt 2 clears it and types it
    character by character, which is a genuinely different mechanism — it emits
    the key events a controlled React component listens for, where `fill()`
    emits one synthetic `input`. Decision 3.
    """
    if attempt == 1:
        locator.fill(value, timeout=timeout_ms)
        return "fill"
    locator.fill("", timeout=timeout_ms)
    locator.press_sequentially(
        value, delay=5, timeout=_typing_timeout(value, timeout_ms)
    )
    return "type"


def _fill_text(
    control: Control, locator: Any, answer: Answer, timeout_ms: int
) -> FillOutcome:
    """Get `answer.value` into a text control, or say honestly why not.

    The PRE-WRITE value is captured first, and it is what makes the four-way
    outcome split correct rather than merely plausible. "Reads back non-empty
    and different from what we wrote" conflates two opposite situations:

      * the field REFORMATTED our value (a phone mask rewriting `5550100` as
        `(555) 0100`) — it accepted the write, and retrying would type into a
        field that already holds something;
      * the field REVERTED to what it held before — a React-controlled input
        that re-rendered from its own state and threw our write away. That is a
        swallowed write, and it is precisely what the retry exists for.

    Without `before`, the second case was reported `changed` with the note "the
    field accepted the value … it reformatted or truncated it" and **skipped the
    retry entirely**. Browser autofill, ATS session-restore and
    apply-with-LinkedIn prefill all make a pre-populated field common, so this
    was not a corner case: it left a stale, wrong value in a real application
    and described it to the user as a reformat.
    (`test_a_reverted_write_on_a_prepopulated_field_is_retried_not_called_changed`.)
    """
    value = answer.value
    before = _read_text(locator, timeout_ms)
    last_error = ""
    strategy = ""
    actual = ""
    made = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1 and not _may_type_character_by_character(control, value):
            if last_error:
                # A real error is a more useful explanation than "we declined to
                # retry"; fall through to the error note below.
                break
            return _outcome(
                answer, BLANK, "", strategy, made,
                note=(
                    "the field would not accept its value, and it spans several "
                    "lines, so the agent did not retry by typing it — an Enter "
                    "keystroke in a single-line field can submit the form. Paste "
                    "it in yourself."
                ),
            )
        made = attempt
        try:
            strategy = _write_text(control, locator, value, attempt, timeout_ms)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        actual = _read_text(locator, timeout_ms)
        if _same(value, actual):
            return _outcome(answer, FILLED, actual, strategy, attempt,
                            note=_filled_note(answer))
        if actual.strip() and not _same(actual, before):
            # The field took it and changed it. Retrying would type twice.
            return _outcome(
                answer, CHANGED, actual, strategy, attempt,
                note=(
                    f"the field accepted the value but now reads “{actual}” instead "
                    f"of “{value}” — it reformatted or truncated it. Check it."
                ),
            )
        # Empty, or reverted to whatever was already there: a swallowed write.
        # Both fall through and retry.
    if _same(actual, before) and (before or "").strip():
        note = (
            f"the field rejected the value and snapped back to what it already "
            f"held, “{before}”, after {made} attempts — that is NOT your value. "
            f"Replace it yourself with: “{value}”."
        )
        return _outcome(answer, BLANK, before, strategy, made, note=note)
    note = (
        f"the field would not accept its value: it was still empty after "
        f"{made} attempts (set, then typed character by character). "
        f"Type it in yourself: “{value}”."
    )
    if last_error:
        note = (
            f"the field could not be written to ({last_error}). Type it in "
            f"yourself: “{value}”."
        )
    return _outcome(answer, BLANK, "", strategy, made, note=note)


def _fill_select(
    control: Control, locator: Any, answer: Answer, timeout_ms: int
) -> FillOutcome:
    """A real `<select>`. No retry — see decision 3."""
    try:
        locator.select_option(label=answer.value, timeout=timeout_ms)
    except Exception as exc:
        return _outcome(
            answer, BLANK, "", "select_option", 1,
            note=(
                f"“{answer.value}” could not be selected ({type(exc).__name__}) — "
                f"choose it yourself."
            ),
        )
    actual = _read_selected_label(locator, timeout_ms)
    if _same(answer.value, actual):
        return _outcome(answer, FILLED, actual, "select_option", 1,
                        note=_filled_note(answer))
    if actual.strip():
        return _outcome(
            answer, CHANGED, actual, "select_option", 1,
            note=f"the dropdown now reads “{actual}”, not “{answer.value}” — check it.",
        )
    return _outcome(
        answer, BLANK, "", "select_option", 1,
        note=f"the dropdown did not take “{answer.value}” — choose it yourself.",
    )


def _fill_choice_group(
    controls: list[Control], answer: Answer, page: Any, timeout_ms: int
) -> FillOutcome:
    """A radio/checkbox group: pick the member option matching the answer.

    There is no single element for a group, so `find_control` correctly returns
    `None` and this path takes over (decision 6). Matching is on the normalized
    option label — the resolver's option gate already guaranteed the value is one
    of the question's own options verbatim, so this is a lookup, not a fuzzy
    match.
    """
    options = find_group_options(controls, answer.question.label)
    if not options:
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                "the options for this question could not be located on the page — "
                f"choose “{answer.value}” yourself."
            ),
        )
    wanted = normalize_label(answer.value)
    matches = [c for c in options if normalize_label(c.label) == wanted]
    if len(matches) != 1:
        which = "no option" if not matches else f"{len(matches)} options"
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                f"{which} on this question matches “{answer.value}”, so nothing was "
                f"ticked — choose it yourself."
            ),
        )
    target = matches[0]
    if _refuse_submitish(target):
        return _outcome(answer, BLANK, "", "", 0, note=_SUBMIT_REFUSAL_NOTE)
    # `check()` is the one write in this module that dispatches a click, so it
    # is aimed ONLY at an element that is definitionally not a button. Without
    # this, a future change to how a group's members are discovered could point
    # it at a `<button role="radio">` — which a click-scanning source guard
    # would not catch, because the click is inside Playwright.
    if target.tag != "input" or target.input_type not in ("radio", "checkbox"):
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                f"“{target.label}” is not a radio or checkbox input, so the agent "
                f"did not tick it — choose it yourself."
            ),
        )
    locator = _single_locator(page, target, timeout_ms)
    if locator is None:
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                f"“{target.label}” has nothing unique to address it by on the page — "
                f"tick it yourself."
            ),
        )
    refusal = _gate(target, locator, timeout_ms)
    if refusal:
        return _outcome(answer, BLANK, "", "", 0, note=refusal)
    try:
        locator.check(timeout=timeout_ms)
    except Exception as exc:
        return _outcome(
            answer, BLANK, "", "check", 1,
            note=f"“{target.label}” could not be ticked ({type(exc).__name__}).",
        )
    try:
        checked = bool(locator.is_checked(timeout=timeout_ms))
    except Exception:
        checked = False
    if checked:
        return _outcome(answer, FILLED, target.label, "check", 1,
                        note=_filled_note(answer))
    return _outcome(
        answer, BLANK, "", "check", 1,
        note=f"“{target.label}” did not stay ticked — tick it yourself.",
    )


_SUBMIT_REFUSAL_NOTE = (
    "this control reads like a submit control, and the agent never acts on one. "
    "Nothing was done to it."
)


def _filled_note(answer: Answer) -> str:
    if is_marked(answer.value):
        return (
            "AI-drafted and typed in WITH its review marker still attached — read "
            "it, fix anything that is not true of you, and delete the marker line "
            "before you submit."
        )
    return "filled from your saved profile and verified on the page."


def _outcome(
    answer: Answer, status: str, actual: str, strategy: str, attempts: int, *, note: str
) -> FillOutcome:
    return FillOutcome(
        key=answer.question.key,
        label=answer.question.label,
        status=status,
        value=actual,
        intended=answer.value,
        source=answer.source,
        kind=answer.kind,
        note=note,
        strategy=strategy,
        attempts=attempts,
        drafted=is_marked(answer.value),
    )


# ---------------------------------------------------------------------------
# One answer
# ---------------------------------------------------------------------------

# Kinds handled by the choice-group path rather than by a single element.
_CHOICE_QUESTION_KINDS = frozenset({"checkbox"})


def fill_one(
    page: Any,
    controls: list[Control],
    answer: Answer,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> FillOutcome:
    """Write one answer and verify it. Never raises.

    `page` is a Playwright `Page` (or anything with `.locator()`); `controls` is
    a snapshot from `PageLocator.controls`, passed in rather than re-read so that
    every field in one pass acts on ONE DOM snapshot — the same correctness
    property `PageLocator` documents.
    """
    if not is_typeable(answer):
        return _refusal_outcome(answer)

    control = _control_for(controls, answer.question)

    # A group has no single element; `find_control` returning None for a group
    # heading is correct, not a failure. Decision 6.
    if control is None or answer.question.kind in _CHOICE_QUESTION_KINDS:
        if find_group_options(controls, answer.question.label):
            return _fill_choice_group(controls, answer, page, timeout_ms)
    if control is None:
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                "this field could not be located unambiguously on the page, so "
                f"nothing was typed — fill it in yourself: “{answer.value}”."
            ),
        )

    if _refuse_submitish(control):
        return _outcome(answer, BLANK, "", "", 0, note=_SUBMIT_REFUSAL_NOTE)

    if control.kind == "file":
        # Reachable only if a board labels a file input as something the
        # resolver classified non-blocking. The résumé path is the ONLY way this
        # module attaches anything.
        return _outcome(
            answer, BLANK, "", "", 0,
            note="this is a file field; the agent only ever attaches your résumé.",
        )

    locator = _single_locator(page, control, timeout_ms)
    if locator is None:
        return _outcome(
            answer, BLANK, "", "", 0,
            note=(
                "this field has nothing unique to address it by on the page, so "
                f"nothing was typed — fill it in yourself: “{answer.value}”."
            ),
        )

    refusal = _gate(control, locator, timeout_ms)
    if refusal:
        return _outcome(answer, BLANK, "", "", 0, note=refusal)

    if control.tag == "select":
        return _fill_select(control, locator, answer, timeout_ms)
    if control.tag == "input" and control.input_type in ("radio", "checkbox"):
        return _fill_choice_group(controls, answer, page, timeout_ms)
    return _fill_text(control, locator, answer, timeout_ms)


# ---------------------------------------------------------------------------
# The résumé attach — last, label-matched, verified (decision 8)
# ---------------------------------------------------------------------------

# A file input this file may attach the résumé to. Matched on the NORMALIZED
# label (`normalize_label` folds accents and punctuation), so "Résumé", "Resume"
# and "Resume/CV" all reduce to the same tokens.
_RESUME_RE = re.compile(r"\b(?:resume|cv|curriculum\s+vitae)\b")

# Other document types a form asks for. A label naming one of these — even if it
# ALSO says "resume" ("Cover letter or resume") — is not an unambiguous résumé
# slot, and the agent refuses rather than guessing.
_OTHER_DOCUMENT_RE = re.compile(
    r"\b(?:cover\s+letter|transcript|portfolio|writing\s+sample|reference"
    r"|references|certificate|certification|diploma|passport|photo|headshot"
    r"|offer\s+letter|pay\s+stub)\b"
)

RESUME_MISSING_NOTE = (
    "no résumé file was given to the agent, so nothing was attached — attach it "
    "yourself in the open browser window."
)


def _resume_texts(control: Control) -> str:
    return normalize_label(f"{control.group_label} {control.label}")


def find_resume_input(controls: list[Control]) -> tuple[Control | None, str]:
    """The one file input that is unambiguously the résumé slot, and why not.

    Returns `(control, "")` on success or `(None, reason)` on refusal. PURE.

    Refusing is the designed outcome, not a fallback: a form can carry a résumé
    input, a cover-letter input, a transcript input and a portfolio input, and
    putting the résumé in the transcript slot is a worse outcome for the user
    than an empty slot they fill in themselves.
    """
    files = [c for c in controls if c.kind == "file"]
    if not files:
        return None, "this form has no file upload field the agent could find."
    candidates = [
        c for c in files
        if _RESUME_RE.search(_resume_texts(c))
        and not _OTHER_DOCUMENT_RE.search(_resume_texts(c))
    ]
    if not candidates:
        labels = ", ".join(f"“{c.group_label or c.label}”" for c in files) or "—"
        return None, (
            f"none of this form's file fields ({labels}) is labelled as a résumé "
            f"or CV, so nothing was attached — attach it yourself."
        )
    if len(candidates) > 1:
        labels = ", ".join(f"“{c.group_label or c.label}”" for c in candidates)
        return None, (
            f"{len(candidates)} file fields ({labels}) could each be the résumé "
            f"slot, and the agent will not guess between them — attach it yourself."
        )
    return candidates[0], ""


def _read_filename(locator: Any, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    """The filename the file input now holds.

    `input_value()` alone is NOT enough: measured on headed Chromium against a
    local `file://` page, it returns ``C:\\fakepath\\<name>`` — the HTML spec's
    deliberate fake path, with a Windows separator, on macOS. So the real
    `File.name` is read first and the fake path is only parsed as a fallback.
    Decision 9.
    """
    try:
        name = locator.evaluate(
            "el => el.files && el.files.length ? el.files[0].name : ''",
            timeout=timeout_ms,
        )
        if name:
            return str(name)
    except Exception:
        pass
    raw = _read_text(locator, timeout_ms)
    return raw.replace("\\", "/").rsplit("/", 1)[-1] if raw else ""


def attach_resume(
    page: Any,
    controls: list[Control],
    resume_path: str | os.PathLike[str] | None,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> FillOutcome:
    """Attach `resume_path` to the form's résumé input. Never raises.

    Called LAST by `fill_form`, deliberately — see decision 8. Attaching a file
    is not submitting.
    """
    label = "Résumé"
    if not resume_path:
        return FillOutcome(key=RESUME_KEY, label=label, status=BLANK,
                           kind="file_upload", source="file", note=RESUME_MISSING_NOTE)
    # `os.fspath` raises TypeError on a non-path, and this function promises not
    # to raise, so even the argument handling is guarded.
    try:
        path = os.fspath(resume_path)
        filename = os.path.basename(path)
        exists = os.path.isfile(path)
    except Exception as exc:
        return FillOutcome(
            key=RESUME_KEY, label=label, status=BLANK, kind="file_upload",
            source="file",
            note=f"the résumé path could not be read ({type(exc).__name__}) — "
                 f"attach it yourself.",
        )
    # `intended` and every note carry the BASENAME, not the absolute path. Task 7
    # renders these to the user, and a handoff has no business printing
    # `/Users/<name>/…` back at them.
    if not exists:
        return FillOutcome(
            key=RESUME_KEY, label=label, status=BLANK, kind="file_upload",
            source="file", intended=filename,
            note=f"the résumé file “{filename}” was not found, so nothing was attached.",
        )

    control, reason = find_resume_input(controls)
    if control is None:
        return FillOutcome(key=RESUME_KEY, label=label, status=BLANK,
                           kind="file_upload", source="file", intended=filename, note=reason)

    label = control.group_label or control.label or label
    if _refuse_submitish(control):
        return FillOutcome(key=RESUME_KEY, label=label, status=BLANK,
                           kind="file_upload", source="file", intended=filename,
                           note=_SUBMIT_REFUSAL_NOTE)

    locator = _single_locator(page, control, timeout_ms)
    if locator is None:
        return FillOutcome(
            key=RESUME_KEY, label=label, status=BLANK, kind="file_upload",
            source="file", intended=filename,
            note=("the résumé field has nothing unique to address it by on the "
                  "page — attach it yourself."),
        )

    # Same gate as every other write — and it lets a file input through even when
    # `is_visible()` is False, because `_needs_visibility_check` is False for
    # `kind == "file"`. Ashby and Greenhouse hide the real input behind a styled
    # button, so this exemption is what makes résumé upload possible at all on
    # two of the three boards. Decision 7.
    refusal = _gate(control, locator, timeout_ms)
    if refusal:
        return FillOutcome(key=RESUME_KEY, label=label, status=BLANK,
                           kind="file_upload", source="file", intended=filename,
                           note=refusal)

    try:
        locator.set_input_files(path, timeout=timeout_ms)
    except Exception as exc:
        return FillOutcome(
            key=RESUME_KEY, label=label, status=BLANK, kind="file_upload",
            source="file", intended=filename,
            note=(f"the résumé could not be attached ({type(exc).__name__}: {exc}) — "
                  f"attach it yourself."),
        )

    landed = _read_filename(locator, timeout_ms)
    if landed == filename:
        return FillOutcome(
            key=RESUME_KEY, label=label, status=ATTACHED, value=landed,
            intended=filename, kind="file_upload", source="file", strategy="attach",
            attempts=1,
            note=(f"“{filename}” attached to “{label}” and confirmed, after every "
                  f"other field was filled."),
        )
    return FillOutcome(
        key=RESUME_KEY, label=label, status=BLANK, value=landed, intended=filename,
        kind="file_upload", source="file", strategy="attach", attempts=1,
        note=(
            f"the résumé did not land: the field reads “{landed or 'nothing'}” "
            f"instead of “{filename}” — attach it yourself."
        ),
    )


# ---------------------------------------------------------------------------
# The whole form
# ---------------------------------------------------------------------------


def _claimed_resume_control(
    controls: list[Control], resume_path: str | os.PathLike[str] | None
) -> Control | None:
    """The control `attach_resume` is going to claim, or `None`.

    Deliberately mirrors `attach_resume`'s OWN preconditions — a path, an
    existing file, and an unambiguous résumé slot — so the two cannot disagree
    about which field is the résumé. If any precondition fails, this returns
    `None` and the resolver's answer for that field survives, which is right:
    the agent is not going to attach anything, so "attach it yourself" is once
    again the true thing to say.
    """
    if not resume_path:
        return None
    try:
        if not os.path.isfile(os.fspath(resume_path)):
            return None
    except Exception:
        return None
    control, _reason = find_resume_input(controls)
    return control



def fill_form(
    page_locator: Any,
    answers: list[Answer],
    *,
    resume_path: str | os.PathLike[str] | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> FillReport:
    """Fill every answer, then attach the résumé LAST. Never raises, never
    clicks, never submits.

    `page_locator` is a `locate_dom.PageLocator`. Its `controls` snapshot is read
    ONCE and reused for the whole pass: re-reading mid-pass would mean acting on
    selectors from one DOM using questions from another, which is the bug
    `PageLocator` already documents for `questions()`.

    The résumé goes last because Greenhouse and Lever run a parse-and-prefill on
    upload that overwrites already-filled fields. That ordering is pinned by
    `test_the_resume_is_attached_last`, which asserts the attach is the final
    page mutation — not merely the final entry in the report.

    **The résumé field is reported exactly once.** The résumé input is itself a
    question, so the resolver emits a `file_upload` answer for it — and that
    answer's note says "attach it yourself". Left alone, the report contained
    BOTH that and the attach outcome, i.e. Task 7 would have told the user
    "Resume/CV — blank, attach it yourself" about a slot the agent had just
    successfully attached to. So the answer for whichever control the attach
    path CLAIMS is suppressed, and only the attach outcome speaks for it.
    Suppression is keyed on the control the attach claimed, not on the answer's
    kind, so a form's *other* file fields (cover letter, transcript) keep their
    resolver answers and are still reported as the human's to do.
    (`test_the_resume_field_is_reported_exactly_once`,
    `test_other_file_fields_keep_their_resolver_answer`.)

    `fill_one` and `attach_resume` never raise. `fill_form` itself can only fail
    where it reads the DOM snapshot (`page_locator.controls`, i.e. the caller's
    own `page.content()`); that is deliberately not swallowed, because a page
    that cannot be read is not a form that can be partially filled.
    """
    # `PageLocator` exposes locators for a *label* or a *key*, but a radio group's
    # members are addressed by their own per-option selectors, which it has no
    # accessor for. Reaching for its `_page` is the one private access in this
    # module; the clean fix is a public `page` property on `PageLocator`, and that
    # module is explicitly out of scope for this task. A bare `Page` is accepted
    # too, which is what the tests pass for the single-field helpers.
    page = getattr(page_locator, "_page", page_locator)
    controls = page_locator.controls

    # Decide WHICH control the attach will claim before the loop, so the loop can
    # suppress that control's resolver answer — but do not attach yet. The attach
    # itself still has to be the last mutation.
    claimed = _claimed_resume_control(controls, resume_path)

    report = FillReport()
    for answer in answers or []:
        if claimed is not None and _control_for(controls, answer.question) is claimed:
            report.superseded.append(answer.question.key)
            continue
        report.outcomes.append(fill_one(page, controls, answer, timeout_ms=timeout_ms))

    # LAST. Always — even when nothing above filled, so the handoff can always
    # say what happened to the résumé.
    report.resume = attach_resume(page, controls, resume_path, timeout_ms=timeout_ms)
    report.outcomes.append(report.resume)
    return report


# ---------------------------------------------------------------------------
# The graph node
# ---------------------------------------------------------------------------


def fill_node(state: dict) -> dict:
    """Task 8's adapter: read the graph state, call `fill_form`, write the report.

    `state` is an `agents.job_applier.state.ApplierState`, annotated as a plain
    dict on purpose — importing the TypedDict would add an in-package import to
    a module whose import list is itself asserted, to close the "a helper in
    another module clicks" hole. A TypedDict is a dict at runtime, so nothing is
    lost but the annotation.

    An empty `resume_path` becomes `None`, which is `fill_form`'s "no résumé was
    given" input — not a path to a file called "".

    This deliberately does NOT catch: `fill_form` swallows every per-field
    failure itself, so anything that escapes it is a page that cannot be read at
    all, and the graph's own guard turns that into a handoff explaining the stop
    (and closes the browser) rather than a half-report that looks complete.
    """
    return {
        "fill_report": fill_form(
            state.get("locator"),
            state.get("answers") or [],
            resume_path=(state.get("resume_path") or "").strip() or None,
        )
    }
