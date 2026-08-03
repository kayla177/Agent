"""Did the human actually submit it? — Phase B's after-the-fact verification.

Phase A records an application **optimistically**: the moment the user confirms
the apply modal, a row lands in `applications`. Nothing checks that a form was
ever really sent. Greenhouse, Lever and Ashby all replace the form with a
confirmation page once a submission goes through, so a page read *after* the
human presses Submit can upgrade that optimistic row to a verified one.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module is the strictest case of it — it exists precisely because the agent
does NOT submit. It reads two strings (`html`, `url`) and returns a verdict. It
imports no Playwright, holds no page, and has no way to touch a control.

Who calls this
==============
Nothing, yet — deliberately. The graph ends and leaves the browser open; the
human submits *afterwards*, so at graph-exit time there is nothing to detect.
Task 10 owns the trigger (a UI action, or a poll of the still-open page). Two
entry points are provided and neither is wired in:

  * `detect_confirmation(html, url)` — pure. No browser, no database, no model.
  * `stamp_if_confirmed(app_id, html, url)` — the same verdict, plus the one
    store write it authorises. The database import is inside the function so the
    pure half stays importable with no storage layer at all (pinned by
    `test_the_detector_module_imports_no_database_layer`).

The risk is asymmetric, so the answer is biased
===============================================
A **false negative** costs the user nothing she cannot see: the optimistic row
is still in her tracker, merely unstamped. A **false positive** tells her an
application is submitted when it is not, so she stops following up on a job she
never applied for. That is unrecoverable by inspection — the row *looks*
verified. Every judgement call below therefore breaks toward "no stamp".

Concretely, a stamp requires ALL FOUR of:

  1. **A recognised ATS host.** `greenhouse.io` / `lever.co` / `ashbyhq.com`
     only. A company-hosted careers page that *embeds* one of these boards is
     not recognised and will never be stamped — a known, accepted false
     negative (see KNOWN LIMITS).
  2. **A completion phrase in the page's VISIBLE TEXT** — text nodes only, with
     `<script>`/`<style>`/`<template>` contents and every tag attribute removed.
     This is not a stylistic preference. All three real apply-form fixtures
     contain confirmation-shaped strings *in exactly those places*:
       - `tests/fixtures/ats/lever-form.html` ships the stylesheet rule
         `.confirmation-message {text-align: center;}` on the form page (Lever
         serves one stylesheet for both the form and its thanks page), and a
         question card literally headed "AU Clearance Confirmation";
       - `tests/fixtures/ats/ashby-form.html` embeds the JSON key
         `"applicationSubmittedSuccessMessage": null` in a `<script>` payload.
     A naive `"confirmation" in html` or `"applicationSubmitted" in html` check
     therefore reports SUCCESS ON THE UNSUBMITTED FORM for two of the three
     boards. Both vectors are pinned as negative tests against the real
     fixtures.
  3. **No failure phrase** anywhere in that visible text. A validation-error
     re-render saying "your application was not submitted" contains the word
     "submitted"; the negation has to be looked for explicitly.
  4. **No résumé/file-upload control left on the page.** A confirmation page
     does not ask for the résumé again; all three real form fixtures do have a
     `type="file"` input (3 / 2 / 1 respectively). This is the structural half
     of the check: it distinguishes "the form is gone" from "the form is still
     here with encouraging words on it".

The URL is read, recorded, and NOT allowed to decide anything on its own — see
`AtsMarkers.url_paths` for why.

KNOWN LIMITS (each one is a false negative, i.e. the safe direction)
====================================================================
  * **The confirmation fixtures are synthetic.** No real confirmation page
    exists anywhere in this repo, and none can be captured: obtaining one means
    actually submitting an application, which THE ONE RULE forbids. The three
    `tests/fixtures/ats/*-confirmation.html` files were hand-written from the
    phrasing these boards are believed to use. **They have never been compared
    against a real post-submit page.** So the negative evidence here is real
    (measured against captured apply forms) and the positive evidence is not.
    The first real submission is what validates the phrase lists; until then,
    treat a missing stamp as unremarkable.
  * **Custom confirmation copy.** Ashby exposes
    `applicationSubmittedSuccessMessage` and Greenhouse lets a company edit its
    confirmation text; either can replace the default wording entirely, and then
    no phrase matches. Nothing is stamped. That is the correct outcome for a
    detector that cannot read the company's mind.
  * **Embedded boards.** `careers.acme.com` with a Greenhouse iframe reports the
    company's host, not the board's, and is not recognised (limit 1).
  * **A single-page board that keeps the form mounted** behind a success overlay
    would fail limit 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AtsMarkers:
    """One board's evidence table.

    `text_phrases` overlap heavily between boards, and that is not an oversight
    waiting to be de-duplicated into one shared tuple: "thank you for applying"
    is generic English, not a Lever trademark. Keeping a separate table per
    board means a phrase added for one board after seeing its real confirmation
    page cannot silently widen the other two.

    `url_paths` are **supporting evidence only**. `detect_confirmation` records
    whether one matched and never lets it decide, because the path a board
    redirects to after a submit is the one thing here that could not be checked
    against anything: the captured fixtures are all pre-submit URLs. Lever's
    `/thanks` is believed to be right and is still not trusted; if the paths are
    wrong the detector loses nothing, whereas a URL-only rule that happened to
    match a form page would stamp an unsubmitted application.
    """

    #: Registrable domain(s), matched at a dot boundary so `evil-lever.co` and
    #: `lever.co.attacker.net` do not pass.
    hosts: tuple[str, ...]
    text_phrases: tuple[str, ...]
    url_paths: tuple[str, ...]


#: Phrases every board is checked for. Each one asserts a *completed* action;
#: none of them appears in any of the three captured apply-form fixtures
#: (`test_no_real_apply_form_is_read_as_a_confirmation` measures that, rather
#: than this comment asserting it).
_COMPLETED = (
    "thank you for applying",
    "thanks for applying",
    "your application has been submitted",
    "your application was submitted",
    "your application has been received",
    "we have received your application",
    "we've received your application",
    "application submitted successfully",
    "successfully submitted your application",
)

_MARKERS: dict[str, AtsMarkers] = {
    "greenhouse": AtsMarkers(
        hosts=("greenhouse.io",),
        text_phrases=_COMPLETED + ("your application for this position has been",),
        url_paths=("/application_confirmation", "/confirmation"),
    ),
    "lever": AtsMarkers(
        hosts=("lever.co",),
        text_phrases=_COMPLETED,
        url_paths=("/thanks",),
    ),
    "ashby": AtsMarkers(
        hosts=("ashbyhq.com",),
        text_phrases=_COMPLETED,
        url_paths=("/application/confirmation", "/confirmation"),
    ),
}

#: Explicit negations and error copy. Checked before any phrase can count: a
#: re-rendered form saying "your application was NOT submitted" contains a
#: perfectly good completion phrase as a substring of its own denial.
_FAILURE_PHRASES = (
    "was not submitted",
    "not been submitted",
    "could not be submitted",
    "unable to submit",
    "failed to submit",
    "error submitting",
    "please correct",
    "something went wrong",
    "problem submitting",
)

#: Elements whose CONTENT is not text a human reads. The confirmation-shaped
#: strings in the real form fixtures live in the first two.
_INVISIBLE_ELEMENTS = frozenset({
    "script", "style", "template", "noscript", "title", "svg", "head",
})

#: A résumé upload control still on the page — i.e. the form has not gone away.
_FILE_INPUT_RE = re.compile(
    r"<input\b[^>]*\btype\s*=\s*[\"']?file\b", re.IGNORECASE | re.DOTALL
)

#: Typographic apostrophes/quotes, so "We've" written with U+2019 still matches
#: a phrase list written in ASCII. `unicodedata.normalize` does NOT do this.
_QUOTE_MAP = {ord("‘"): "'", ord("’"): "'",
              ord("“"): '"', ord("”"): '"'}


class _VisibleText(HTMLParser):
    """Collect text nodes, skipping `_INVISIBLE_ELEMENTS` contents.

    `convert_charrefs=True` (the default) means `&amp;` and `&#8217;` arrive
    already decoded in `handle_data`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipping: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _INVISIBLE_ELEMENTS:
            self._skipping.append(tag)

    def handle_endtag(self, tag: str) -> None:
        # Tolerant of mismatched nesting: pop the matching open skip if there is
        # one, rather than assuming well-formed markup. Real board HTML is not.
        if tag in _INVISIBLE_ELEMENTS and tag in self._skipping:
            index = len(self._skipping) - 1 - self._skipping[::-1].index(tag)
            del self._skipping[index:]

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.chunks.append(data)


