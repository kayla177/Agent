"""Task 9 — did the human actually submit it?

Two halves, tested separately because they fail differently:

  * `agents.job_applier.confirm.detect_confirmation(html, url)` — pure. Two
    strings in, a verdict out. No browser, no database, no model, no network.
  * `agents.application_tracker.store.mark_confirmed(app_id)` — the one writer
    of `applications.confirmed_at`, which only ever stamps.

WHERE THE EVIDENCE IS REAL AND WHERE IT IS NOT
==============================================
The three `*-confirmation.html` fixtures are **synthetic**. They were hand-written
and have never been compared against a real post-submit page, because getting a
real one means actually submitting a job application — THE ONE RULE of Phase B
forbids it. So every test below that asserts `confirmed is True` is asserting
that the detector behaves as designed on the page shape it was designed for; it
is NOT evidence that a real Greenhouse/Lever/Ashby confirmation page will match.
Each fixture says so in a header comment, and `test_the_confirmation_fixtures_are
_labelled_synthetic` fails if that label is ever quietly removed.

The negative half IS real, and it is the half that guards the harmful direction.
`tests/fixtures/ats/{greenhouse,lever,ashby}-form.html` are captured from live
postings, and two of the three contain confirmation-shaped strings on the
UNSUBMITTED form:

    lever-form.html   →  ".confirmation-message {text-align: center;}"  (CSS)
                         "AU Clearance Confirmation"                   (a card)
    ashby-form.html   →  '"applicationSubmittedSuccessMessage":null'    (JSON)

so `"confirmation" in html` and `"applicationSubmitted" in html` are both True on
a form nobody has filled in. Those two facts are asserted here, not assumed, and
the detector is required to refuse all three real forms.

Asymmetry, since it drives every judgement call: a false negative leaves an
unstamped optimistic row the user can still see. A false positive tells her an
application is submitted when it is not, so she stops chasing a job she never
applied for. When the evidence is thin, no stamp.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import store_db  # noqa: E402
from agents.application_tracker import store as appstore  # noqa: E402
from agents.job_applier import confirm  # noqa: E402
from agents.job_applier.confirm import (  # noqa: E402
    detect_confirmation,
    identify_ats,
    stamp_if_confirmed,
    visible_text,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
BOARDS = ("greenhouse", "lever", "ashby")

#: The real URLs the apply-form fixtures were captured from (kept in step with
#: `scripts/capture_ats_fixtures.py`'s TARGETS). Detection has to refuse these
#: pages *at their own URLs* — refusing them at a made-up URL would prove
#: nothing, since an unrecognised host is refused unconditionally.
FORM_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/cloudflare/jobs/8077075",
    "lever": "https://jobs.lever.co/palantir/395a4483-fc3d-4b77-a500-501923fd0976/apply",
    "ashby": "https://jobs.ashbyhq.com/snowflake/41e65c6c-a01e-4f40-af14-ae75d3b95e27/application",
}

#: Plausible post-submit URLs for the synthetic fixtures. Unverified, like the
#: fixtures themselves — which is exactly why the detector treats the URL as
#: supporting evidence and never as a reason on its own.
CONFIRM_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/cloudflare/jobs/8077075/application_confirmation",
    "lever": "https://jobs.lever.co/palantir/395a4483-fc3d-4b77-a500-501923fd0976/thanks",
    "ashby": "https://jobs.ashbyhq.com/snowflake/41e65c6c-a01e-4f40-af14-ae75d3b95e27/application",
}


def form_html(board: str) -> str:
    return (FIXTURES / f"{board}-form.html").read_text()


def confirmation_html(board: str) -> str:
    return (FIXTURES / f"{board}-confirmation.html").read_text()


# ---------------------------------------------------------------------------
# The fixtures' provenance is part of the contract
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("board", BOARDS)
def test_the_confirmation_fixtures_are_labelled_synthetic(board):
    """These files must never be mistaken for captures.

    Every green assertion about a confirmation page in this file rests on
    hand-written HTML. A future reader who assumes otherwise will over-trust the
    positive half of the detector, so the label is asserted rather than left to
    goodwill — and the label says the specific true thing ("never been compared
    against a real ... page"), not a vague "example".
    """
    text = confirmation_html(board)
    assert "SYNTHETIC FIXTURE" in text
    assert "NEVER BEEN COMPARED AGAINST A REAL" in text.upper()


def test_the_apply_form_fixtures_really_do_carry_confirmation_shaped_strings():
    """The measured basis for reading VISIBLE TEXT instead of raw HTML.

    If a board ever stops shipping these strings on its form page, this test
    fails and the justification in `confirm.py` has to be re-checked rather than
    quietly becoming folklore. (It would then be a *stale reason for a rule that
    is still right* — but "still right for reasons nobody can reproduce" is how
    the rule gets deleted.)
    """
    lever = form_html("lever")
    assert ".confirmation-message {text-align: center;}" in lever
    assert "AU Clearance Confirmation" in lever
    assert '"applicationSubmittedSuccessMessage":null' in form_html("ashby")


# ---------------------------------------------------------------------------
# Negative: the real, captured apply forms. The direction that matters.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("board", BOARDS)
def test_no_real_apply_form_is_read_as_a_confirmation(board):
    """A captured, UNSUBMITTED apply form must never be stamped."""
    result = detect_confirmation(form_html(board), FORM_URLS[board])
    assert result.confirmed is False
    assert result.ats == board  # the host WAS recognised; the page was refused


def test_the_naive_raw_html_check_would_have_passed_the_lever_form():
    """The false positive this design exists to prevent, made concrete.

    `"confirmation" in html` is True on Lever's empty form — twice over, from a
    stylesheet rule and from a question card. Note the word survives into the
    visible text too (the card heading is real page text), so stripping
    `<style>` is not by itself the discriminator: the phrase list is. Both are
    load-bearing and this test pins both.
    """
    html = form_html("lever")
    assert "confirmation" in html.lower()               # the naive check passes
    assert "confirmation" in visible_text(html)         # even after stripping
    assert detect_confirmation(html, FORM_URLS["lever"]).confirmed is False


def test_the_naive_raw_html_check_would_have_passed_the_ashby_form():
    """Same trap, different vector: Ashby's bootstrap JSON. Here the stripping
    IS the discriminator — the key lives in a `<script>`, so it never reaches the
    visible text at all."""
    html = form_html("ashby")
    assert "applicationSubmitted" in html               # the naive check passes
    assert "applicationsubmitted" not in visible_text(html)
    assert detect_confirmation(html, FORM_URLS["ashby"]).confirmed is False


# ---------------------------------------------------------------------------
# Positive: the synthetic fixtures (see the module docstring's caveat)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("board", BOARDS)
def test_each_synthetic_confirmation_fixture_is_detected(board):
    result = detect_confirmation(confirmation_html(board), CONFIRM_URLS[board])
    assert result.confirmed is True, result.reason
    assert result.ats == board
    assert result.marker, "the matched phrase must be recorded so a human can check it"
    assert result.marker in visible_text(confirmation_html(board))


def test_the_lever_fixture_matches_through_a_typographic_apostrophe():
    """`We&rsquo;ve received your application` — U+2019, which is what a real
    page uses. The phrase list is ASCII, so the text is folded before matching;
    without that fold this fixture's second phrase would be invisible."""
    text = visible_text(confirmation_html("lever"))
    assert "we've received your application" in text
    assert "’" not in text


def test_a_phrase_split_across_tags_still_matches():
    """`Thank you for<br>applying` is one sentence to a human and two text nodes
    to a parser."""
    html = "<h1>Thank you for<br><span>applying</span></h1><p>Done.</p>"
    assert detect_confirmation(html, FORM_URLS["lever"]).confirmed is True


def test_text_nodes_are_joined_with_a_space_not_concatenated():
    """The other side of that join. Gluing adjacent nodes together would let
    `<td>Submit</td><td>ted</td>` fabricate words that are not on the page."""
    assert visible_text("<b>Thank</b><b>s</b> for applying") == "thank s for applying"
    assert detect_confirmation("<b>Thank</b><b>s</b> for applying", FORM_URLS["lever"]).confirmed is False


# ---------------------------------------------------------------------------
# Negative: synthetic pages aimed at each individual rule
# ---------------------------------------------------------------------------
def test_a_phrase_hidden_in_a_style_or_script_block_is_not_evidence():
    for wrapper in ("style", "script"):
        html = f"<{wrapper}>/* Thank you for applying */</{wrapper}><p>Apply below.</p>"
        assert detect_confirmation(html, FORM_URLS["lever"]).confirmed is False, wrapper


def test_a_phrase_in_an_attribute_is_not_evidence():
    """Attributes are markup. `title="Thank you for applying"` on the Apply
    button of an empty form is a tooltip, not a receipt."""
    html = (
        '<div class="thank-you-for-applying" data-x="your application has been submitted" '
        'title="Thank you for applying" aria-label="thanks for applying">Apply</div>'
    )
    assert detect_confirmation(html, FORM_URLS["lever"]).confirmed is False


def test_an_unrecognised_host_is_never_confirmed():
    """A company careers page that embeds a board reports its OWN host. Refused,
    even with perfect wording — an accepted false negative (see confirm.py's
    KNOWN LIMITS), because "any page anywhere that says thank you" is exactly
    how a stamp gets applied to something that never happened."""
    result = detect_confirmation(
        "<h1>Thank you for applying</h1>", "https://careers.acme.com/thanks"
    )
    assert result.confirmed is False
    assert result.ats == ""
    assert "recognised ATS host" in result.reason


@pytest.mark.parametrize("url", [
    "https://jobs.lever.co.attacker.net/x/thanks",
    "https://evil-lever.co/x/thanks",
    "https://greenhouse.io.phish.example/confirmation",
])
def test_a_lookalike_host_is_not_a_board(url):
    """Suffix matching has to be at a dot boundary in both directions."""
    assert identify_ats(url) == ""
    assert detect_confirmation("<h1>Thank you for applying</h1>", url).confirmed is False


def test_a_url_that_looks_post_submit_is_not_enough_on_its_own():
    """`/thanks` is the weakest evidence here and the only kind that could not be
    checked against anything, so it decides nothing. A page with no confirmation
    wording is refused at a confirmation URL."""
    result = detect_confirmation("<h1>Software Engineer</h1><p>Apply now.</p>",
                                 CONFIRM_URLS["lever"])
    assert result.confirmed is False
    assert result.url_hint is True, "the hint is recorded even when it is ignored"


def test_a_confirmation_without_a_matching_url_still_counts():
    """The converse: the URL is not required either. Ashby's success view is
    rendered at the same `/application` path as the form, so requiring a URL
    change would have made Ashby undetectable."""
    result = detect_confirmation(confirmation_html("ashby"), CONFIRM_URLS["ashby"])
    assert result.confirmed is True
    assert result.url_hint is False


@pytest.mark.parametrize("denial", [
    "Your application was not submitted.",
    "We could not be submitted — please try again.",
    "There was a problem submitting your application.",
    "Please correct the errors below.",
])
def test_an_error_page_that_also_says_thank_you_is_refused(denial):
    """A re-rendered form with a validation error is the realistic false-positive
    source: the page keeps its friendly heading and adds a failure notice. The
    denial contains a completion phrase as a substring of itself, so the negation
    has to be searched for explicitly and checked FIRST."""
    html = f"<h1>Thank you for applying</h1><div class='error'>{denial}</div>"
    result = detect_confirmation(html, CONFIRM_URLS["lever"])
    assert result.confirmed is False
    assert "denial" in result.reason


def test_a_page_still_asking_for_a_resume_is_refused():
    """The structural half: a confirmation page does not ask for the résumé
    again. All three captured forms carry a file input; a page that both thanks
    you and offers an upload is a form, not a receipt."""
    html = (
        "<h1>Thank you for applying</h1>"
        '<label for="cv">Resume</label><input id="cv" type="file">'
    )
    result = detect_confirmation(html, CONFIRM_URLS["lever"])
    assert result.confirmed is False
    assert "file-upload" in result.reason
    assert result.marker, "the phrase that ALMOST convinced it is still recorded"


@pytest.mark.parametrize("html,url", [
    ("", ""),
    ("", "https://jobs.lever.co/x/y/thanks"),
    ("<h1>Thank you for applying", "https://jobs.lever.co/x/y/thanks"),  # unclosed
    ("<style><h1>Thank you for applying</h1>", "https://jobs.lever.co/x/y/thanks"),
    ("not html at all <<<>>", "https://jobs.lever.co/x/y/thanks"),
    ("<h1>Thank you for applying</h1>", "not a url"),
])
def test_degenerate_input_returns_a_verdict_instead_of_raising(html, url):
    """A parser blowing up must not surface as anything a caller could read as a
    successful submission. Note case 3: an unclosed `<style>` swallows the whole
    page, giving no visible text — refused, which is the safe direction."""
    result = detect_confirmation(html, url)
    assert isinstance(result, confirm.ConfirmationResult)
    if html.strip() == "<h1>Thank you for applying":
        assert result.confirmed is True  # tolerant of a missing close tag
    else:
        assert result.confirmed is False


# ---------------------------------------------------------------------------
# The detector is pure
# ---------------------------------------------------------------------------
def test_detect_confirmation_touches_no_database(monkeypatch):
    """Purity, enforced rather than described. If `detect_confirmation` ever
    grew a store call, every case below would raise."""
    def explode(*args, **kwargs):
        raise RuntimeError("detect_confirmation must not open the database")

    monkeypatch.setattr(store_db, "connect", explode)
    monkeypatch.setattr(store_db, "init_db", explode)
    assert detect_confirmation(confirmation_html("lever"), CONFIRM_URLS["lever"]).confirmed
    assert not detect_confirmation(form_html("lever"), FORM_URLS["lever"]).confirmed


def test_the_detector_module_imports_no_database_layer_and_no_browser():
    """`confirm.py` may only reach the store from INSIDE `stamp_if_confirmed`.

    A module-level import would make the pure detector drag the storage layer
    (and `config`, and the prefs file) behind it, and would make "this half has
    no side effects" a claim about discipline rather than about imports.
    """
    source = pathlib.Path(confirm.__file__).read_text()
    module_level = re.findall(r"^(?:import|from)\s+\S+", source, re.M)
    assert not any("store" in line or "playwright" in line for line in module_level), module_level
    assert "import playwright" not in source
    assert "from playwright" not in source
    # And the lazy import really is inside the function that needs it.
    assert "from agents.application_tracker import store" in source


# ---------------------------------------------------------------------------
# The store write: stamp only, once, and nothing else
# ---------------------------------------------------------------------------
def _new_app(**kw) -> dict:
    return appstore.add_application(
        kw.pop("company", "Acme"), kw.pop("role", "SWE"), **kw
    )


def _row(app_id: int) -> dict:
    return next(a for a in appstore.load_all() if a["id"] == app_id)


def test_a_new_application_starts_unconfirmed(temp_db):
    """Optimistic by construction: Phase A's row is not evidence of anything."""
    app = _new_app()
    assert app["confirmed_at"] is None


def test_mark_confirmed_stamps_the_row(temp_db):
    app = _new_app()
    out = appstore.mark_confirmed(app["id"], when="2026-08-02T12:00:00+00:00")
    assert out["confirmed_at"] == "2026-08-02T12:00:00+00:00"
    assert _row(app["id"])["confirmed_at"] == "2026-08-02T12:00:00+00:00"


def test_the_default_stamp_is_an_iso8601_utc_instant(temp_db):
    """Matches `server/db._utcnow()`. A bare date could not distinguish two
    confirmations on the same day, and the tracker's other date columns are
    day-resolution precisely because they are user-entered."""
    app = _new_app()
    stamp = appstore.mark_confirmed(app["id"])["confirmed_at"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", stamp), stamp


def test_a_second_detection_is_a_no_op_not_a_re_stamp(temp_db):
    """Ruling: a second detection on an already-confirmed row must not move the
    timestamp. Re-stamping would rewrite a recorded fact every time a page
    happened to be re-read, and the earliest known confirmation is the true one."""
    app = _new_app()
    first = appstore.mark_confirmed(app["id"], when="2026-08-02T12:00:00+00:00")
    again = appstore.mark_confirmed(app["id"], when="2026-08-09T23:59:59+00:00")
    assert again["confirmed_at"] == first["confirmed_at"] == "2026-08-02T12:00:00+00:00"


def test_mark_confirmed_changes_nothing_except_confirmed_at(temp_db):
    app = _new_app(url="https://x", notes="n", status="interview")
    before = _row(app["id"])
    after = appstore.mark_confirmed(app["id"])
    assert after["confirmed_at"] is not None
    assert {k: v for k, v in after.items() if k != "confirmed_at"} == \
           {k: v for k, v in before.items() if k != "confirmed_at"}


def test_mark_confirmed_on_a_missing_id_returns_none_and_creates_nothing(temp_db):
    assert appstore.mark_confirmed(9999) is None
    assert appstore.load_all() == []


def test_a_later_status_change_does_not_un_confirm(temp_db):
    """`update_status` is the other writer on this table and it must leave the
    verification alone — including the manual path that clears `auto_detected`."""
    app = _new_app()
    appstore.mark_confirmed(app["id"], when="2026-08-02T12:00:00+00:00")
    appstore.update_status(app["id"], "rejected")
    assert _row(app["id"])["confirmed_at"] == "2026-08-02T12:00:00+00:00"


_PY_DIRS = ("agents", "server", "scripts", "shell")
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _repo_sources() -> list[pathlib.Path]:
    paths = list(_ROOT.glob("*.py"))
    for name in _PY_DIRS:
        directory = _ROOT / name
        if not directory.is_dir():
            continue
        paths += [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(paths)


def test_the_source_sweep_actually_covers_the_files_it_claims_to():
    """A sweep that silently globs nothing is the failure mode of every guard
    like the one below."""
    swept = {p.relative_to(_ROOT).as_posix() for p in _repo_sources()}
    for required in ("store_db.py", "agents/application_tracker/store.py",
                    "agents/job_applier/confirm.py", "server/routers/applications.py"):
        assert required in swept, required


def test_nothing_in_the_repo_ever_clears_confirmed_at():
    """Ruling: never un-confirm. Enforced across the whole source tree, not just
    the two files this task touched, because the harmful edit is one somebody
    adds later in a "reset the row" helper.

    Only SET clauses are matched: `WHERE confirmed_at = ''` is the guard that
    makes the stamp write-once, and a pattern broad enough to catch it would have
    to be weakened or deleted, taking the real check with it.
    """
    clearing = re.compile(r"SET\s+confirmed_at\s*=\s*(?:NULL|None|''|\"\")", re.I)
    writers = []
    for path in _repo_sources():
        source = path.read_text()
        if "confirmed_at" not in source:
            continue
        assert not clearing.search(source), f"{path} clears confirmed_at"
        if "SET confirmed_at" in source:
            writers.append(path.relative_to(_ROOT).as_posix())
    assert writers == ["agents/application_tracker/store.py"], writers


def _code_only(path: pathlib.Path) -> str:
    """`path`'s source with every docstring removed.

    Same reason `tests/test_applier_locate.py` does this: the prose in
    `confirm.py` legitimately names what it refuses to do ("not a delete, not a
    status change"), and a raw-text guard would either false-positive on the
    explanation or get "fixed" by deleting the sentence.
    """
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_the_detection_path_never_deletes_or_drops_anything():
    """A non-match leaves the row alone; it does not tidy it up."""
    code = _code_only(pathlib.Path(confirm.__file__)).lower()
    for banned in ("delete", "drop table", "truncate", "update_status", "set_resume_link"):
        assert banned not in code, banned
    # And the guard is not vacuous: the one store call it IS allowed to make.
    assert "mark_confirmed" in code


# ---------------------------------------------------------------------------
# The two halves together — the brief's three cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("board", BOARDS)
def test_a_confirmation_page_stamps_the_row(temp_db, board):
    """The brief's first case, per ATS. Synthetic fixtures — see the module
    docstring."""
    app = _new_app(company=board)
    result = stamp_if_confirmed(app["id"], confirmation_html(board), CONFIRM_URLS[board])
    assert result.confirmed is True
    assert _row(app["id"])["confirmed_at"] is not None


@pytest.mark.parametrize("board", BOARDS)
def test_a_non_confirmation_page_leaves_the_row_exactly_as_it_was(temp_db, board):
    """The brief's second case. Not "leaves it unconfirmed" — leaves it
    IDENTICAL. Absence of evidence is not evidence: no status change, no note, no
    deletion, no write of any kind."""
    app = _new_app(company=board, url="https://x", notes="keep me")
    before = _row(app["id"])
    result = stamp_if_confirmed(app["id"], form_html(board), FORM_URLS[board])
    assert result.confirmed is False
    assert _row(app["id"]) == before
    assert _row(app["id"])["confirmed_at"] is None


def test_a_non_match_performs_no_database_access_at_all(temp_db, monkeypatch):
    """Stronger than comparing rows before and after: the store is made to
    explode, so a write that happened to be a no-op would still fail here."""
    def explode(*args, **kwargs):
        raise RuntimeError("a non-match must not touch the database")

    monkeypatch.setattr(store_db, "connect", explode)
    monkeypatch.setattr(store_db, "init_db", explode)
    result = stamp_if_confirmed(1, form_html("lever"), FORM_URLS["lever"])
    assert result.confirmed is False


def test_detecting_the_same_confirmation_twice_is_idempotent(temp_db):
    """The brief's third case, through the wired path rather than the store
    alone: a poll that reads the same open page twice must not move the stamp."""
    app = _new_app()
    stamp_if_confirmed(app["id"], confirmation_html("ashby"), CONFIRM_URLS["ashby"])
    first = _row(app["id"])["confirmed_at"]
    stamp_if_confirmed(app["id"], confirmation_html("ashby"), CONFIRM_URLS["ashby"])
    assert _row(app["id"])["confirmed_at"] == first


def test_a_confirmation_for_a_different_board_still_stamps_only_the_row_given(temp_db):
    """Two optimistic rows, one confirmation. The other row must not move —
    `mark_confirmed` is addressed by id and has no WHERE-less update in it."""
    a, b = _new_app(company="A"), _new_app(company="B")
    stamp_if_confirmed(a["id"], confirmation_html("greenhouse"), CONFIRM_URLS["greenhouse"])
    assert _row(a["id"])["confirmed_at"] is not None
    assert _row(b["id"])["confirmed_at"] is None
