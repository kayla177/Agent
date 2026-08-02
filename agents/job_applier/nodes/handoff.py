"""Turn a fill pass into the one thing the human actually reads.

Everything upstream in Phase B is judged through this module. If it buries what
blocks submission, or claims a field was filled that wasn't, the feature is
worse than useless: it manufactures false confidence about a real job
application sent under the user's own name.

THE ONE RULE for all of Phase B — no code path may ever click a submit button.
This module writes no DOM code at all: it takes a `FillReport` and some
`Question` lists and returns text. It also never *says* an application was
submitted, on any path, which is the second half of the same rule and is pinned
by `test_no_rendered_report_on_any_path_claims_a_submission`.

Design decisions, and why
=========================

**1. Ordered by what stops the user submitting, not by what the agent did.**
Three action bands, then a fourth informational one, in this order:

  1. `BLOCKING`  — required and the field is still empty. The form will not
     submit. This is the only band that costs her the application if she
     misses it, so it leads.
  2. `REVIEW`    — has a value, or is empty but optional: AI drafts, values the
     page rewrote, optional blanks, questions with no readable label.
  3. `WITHHELD`  — voluntary EEO self-identification, deliberately untouched.
     Needs no action, but must be *said* (decision 3).
  4. `DONE`      — filled and verified, plus the attached résumé. Rendered LAST
     and compactly (one line, `label — value`) so she can spot-check it without
     it being the first thing she reads. `render_text(show_filled=False)`
     drops it entirely; Task 10 collapses it by `group == DONE`.

The header opens with a count — "7 of 29 fields need you" — because the first
thing she needs is the size of the job, not its first item.

**2. A `changed` field is NOT blocking.** A phone mask rewriting `5550100` as
`(555) 0100` genuinely accepted the value: the form will submit. It is wrong to
put it above a required field that is empty, and wrong to call it `blank`. It
is `REVIEW`. The same goes for a required field carrying an AI draft — a draft
is a value, so the form submits; it just may submit something untrue.

**3. The three reasons a field was left alone are never merged.** They need
three different actions from the user, so each item carries its own `reason`
and each renders its own tag:

  * `refused` — a `resolver.BLOCKING_KINDS` question (work authorization,
    sponsorship, citizenship, consent, file uploads). The agent will never
    answer these. Where the resolver derived a value from the profile, it is
    rendered as a SUGGESTION she confirms — explicitly "not entered" — never as
    an answer. (`test_a_refused_answer_shows_a_suggestion_she_confirms_not_an_answer`.)
  * `unreadable` — `PageLocator.unreadable()`: a question exists but no label
    could be read, so she has to find it on the page herself. Never dropped.
    (`test_unreadable_questions_are_never_silently_dropped`.)
  * `withheld_eeo` — `PageLocator.withheld_eeo()`: deliberately not touched.
    Said out loud, or she goes looking for a bug that isn't there.

**4. `superseded` outcomes are not rendered.** The résumé slot is itself a
question, so the resolver emits a `file_upload` answer for it AND the attach
path reports the same DOM field. Rendering both showed "Resume/CV — blank,
attach it yourself" for a slot that had just been successfully attached. The
suppressed keys arrive in `FillReport.superseded`; this module both skips them
and *uses* them — a superseded key is how the résumé outcome (whose own key is
a synthetic `__resume__`) finds its `Question`, and therefore its section and
its `required` flag. Without that, a failed attach on Lever's required
Resume/CV would have landed in `REVIEW` instead of `BLOCKING`.
(`test_a_superseded_question_is_reported_exactly_once`,
`test_a_failed_attach_on_a_required_resume_slot_is_blocking`.)

**5. `resolver.blocking()` is deliberately NOT the source of the blocking
band, and that is a correctness choice rather than an oversight.** It answers
"which answers must the human confirm?" over *intents*, before anything was
typed. This module knows what the page actually holds afterwards, and the two
disagree in a way that matters: a required field whose write the page swallowed
has a non-empty `Answer.value`, so `blocking()` omits it — while in fact the
form will not submit. So the band is computed from the outcome
(`status == BLANK and Question.required`), and `BLOCKING_KINDS` is used for the
orthogonal question of *why* a field was left alone.
(`test_the_blocking_band_is_what_the_page_holds_not_what_the_resolver_intended`.)

**6. Grouped by `Question.section`, but only where the board publishes one.**
MEASURED on the three captured fixtures: Lever populates all 29 questions
across 14 sections; Greenhouse (15 questions) and Ashby (16) are entirely
blank, because neither renders a heading element the DOM locator can attribute
a field to. Grouping naively would print "(no section)" above every single row
on two of the three boards — pure noise, and it makes the report look broken.
So the rule is per band: if no item in a band has a section, the band is a flat
list with no headings at all; if some do, the sectioned items are grouped in
first-appearance order and the rest fall under one "Other questions" heading at
the end. Section headings are also truncated — Lever's video-prompt heading is
a 400-character paragraph, which is a heading in name only.
(`test_boards_without_sections_get_no_section_headings_at_all`,
`test_lever_is_grouped_under_its_real_section_headings`.)

**7. Structured data AND rendered text.** Task 10 puts this in the web UI and
wants the structure, not a parsed string; the daily runs want the text. So
`build_report` returns a `HandoffReport` of `ReportItem`s and `render_text()` is
a pure function of it.

**8. Basenames, never absolute paths.** `fill.attach_resume` already carries the
basename in its own notes, but a Playwright exception message quotes the path it
was handed — so a failed attach surfaced `/Users/<name>/…` into the user's
report. Every free-text field this module renders is passed through
`_basenames()`. URL-safe by construction: the pattern refuses to start a match
after a word character, so `https://linkedin.com/in/name` is untouched while
`/Users/x/résumé.pdf` becomes `résumé.pdf`. (`test_absolute_paths_are_reduced_to_basenames`,
`test_urls_are_not_mistaken_for_paths`.)

**9. The AI-draft marker survives everything.** Drafted values are flagged by
`drafting.is_marked()` — never by re-hardcoding the literal — and the marker is
never stripped, including when a 1,500-character draft is excerpted for
display: `_excerpt` re-attaches it if truncation would have eaten it.
(`test_a_drafted_value_keeps_its_marker_even_when_excerpted`.)

**10. Nothing here raises.** This is the last thing that runs; a handoff that
throws leaves the user with a filled browser window and no idea what is in it.
Accessors on the page locator are called defensively, and `fill_report=None`
(the agent died before filling anything) renders a valid, honest report.
(`test_a_total_failure_still_renders_a_report`.)
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any, Iterable

from agents.job_applier.drafting import DRAFT_MARKER, is_marked
from agents.job_applier.nodes.fill import (
    ATTACHED,
    CHANGED,
    FILLED,
    RESUME_KEY,
    FillOutcome,
    FillReport,
)
from agents.job_applier.resolver import BLOCKING_KINDS
from agents.job_applier.schema_greenhouse import Question

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Required, and the page does not hold a value. The form will not submit.
BLOCKING = "blocking"
#: Has a value that may be wrong, or is empty but optional. Her eyes needed.
REVIEW = "review"
#: Deliberately not touched (voluntary EEO self-identification).
WITHHELD = "withheld"
#: Filled or attached and verified. Spot-check material only.
DONE = "done"

#: Render order. THIS TUPLE IS THE ORDERING — see decision 1.
GROUPS: tuple[str, str, str, str] = (BLOCKING, REVIEW, WITHHELD, DONE)

# Why an item is where it is. The first three are the three different reasons a
# field was left alone (decision 3) and are never collapsed into one another.
REASON_REFUSED = "refused"
REASON_UNREADABLE = "unreadable"
REASON_WITHHELD_EEO = "withheld_eeo"
REASON_EMPTY = "empty"
REASON_CHANGED = "changed"
REASON_DRAFTED = "drafted"
REASON_FILLED = "filled"
REASON_ATTACHED = "attached"

REASONS: tuple[str, ...] = (
    REASON_REFUSED, REASON_UNREADABLE, REASON_WITHHELD_EEO, REASON_EMPTY,
    REASON_CHANGED, REASON_DRAFTED, REASON_FILLED, REASON_ATTACHED,
)

#: The short tag printed after each label. Distinct per reason on purpose:
#: "find it on the page yourself" and "confirm this one yourself" are different
#: jobs, and one tag for both would hide that.
_REASON_TAG: dict[str, str] = {
    REASON_REFUSED: "yours to answer — the agent never answers this kind",
    REASON_UNREADABLE: "no readable label — find this one on the page yourself",
    REASON_WITHHELD_EEO: "not touched on purpose",
    REASON_EMPTY: "not filled",
    REASON_CHANGED: "the page rewrote your value",
    REASON_DRAFTED: "AI-DRAFTED — read every word",
    REASON_FILLED: "filled",
    REASON_ATTACHED: "attached",
}

_BULLET: dict[str, str] = {
    BLOCKING: "!", REVIEW: "?", WITHHELD: "·", DONE: "✓",
}

_BAND_HEADING: dict[str, str] = {
    BLOCKING: "REQUIRED AND STILL EMPTY — the form will not submit without these",
    REVIEW: "NEEDS YOUR REVIEW — the agent put something here, or could not",
    WITHHELD: "LEFT UNTOUCHED ON PURPOSE — voluntary self-identification",
    DONE: "FILLED AND VERIFIED — nothing to do; open it to spot-check",
}

#: The first line of every report, on every path. Deliberately a constant: the
#: statement that nothing was submitted is not something a caller may omit, and
#: a test asserts every render starts with it.
NOT_SUBMITTED_HEADLINE = (
    "NOTHING WAS SUBMITTED. The browser window is still open on this form, "
    "waiting for you."
)

#: The standing instruction, repeated at the end so it is the last thing read
#: as well as the first.
HANDOFF_INSTRUCTION = (
    "The agent does not press Submit and never will. Work through the list "
    "below in the open browser window, fix anything that is wrong, and press "
    "Submit yourself when you are happy with it."
)

_HEADING_FALLBACK = "Other questions"
_NO_LABEL = "(this question has no readable label on the page)"

_WIDTH = 78
_SECTION_CAP = 96
_VALUE_CAP = 220
_DONE_VALUE_CAP = 60
_NOTE_CAP = 480

_RULE = "─" * 70


# ---------------------------------------------------------------------------
# Absolute paths are never shown to the user (decision 8)
# ---------------------------------------------------------------------------
# Starts only where a path can start: at `/` or `~/`, NOT preceded by a word
# character, `:` or another `/`. That single lookbehind is what keeps URLs
# intact — in `https://linkedin.com/in/name` the `/in/name` run is preceded by
# `m`, and the `//` after the scheme is preceded by `:`. Two or more segments
# are required so a bare `/` or a lone `/tmp` is not rewritten.
_ABS_PATH_RE = re.compile(r"(?<![\w:/~.-])~?(?:/[^\s/\"'“”<>)\]]+){2,}")


def _basenames(text: str) -> str:
    """`/Users/kayla.li/docs/résumé.pdf` -> `résumé.pdf`, URLs untouched.

    A handoff has no business printing the user's home directory back at them,
    and the paths that leak in are not ones this module chose to print: they
    arrive inside a Playwright exception message quoted verbatim by
    `fill.attach_resume`.
    """
    return _ABS_PATH_RE.sub(lambda m: m.group(0).rsplit("/", 1)[-1], text or "")


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _excerpt(text: str, cap: int) -> str:
    """One-line, length-capped rendering that CANNOT lose the draft marker.

    Truncation is the interesting case: a 1,500-character drafted answer is
    excerpted for display, and if the marker were only surviving because it
    happens to sit at the front and fit inside `cap`, then any future change to
    `mark()`'s layout or to `cap` would silently un-flag an AI draft in the one
    place the human looks. So the property is restored explicitly instead of
    being inherited from the layout.
    """
    flat = _flat(text)
    out = flat if len(flat) <= cap else flat[:cap].rstrip() + " …"
    if is_marked(text) and not is_marked(out):
        out = f"{DRAFT_MARKER} {out}"
    return out


# ---------------------------------------------------------------------------
# The structure (decision 7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportItem:
    """One row of the handoff: one form field, and what the user must do about it.

    `group` says how urgent it is (decision 1); `reason` says why it is in that
    state (decision 3). Two axes rather than one, because "required and empty"
    and "the agent refuses to answer this kind" are both true of the same Lever
    work-authorization question and neither implies the other.

    `value` is what the page ACTUALLY holds — the read-back, never the intent.
    `suggestion` is a value the resolver derived but the agent refused to type;
    it is rendered as something to confirm, never as an answer.
    """

    key: str
    label: str
    group: str
    reason: str
    section: str = ""
    required: bool = False
    status: str = ""
    value: str = ""
    intended: str = ""
    suggestion: str = ""
    note: str = ""
    drafted: bool = False
    kind: str = ""


@dataclass(frozen=True)
class HandoffReport:
    """Everything the human is told, as data plus a `render_text()`.

    `submitted` is a literal `False` field rather than an absence, so a consumer
    that wants to assert the application was not sent has something to assert
    on. Nothing in this package can set it True; there is no code path that
    submits.
    """

    items: tuple[ReportItem, ...] = ()
    job_title: str = ""
    company: str = ""
    form_url: str = ""
    resume_filename: str = ""
    #: Said in the header instead of as a row, for the case where the attach
    #: outcome speaks for no reported field. See `build_report`.
    resume_note: str = ""
    #: Set when the agent stopped early. Rendered near the top, because a
    #: partial report that does not say it is partial is a lie by omission.
    error: str = ""
    submitted: bool = False

    def group(self, name: str) -> tuple[ReportItem, ...]:
        return tuple(i for i in self.items if i.group == name)

    @property
    def blocking(self) -> tuple[ReportItem, ...]:
        return self.group(BLOCKING)

    @property
    def needs_you(self) -> tuple[ReportItem, ...]:
        """Everything she has to act on. Withheld EEO is excluded: it needs a
        decision only if she wants to self-identify, and counting it would
        inflate the number the header leads with."""
        return tuple(i for i in self.items if i.group in (BLOCKING, REVIEW))

    @property
    def total(self) -> int:
        return len(self.items)

    def counts(self) -> dict[str, int]:
        return {g: len(self.group(g)) for g in GROUPS}

    # -- rendering ---------------------------------------------------------

    def summary_line(self) -> str:
        if not self.items:
            return (
                "No fields were reported for this form — the agent could not "
                "read anything on it, so all of it is yours."
            )
        needs = len(self.needs_you)
        line = (
            f"{needs} of {self.total} field{'' if self.total == 1 else 's'} "
            f"need{'s' if needs == 1 else ''} you."
        )
        blocking = len(self.blocking)
        if blocking:
            line += (
                f" {blocking} of those {'is' if blocking == 1 else 'are'} "
                f"required and still empty, so the form will not submit until "
                f"you deal with {'it' if blocking == 1 else 'them'}."
            )
        elif not needs:
            line += " Everything the agent touched read back correctly."
        return line

    def render_text(self, *, show_filled: bool = True) -> str:
        """The report as plain text. Never claims a submission (decision 1).

        `show_filled=False` drops the `DONE` band, which is the text-mode
        equivalent of leaving it collapsed; the band is last either way.
        """
        out: list[str] = [NOT_SUBMITTED_HEADLINE, ""]

        title = " — ".join(p for p in (self.job_title, self.company) if p)
        if title:
            out.append(title)
        if self.form_url:
            out.append(self.form_url)
        if title or self.form_url:
            out.append("")

        if self.error:
            out.append(
                _wrap(
                    f"THE AGENT STOPPED EARLY: {_basenames(_flat(self.error))} "
                    f"Everything below is only what it managed before that, so "
                    f"treat the form as unfilled until you have checked it.",
                    indent="", hang="",
                )
            )
            out.append("")

        out.append(_wrap(self.summary_line(), indent="", hang=""))
        if self.resume_note:
            out.append(_wrap(f"RÉSUMÉ: {self.resume_note}", indent="", hang=""))
        out.append("")
        out.append(_wrap(HANDOFF_INSTRUCTION, indent="", hang=""))

        for band in GROUPS:
            items = self.group(band)
            if not items:
                continue
            out.append("")
            out.append(_RULE)
            fold = "▸ " if band == DONE else ""
            out.append(f"{fold}{_BAND_HEADING[band]}  ({len(items)})")
            out.append(_RULE)
            if band == DONE and not show_filled:
                out.append("    (collapsed)")
                continue
            out.extend(_render_band(band, items))

        out.append("")
        out.append(_RULE)
        out.append(_wrap(NOT_SUBMITTED_HEADLINE, indent="", hang=""))
        return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


def _accessor(source: Any, name: str) -> list[Question]:
    """Call `source.name()` and return a question list, or `[]`.

    Defensive on purpose (decision 10): `PageLocator.unreadable()` re-parses a
    DOM snapshot, and a handoff that dies because the snapshot went strange
    leaves the user with a filled browser and no report at all.
    """
    if source is None:
        return []
    fn = getattr(source, name, None)
    if fn is None:
        return []
    try:
        return list(fn() or [])
    except Exception:
        return []


def _choices(question: Question) -> str:
    """" whose choices are “a”, “b”, “c”" — or "" when there are none.

    Only ever used for an unreadable question, where the choices may be the
    only thing on the page that identifies which field is meant.
    """
    options = [_flat(o) for o in (question.options or []) if _flat(o)]
    if not options:
        return ""
    shown = ", ".join(f"“{o}”" for o in options[:4])
    more = f" and {len(options) - 4} more" if len(options) > 4 else ""
    return f" whose choices are {shown}{more}"


def _reason_for(outcome: FillOutcome) -> str:
    if outcome.status == ATTACHED:
        return REASON_ATTACHED
    if outcome.status == FILLED:
        return REASON_DRAFTED if outcome.drafted else REASON_FILLED
    if outcome.status == CHANGED:
        return REASON_CHANGED
    # BLANK, or anything a future status adds: nothing is in the field.
    return REASON_REFUSED if outcome.kind in BLOCKING_KINDS else REASON_EMPTY


def _group_for(outcome: FillOutcome, required: bool) -> str:
    if outcome.status == ATTACHED:
        return DONE
    if outcome.status == FILLED:
        # A draft IS a value — the form will submit — so it never blocks. It
        # does need reading, which is what REVIEW means. Decision 2.
        return REVIEW if outcome.drafted else DONE
    if outcome.status == CHANGED:
        return REVIEW
    return BLOCKING if required else REVIEW


def _item_from_outcome(
    outcome: FillOutcome, *, section: str, required: bool
) -> ReportItem:
    reason = _reason_for(outcome)
    # A suggestion is ONLY a profile-derived value the executor refused to type
    # (`fill._refusal_outcome`). The résumé outcome also carries an `intended`
    # (the filename) and `file_upload` is in BLOCKING_KINDS, but "suggested:
    # résumé.pdf — confirm it yourself" is nonsense, and `source` is what tells
    # the two apart.
    suggestion = (
        outcome.intended.strip()
        if reason == REASON_REFUSED and outcome.source == "profile"
        else ""
    )
    return ReportItem(
        key=outcome.key,
        label=outcome.label,
        group=_group_for(outcome, required),
        reason=reason,
        section=section,
        required=required,
        status=outcome.status,
        value=_basenames(outcome.value),
        intended=_basenames(outcome.intended),
        suggestion=_basenames(suggestion),
        note=_basenames(outcome.note),
        drafted=outcome.drafted,
        kind=outcome.kind,
    )


def build_report(
    fill_report: FillReport | None,
    *,
    questions: Iterable[Question] | None = None,
    page_locator: Any | None = None,
    unreadable: Iterable[Question] | None = None,
    withheld_eeo: Iterable[Question] | None = None,
    job_title: str = "",
    company: str = "",
    form_url: str = "",
    error: str = "",
) -> HandoffReport:
    """Assemble the handoff. Never raises, never claims a submission.

    `page_locator` is a `locate_dom.PageLocator`; its `questions()`,
    `unreadable()` and `withheld_eeo()` are read from the ONE snapshot it
    already holds, so this costs no re-parse. Explicit `questions` /
    `unreadable` / `withheld_eeo` arguments win over it, which is how the tests
    build a report without a page at all.

    `fill_report=None` is a supported input, not an error: it is what the graph
    has when the browser died before anything was filled, and the user still
    needs to be told that nothing was submitted.
    """
    qs = list(questions) if questions is not None else _accessor(page_locator, "questions")
    unread = list(unreadable) if unreadable is not None else _accessor(page_locator, "unreadable")
    eeo = list(withheld_eeo) if withheld_eeo is not None else _accessor(page_locator, "withheld_eeo")

    by_key: dict[str, Question] = {q.key: q for q in qs}
    outcomes = list(fill_report.outcomes) if fill_report else []
    superseded = set(fill_report.superseded) if fill_report else set()
    resume_outcome = fill_report.resume if fill_report else None

    # The résumé outcome's own key is a synthetic `__resume__` that matches no
    # question. Its real question is the one whose answer was suppressed for it
    # — that is exactly what `superseded` records. Decision 4.
    resume_question: Question | None = None
    if len(superseded) == 1:
        resume_question = by_key.get(next(iter(superseded)))

    # The attach outcome is a ROW only when it speaks for a field on this form:
    # it attached something, or the resolver's answer for that slot was
    # superseded for it. When neither is true — most often because no résumé
    # file was given at all — it speaks for nothing, and rendering it anyway put
    # TWO résumé rows in front of the user: the form's own Resume/CV question
    # ("the agent attaches your résumé itself, last") and a ghost row ("no
    # résumé file was given"), which contradict each other. So that case becomes
    # one line in the header instead. MEASURED: on Lever with no résumé path,
    # this is the difference between 30 reported "fields" and the form's real 29.
    # (`test_a_missing_resume_is_said_once_in_the_header_not_as_a_ghost_field`.)
    resume_note = ""
    ghost_resume: FillOutcome | None = None
    if resume_outcome is not None and not superseded and resume_outcome.status != ATTACHED:
        resume_note = _basenames(resume_outcome.note)
        ghost_resume = resume_outcome

    items: list[ReportItem] = []
    for outcome in outcomes:
        if ghost_resume is not None and outcome is ghost_resume:
            continue
        # Belt and braces: `fill_form` never appends an outcome for a key it
        # superseded, and if that ever changes this module must still not print
        # the résumé slot twice.
        if outcome.key in superseded:
            continue
        question = by_key.get(outcome.key)
        if question is None and (outcome.key == RESUME_KEY or outcome is resume_outcome):
            question = resume_question
        items.append(_item_from_outcome(
            outcome,
            section=question.section if question else "",
            required=bool(question.required) if question else False,
        ))

    for question in unread:
        items.append(ReportItem(
            key=question.key,
            label="",
            group=BLOCKING if question.required else REVIEW,
            reason=REASON_UNREADABLE,
            section=question.section,
            required=bool(question.required),
            kind=question.kind,
            # The label is the ONE thing she would normally use to find the
            # field, and it is exactly what is missing — so everything else
            # that might locate it goes in instead.
            note=(
                "the agent could see a field here but could not read what it is "
                "asking, so it left the field completely alone. It is a "
                f"{question.kind or 'form'} field{_choices(question)}; find it "
                "on the page and answer it yourself."
            ),
        ))

    for question in eeo:
        items.append(ReportItem(
            key=question.key,
            label=question.label,
            group=WITHHELD,
            reason=REASON_WITHHELD_EEO,
            section=question.section,
            required=bool(question.required),
            kind=question.kind,
            note=(
                "a voluntary self-identification question. The agent never "
                "answers these and did not touch it — this is not a bug. Answer "
                "it or decline to, yourself."
            ),
        ))

    return HandoffReport(
        items=tuple(items),
        job_title=job_title,
        company=company,
        form_url=form_url,
        resume_filename=_basenames(resume_outcome.intended) if resume_outcome else "",
        resume_note=resume_note,
        error=error,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _wrap(text: str, *, indent: str, hang: str) -> str:
    return textwrap.fill(
        _flat(text), width=_WIDTH, initial_indent=indent, subsequent_indent=hang,
    )


def _short_section(section: str) -> str:
    """Lever's video-prompt "heading" is a 400-character paragraph, so a heading
    is not automatically short. Cut on a word boundary rather than mid-word."""
    flat = _flat(section)
    if len(flat) <= _SECTION_CAP:
        return flat
    cut = flat[:_SECTION_CAP]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:—-") + " …"


def _sectioned(items: tuple[ReportItem, ...]) -> list[tuple[str, list[ReportItem]]]:
    """`[(heading, items)]`, or `[("", items)]` when this band has no sections.

    Decision 6: two of the three real boards publish no section headings at all,
    and printing "(no section)" above every row of those is noise that makes the
    report look broken.
    """
    if not any(i.section for i in items):
        return [("", list(items))]
    order: list[str] = []
    buckets: dict[str, list[ReportItem]] = {}
    for item in items:
        head = _short_section(item.section) or _HEADING_FALLBACK
        if head not in buckets:
            buckets[head] = []
            order.append(head)
        buckets[head].append(item)
    # The catch-all goes last wherever it appeared.
    if _HEADING_FALLBACK in order:
        order = [h for h in order if h != _HEADING_FALLBACK] + [_HEADING_FALLBACK]
    return [(head, buckets[head]) for head in order]


def _render_done_item(item: ReportItem) -> str:
    """One compact line — this band exists to be skimmed, not read."""
    value = _excerpt(item.value or item.intended, _DONE_VALUE_CAP)
    suffix = " attached" if item.reason == REASON_ATTACHED else ""
    label = _flat(item.label) or _NO_LABEL
    return _wrap(
        f"{_BULLET[DONE]} {label} — “{value}”{suffix}", indent="    ", hang="      ",
    )


def _render_item(item: ReportItem) -> list[str]:
    marks = (["required"] if item.required else []) + [_REASON_TAG[item.reason]]
    label = _flat(item.label) or _NO_LABEL
    lines = [_wrap(
        f"{_BULLET[item.group]} {label}  [{' · '.join(marks)}]",
        indent="    ", hang="      ",
    )]
    if item.reason == REASON_CHANGED:
        lines.append(_wrap(
            f"the page now holds “{_excerpt(item.value, _VALUE_CAP)}” — "
            f"you asked it for “{_excerpt(item.intended, _VALUE_CAP)}”.",
            indent="        ", hang="        ",
        ))
    elif item.reason == REASON_DRAFTED:
        lines.append(_wrap(
            f"typed in: “{_excerpt(item.value, _VALUE_CAP)}”",
            indent="        ", hang="        ",
        ))
    if item.suggestion:
        lines.append(_wrap(
            f"SUGGESTION, NOT ENTERED: your profile implies “"
            f"{_excerpt(item.suggestion, _VALUE_CAP)}”. The agent did not type "
            f"it — confirm it yourself.",
            indent="        ", hang="        ",
        ))
    if item.note:
        lines.append(_wrap(
            _excerpt(item.note, _NOTE_CAP), indent="        ", hang="        ",
        ))
    return lines


def _render_band(band: str, items: tuple[ReportItem, ...]) -> list[str]:
    out: list[str] = []
    for heading, group in _sectioned(items):
        if heading:
            out.append("")
            out.append(f"  {heading}")
        for item in group:
            if band == DONE:
                out.append(_render_done_item(item))
            else:
                out.append("")
                out.extend(_render_item(item))
    return out


def render_text(report: HandoffReport, *, show_filled: bool = True) -> str:
    """Module-level alias for `HandoffReport.render_text`."""
    return report.render_text(show_filled=show_filled)