def visible_text(html: str) -> str:
    """`html` reduced to the words a human would see, normalised for matching.

    Lowercased, curly quotes folded to ASCII, all whitespace collapsed to single
    spaces. Text nodes are joined WITH a space (never concatenated), because
    `Thank you for<br>applying` has to read as one sentence — while
    `<td>Submitted</td><td>Yes</td>` must not become one word.

    Everything a tag ATTRIBUTE carries is dropped. `class="confirmation-message"`
    and `title="Thank you for applying"` are markup, not the page's text, and
    treating them as text is how a form page gets read as a confirmation.
    """
    parser = _VisibleText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A malformed page must produce "no evidence", never an exception that
        # a caller might mistake for a failed submission.
        pass
    joined = " ".join(parser.chunks)
    return re.sub(r"\s+", " ", joined.translate(_QUOTE_MAP)).strip().lower()


def identify_ats(url: str) -> str:
    """Which board `url` belongs to: "greenhouse" | "lever" | "ashby" | "".

    Host suffix at a dot boundary, so regional hosts (`jobs.eu.lever.co`,
    `job-boards.eu.greenhouse.io`) are covered without enumerating them, and a
    lookalike domain is not.
    """
    host = (urlsplit(url or "").hostname or "").lower()
    if not host:
        return ""
    for name, markers in _MARKERS.items():
        for domain in markers.hosts:
            if host == domain or host.endswith("." + domain):
                return name
    return ""


@dataclass(frozen=True)
class ConfirmationResult:
    """The verdict, plus enough evidence to explain it in a UI or a log.

    `confirmed` is the only field anything should branch on. `marker` is the
    phrase that carried it (so a human can go look at that page and agree),
    `url_hint` records whether the URL *also* looked like a post-submit path
    without ever having been allowed to decide, and `reason` is the one-line
    explanation — present on a match as well as a miss, because "why did it
    stamp this" is asked at least as often as "why didn't it".
    """

    confirmed: bool
    ats: str = ""
    marker: str = ""
    url_hint: bool = False
    reason: str = ""


def detect_confirmation(html: str, url: str) -> ConfirmationResult:
    """Pure verdict on whether `html`/`url` is a post-submit confirmation page.

    No browser, no database, no model, no network — two strings in, a dataclass
    out. Returns `confirmed=False` for anything it cannot positively vouch for,
    including empty input and malformed markup.

    The four required conditions, and why each one is there, are in the module
    docstring; this function is their conjunction in order of cost.
    """
    ats = identify_ats(url)
    if not ats:
        return ConfirmationResult(
            False,
            reason=(
                "not a recognised ATS host (greenhouse.io / lever.co / "
                "ashbyhq.com), so there is no confirmation page to recognise"
            ),
        )
    markers = _MARKERS[ats]
    path = (urlsplit(url or "").path or "").lower()
    url_hint = any(p in path for p in markers.url_paths)

    text = visible_text(html)
    if not text:
        return ConfirmationResult(
            False, ats=ats, url_hint=url_hint,
            reason="the page has no readable text",
        )

    failure = next((p for p in _FAILURE_PHRASES if p in text), "")
    if failure:
        return ConfirmationResult(
            False, ats=ats, url_hint=url_hint,
            reason=f"the page says “{failure}”, which is a denial, not a receipt",
        )

    phrase = next((p for p in markers.text_phrases if p in text), "")
    if not phrase:
        return ConfirmationResult(
            False, ats=ats, url_hint=url_hint,
            reason=f"no {ats} confirmation wording in the page's visible text",
        )

    if _FILE_INPUT_RE.search(html or ""):
        return ConfirmationResult(
            False, ats=ats, marker=phrase, url_hint=url_hint,
            reason=(
                "the page still has a file-upload control, so the application "
                "form has not been replaced by a confirmation"
            ),
        )

    return ConfirmationResult(
        True, ats=ats, marker=phrase, url_hint=url_hint,
        reason=f"{ats} confirmation page: visible text says “{phrase}”",
    )


def stamp_if_confirmed(app_id: int, html: str, url: str) -> ConfirmationResult:
    """Verify, and on a match only, stamp `applications.confirmed_at`.

    Returns the same `ConfirmationResult` `detect_confirmation` would, so a
    caller can show the reason either way. On a NON-match this function performs
    **no database access at all** — not an update to a null, not a delete, not a
    status change. Absence of evidence is not evidence: the optimistic row is
    left exactly as Phase A wrote it.

    A second call on an already-stamped row is a no-op that keeps the original
    timestamp; that guarantee lives in `store.mark_confirmed`, which is also the
    only place in the codebase that writes this column.

    The import is function-local on purpose: it keeps `detect_confirmation`
    usable (and testable) without pulling in the storage layer.
    """
    result = detect_confirmation(html, url)
    if not result.confirmed:
        return result
    from agents.application_tracker import store as appstore  # noqa: PLC0415

    appstore.mark_confirmed(int(app_id))
    return result
