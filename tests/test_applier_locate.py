"""DOM discovery + label location for Lever / Ashby / Greenhouse application forms.

PURE BY CONSTRUCTION: every test here parses saved HTML from
`tests/fixtures/ats/`. Nothing in this file launches a browser or touches the
network — `test_module_does_not_import_playwright` and
`test_module_has_no_mutating_call` enforce that the module under test keeps its
side of that bargain too.

Fixture provenance — captured 2026-08-01T00:08:49Z by
`scripts/capture_ats_fixtures.py` (a read-only dev probe: it navigates, waits
for the form, dumps `page.content()`, and closes; it never types, clicks, or
submits):

  lever-form.html      https://jobs.lever.co/palantir/395a4483-fc3d-4b77-a500-501923fd0976/apply
                       captured by plain httpx GET — Lever server-renders the form
                       (1 <form>, 74 <input>, 51 <label>, 8 <textarea>, 5 <select>)
  ashby-form.html      https://jobs.ashbyhq.com/snowflake/41e65c6c-a01e-4f40-af14-ae75d3b95e27/application
                       captured RENDERED — a plain GET returns a 41 KB JS shell
                       with 0 inputs
  greenhouse-form.html https://boards.greenhouse.io/cloudflare/jobs/8077075
                       (301 -> job-boards.greenhouse.io/cloudflare/jobs/8077075)
                       captured RENDERED. NOTE: contrary to the Task 4 brief, a
                       plain GET of this URL *does* now yield a server-rendered
                       form (67 KB, 18 <input>, 16 <label>) as long as the 301 is
                       followed — the "254 KB shell, 0 inputs" measurement is
                       reproducible only without following the redirect. It is
                       still captured rendered, because rendered is what the live
                       locator will actually see.

The Greenhouse posting is the same job (Cloudflare 8077075) as
`greenhouse-questions.json`, so `test_greenhouse_dom_keys_agree_with_the_schema`
can cross-check the DOM path against the JSON-schema path for one identical form.
"""

from __future__ import annotations

import ast
import collections
import inspect
import json
import pathlib
import re

import pytest

from agents.job_applier import locate_dom, schema_greenhouse
from agents.job_applier.locate_dom import (
    Control,
    PageLocator,
    discover_questions,
    find_by_key,
    find_control,
    find_selector,
    normalize_label,
    parse_controls,
)
from agents.job_applier.schema_greenhouse import Question, parse_questions

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ats"
BOARDS = ("lever", "ashby", "greenhouse")


def _html(board: str) -> str:
    return (FIXTURES / f"{board}-form.html").read_text()


@pytest.fixture(scope="module")
def controls() -> dict[str, list[Control]]:
    return {board: parse_controls(_html(board)) for board in BOARDS}


@pytest.fixture(scope="module")
def questions() -> dict[str, list[Question]]:
    return {board: discover_questions(_html(board)) for board in BOARDS}


def _by_label(items):
    return {normalize_label(i.label): i for i in items}


# ---------------------------------------------------------------------------
# THE ONE RULE, and purity
# ---------------------------------------------------------------------------

_MUTATING = (
    ".fill(",
    ".click(",
    ".check(",
    ".uncheck(",
    ".type(",
    ".press(",
    ".select_option(",
    "set_input_files(",
    ".dispatch_event(",
    ".submit(",
)


# The read-only code paths of Phase B, as file paths. `capture_ats_fixtures.py`
# matters most of the three: it is the ONE committed file that drives a real
# browser, and until now its safety rested entirely on its own docstring saying
# "grep this file". THE ONE RULE should not be enforced by a comment.
_READ_ONLY_FILES = (
    pathlib.Path(locate_dom.__file__),
    pathlib.Path(__file__).parent.parent / "scripts" / "capture_ats_fixtures.py",
)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove every docstring from `tree`, in place, and return it.

    Necessary for any source-scanning guard here: the prose legitimately names
    the calls and terms being banned, precisely in order to explain why they are
    absent. Scanning raw text therefore either false-positives on the
    explanation or (worse) gets "fixed" by weakening the check to exact matching.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.fix_missing_locations(tree)


def _executable_source(path: pathlib.Path) -> str:
    """`path`'s code with docstrings and comments removed."""
    return ast.unparse(_strip_docstrings(ast.parse(path.read_text())))


def _code_string_literals(path: pathlib.Path) -> list[str]:
    """Every string literal in `path` that is NOT a docstring."""
    return [
        node.value
        for node in ast.walk(_strip_docstrings(ast.parse(path.read_text())))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("path", _READ_ONLY_FILES, ids=lambda p: p.name)
def test_module_has_no_mutating_call(path):
    """No code path in Phase B may ever click a submit button, and these files are
    supposed to only *read*. Cheap textual guard, but it is the one that would
    actually catch a future edit adding a `.fill()` "just for testing"."""
    code = _executable_source(path)
    for call in _MUTATING:
        assert call not in code, f"{path.name} must never call {call}"


def test_the_capture_probe_is_covered_by_the_one_rule_guard():
    """Belt and braces on the parametrisation itself: if someone renames or moves
    the capture script, the guard must fail loudly rather than silently cover one
    fewer file."""
    names = {p.name for p in _READ_ONLY_FILES}
    assert "capture_ats_fixtures.py" in names
    for path in _READ_ONLY_FILES:
        assert path.is_file(), path


def test_module_does_not_import_playwright():
    """Playwright is a ~150MB optional dependency and the whole point of the
    pure/adapter split is that discovery works without it."""
    source = pathlib.Path(locate_dom.__file__).read_text()
    assert "import playwright" not in source
    assert "from playwright" not in source


@pytest.mark.parametrize("board", BOARDS)
def test_submit_and_hidden_controls_are_never_discovered(board, controls):
    """A `type=submit` input discovered as a fillable question is one refactor
    away from being filled. Hidden state and reCAPTCHA are likewise not
    questions."""
    for control in controls[board]:
        assert control.input_type not in ("submit", "button", "reset", "image", "hidden")
        assert control.name != "g-recaptcha-response"


def test_the_recaptcha_rule_is_exercised_by_two_of_three_fixtures_not_three():
    """`_MACHINE_NAMES`'s comment claimed `g-recaptcha-response` was "present in
    all three captured fixtures". MEASURED: greenhouse 3 occurrences, ashby 3,
    **Lever none**.

    That matters for what the rule's evidence is, not for whether it is right: on
    Lever the rule is untested by any fixture, so `test_submit_and_hidden_controls
    _are_never_discovered` passing for Lever says nothing about it. A comment that
    overstates its own coverage is how the untested third gets assumed covered.
    """
    counts = {b: _html(b).count("g-recaptcha-response") for b in BOARDS}
    assert counts == {"greenhouse": 3, "lever": 0, "ashby": 3}
    source = pathlib.Path(locate_dom.__file__).read_text()
    assert "LEVER NONE" in source
    # And the rule is genuinely load-bearing on the two that DO ship it: without
    # `_MACHINE_NAMES` the hidden textarea would be discovered as a question.
    html = '<textarea name="g-recaptcha-response"></textarea>'
    assert parse_controls(html) == []
    assert locate_dom._MACHINE_NAMES == frozenset({"g-recaptcha-response"})


def test_the_page_locator_is_described_by_what_it_does_not_by_a_wrong_length():
    """Decision 6 called `PageLocator` a "~25-line shim". It is 93 lines and eight
    public accessors — and the claim worth making was never about its length: it is
    that NONE of those accessors parses anything, which is what keeps the whole
    matching story testable against saved fixtures with no browser.

    So this asserts the property instead of policing a number: every public
    accessor delegates, and the module-level pure functions are where the parsing
    lives.
    """
    accessors = {n for n in vars(locate_dom.PageLocator) if not n.startswith("_")}
    assert accessors == {
        "controls", "html", "questions", "refresh", "unreadable", "withheld_eeo",
        "locator_for_label", "locator_for_key",
    }
    tree = _strip_docstrings(ast.parse(pathlib.Path(locate_dom.__file__).read_text()))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "PageLocator")
    # No parsing in the class: `parse_controls` / `_questions_from` are CALLED, and
    # nothing in here touches the HTML parser or the label/required helpers.
    called = {n.func.id for n in ast.walk(cls)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert called <= {"parse_controls", "_questions_from", "_answerable",
                      "_unreadable", "_eeo_withheld", "find_selector", "find_by_key",
                      "str", "list"}
    assert "_derive_label" not in called and "_required_of" not in called
    source = pathlib.Path(locate_dom.__file__).read_text()
    assert "93 lines and eight accessors" in source


def test_submit_input_with_a_label_is_still_not_discovered():
    html = """
    <label for="go">Submit Application</label>
    <input id="go" type="submit" value="Submit Application">
    <label for="real">Email</label><input id="real" type="text">
    """
    labels = [c.label for c in parse_controls(html)]
    assert labels == ["Email"]


@pytest.mark.parametrize("board", BOARDS)
def test_no_selector_is_positional(board, controls):
    """A selector built from position (`:nth-child`, `>` chains) silently points
    at the wrong box the moment a board reorders a form. Only `id`/`name`."""
    for control in controls[board]:
        if control.selector is None:
            continue
        assert "nth" not in control.selector
        assert ">" not in control.selector
        assert control.selector.startswith(("[id=", "input[name=", "textarea[name=", "select[name="))


def test_a_hidden_ancestor_hides_the_control():
    """The hidden copy carries the SAME label as the visible field, so it wins
    the exact tier and ambiguity never fires — one wrong candidate, silently.
    Collapsed accordions, mobile/desktop duplicate pairs and `react-modal`'s
    `aria-hidden` on the app root all produce this shape."""
    html = (
        '<div aria-hidden="true"><label for="ghost">Email</label><input id="ghost" name="e1"></div>'
        '<label for="real">Email Address</label><input id="real" name="e2">'
    )
    control = find_control(parse_controls(html), "Email")
    assert control is not None and control.element_id == "real"


@pytest.mark.parametrize(
    "wrapper",
    [
        '<div aria-hidden="true">',
        '<div hidden>',
        '<div inert>',
        '<div style="display:none">',
        '<div style="display: none;">',
        '<div style="color:red;visibility:hidden">',
        "<template>",
    ],
)
def test_controls_inside_a_hidden_container_are_not_discovered(wrapper):
    close = "</template>" if wrapper == "<template>" else "</div>"
    html = f'{wrapper}<label for="a">Email</label><input id="a">{close}'
    assert parse_controls(html) == []


def test_a_visually_hidden_file_input_is_still_discovered():
    """Both Ashby and Greenhouse hide their REAL file inputs with a clip/1px
    square and trigger them from a styled button. Treating clipped controls as
    absent would make résumé upload undiscoverable on two of three boards, so
    only display:none / visibility:hidden / aria-hidden count as hidden."""
    html = (
        '<label for="r">Resume</label>'
        '<input id="r" type="file" tabindex="-1" '
        'style="border: 0px; clip: rect(0px, 0px, 0px, 0px); position: absolute; width: 1px; height: 1px;">'
    )
    control = parse_controls(html)[0]
    assert control.kind == "file" and control.selector == '[id="r"]'


@pytest.mark.parametrize("board", BOARDS)
def test_the_real_resume_upload_survives_the_hidden_filter(board, controls):
    assert any(c.kind == "file" for c in controls[board])


def test_an_input_with_the_boolean_hidden_attribute_is_not_discovered():
    assert parse_controls('<label for="a">Email</label><input id="a" hidden>') == []


def test_unlabelled_inputs_are_not_discovered_by_position():
    """Three anonymous inputs: the answer is "I cannot locate anything here",
    not "probably the second one"."""
    html = "<form><input type=text><input type=text><input type=text></form>"
    parsed = parse_controls(html)
    assert parsed == []
    assert find_control(parsed, "Email") is None


# ---------------------------------------------------------------------------
# Label normalization and boundary-safe matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Email",
        "email",
        "  EMAIL  ",
        "Email*",
        "Email *",
        "Email ✱",  # Lever's U+2731 HEAVY ASTERISK, not an ASCII star
        "Email (required)",
        "Email (Required)",
        "E-mail",
        "E-mail *",
        "Email:",
        "E‑mail",  # U+2011 non-breaking hyphen
    ],
)
def test_email_spellings_all_normalize_to_one_query(raw):
    assert find_control([Control(
        tag="input", input_type="text", label=raw, label_source="label",
        kind="text", required=False, required_source="", selector="[id=e]",
    )], "Email") is not None


def test_normalize_label_collapses_whitespace_and_case():
    assert normalize_label("  Full   NAME ✱ ") == "full name"
    assert normalize_label("How did you hear about this job?*") == "how did you hear about this job"


def test_a_label_that_is_only_a_required_marker_normalizes_to_empty():
    assert normalize_label("*") == ""
    assert normalize_label("✱") == ""


def _one(label: str) -> list[Control]:
    return [Control(
        tag="input", input_type="text", label=label, label_source="label",
        kind="text", required=False, required_source="", selector="[id=x]",
    )]


def test_matching_is_word_boundary_safe_not_substring():
    """`locations.py` matched "uk" inside "Milwaukee" and the Task 3 resolver
    matched the pronoun "us" as the United States. Neither may happen here."""
    assert find_control(_one("Email"), "mail") is None
    assert find_control(_one("Milwaukee, WI"), "UK") is None
    assert find_control(_one("What are your pronouns?"), "us") is None


def test_a_query_may_not_match_a_label_suffix():
    """"Name" must not find "First Name" — that is how a full name lands in a
    first-name box."""
    assert find_control(_one("First Name"), "Name") is None
    assert find_control(_one("Last Name"), "Name") is None


def test_a_query_may_match_a_whole_token_prefix():
    assert find_control(_one("Email Address"), "Email") is not None
    assert find_control(_one("Phone Number"), "Phone") is not None
    # ...but only on a token boundary.
    assert find_control(_one("Emailing List"), "Email") is None


@pytest.mark.parametrize("label", ["姓名", "Прізвище", "Prénom", "Straße", "Ünvan"])
def test_a_non_ascii_label_is_still_findable(label):
    """An ASCII-only `[^0-9a-z]` fold erased CJK and Cyrillic labels to "" —
    unfindable by ANY query — and split "Prénom" into the tokens "pr nom"."""
    assert normalize_label(label) != ""
    assert find_control(_one(label), label) is not None


def test_an_accent_split_does_not_create_a_false_token_boundary():
    """"Pr" matching "Prénom" is the same class of bug as "uk" matching
    "Milwaukee": a word FRAGMENT satisfying the token-prefix rule."""
    assert find_control(_one("Prénom"), "Pr") is None
    assert find_control(_one("Prénom"), "Pré") is None
    # ...while the accent itself is folded, so either spelling of the query works.
    assert find_control(_one("Prénom"), "Prenom") is not None
    assert find_control(_one("Prenom"), "Prénom") is not None


def test_both_readings_of_a_hyphen_are_accepted():
    assert find_control(_one("E-mail"), "Email") is not None
    assert find_control(_one("Full-Name"), "Full Name") is not None
    assert find_control(_one("Full Name"), "Full-Name") is not None


def test_an_empty_query_never_matches_anything():
    assert find_control(_one("Email"), "") is None
    assert find_control(_one("Email"), "   ") is None
    assert find_control(_one("Email"), "*") is None


# ---------------------------------------------------------------------------
# Ambiguity returns None, never a guess
# ---------------------------------------------------------------------------


def test_two_equally_good_matches_return_none():
    html = """
    <label for="a">Email</label><input id="a" type="text">
    <label for="b">Email</label><input id="b" type="text">
    """
    parsed = parse_controls(html)
    assert len(parsed) == 2
    assert find_control(parsed, "Email") is None
    assert find_selector(parsed, "Email") is None


def test_two_ambiguous_prefix_matches_return_none():
    html = """
    <label for="a">Email Address</label><input id="a" type="text">
    <label for="b">Email Confirmation</label><input id="b" type="text">
    """
    assert find_control(parse_controls(html), "Email") is None


def test_an_exact_match_wins_over_two_prefix_matches():
    """The exact tier must be load-bearing: with "Email Address", "Email
    Confirmation" AND a plain "Email", the prefix tier has three hits and would
    return `None`; only tier 1 resolves it.

    (An earlier version of this test used "First Name" + "Name", which does NOT
    exercise the tiering at all — "Name" is a *suffix* of "First Name" so the
    prefix tier already had exactly one hit, and the test passed with the exact
    tier deleted.)
    """
    html = """
    <label for="a">Email Address</label><input id="a" type="text">
    <label for="b">Email Confirmation</label><input id="b" type="text">
    <label for="c">Email</label><input id="c" type="text">
    """
    parsed = parse_controls(html)
    control = find_control(parsed, "Email")
    assert control is not None and control.selector == '[id="c"]'
    # ...and the prefix tier on its own is genuinely ambiguous here.
    assert find_control([c for c in parsed if c.element_id != "c"], "Email") is None


def test_a_duplicated_id_makes_its_label_unusable():
    """Two elements with the same `id` means `<label for>` is ambiguous. Falling
    through to the next chain step is right; picking one is not."""
    html = """
    <label for="dup">Email</label>
    <input id="dup" type="text" placeholder="Your email">
    <span id="dup"></span>
    """
    control = parse_controls(html)[0]
    assert control.label_source == "placeholder"
    assert control.selector is None  # a duplicated id cannot address anything


def test_two_labels_claiming_one_control_make_it_unusable():
    html = """
    <label for="x">Email</label>
    <label for="x">Work Email</label>
    <input id="x" type="text" aria-label="Contact Email">
    """
    control = parse_controls(html)[0]
    assert control.label_source == "aria-label"
    assert control.label == "Contact Email"


def test_greenhouse_two_visually_hidden_attach_labels_are_ambiguous(controls):
    """Real case: Greenhouse labels both its résumé and its cover-letter file
    inputs "Attach". Querying that must not pick one."""
    attach = [c for c in controls["greenhouse"] if c.label == "Attach"]
    assert len(attach) == 2
    assert find_control(controls["greenhouse"], "Attach") is None


# ---------------------------------------------------------------------------
# The fallback chain, step by step — each with a real-fixture example
# ---------------------------------------------------------------------------


def test_chain_1a_label_for_id():
    html = '<label for="e">Email Address</label><input id="e" type="text" name="whatever">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("Email Address", "label")


def test_chain_1a_real_ashby(controls):
    """Ashby: `<label for="_systemfield_email">Email</label>` +
    `<input id="_systemfield_email">`."""
    control = find_control(controls["ashby"], "Email")
    assert control is not None
    assert (control.label, control.label_source) == ("Email", "label")
    assert control.selector == '[id="_systemfield_email"]'


def test_chain_1b_wrapping_label_uses_only_the_text_before_the_control():
    html = (
        "<label><div>Current location <span>✱</span></div>"
        '<div><input type="text" name="location"></div>'
        "<div>No location found. Try entering a different location</div></label>"
    )
    control = parse_controls(html)[0]
    # The ✱ is stripped from the STORED label (`_clean_label`) but was still
    # visible to the required inference, which is why this comes back required.
    assert (control.label, control.label_source) == ("Current location", "label")
    assert control.required and control.required_source == "label-marker"


def test_chain_1b_real_lever(controls):
    """On the real Lever form this field has BOTH a wrapping `<label>` and a
    question block, and the block wins for a non-choice control (see
    `test_the_question_block_beats_the_wrapping_label_for_a_non_choice_control`).
    Either way the trailing dropdown chrome must not reach the label."""
    control = find_control(controls["lever"], "Current location")
    assert control is not None
    assert control.label == "Current location"
    assert control.label_source == "question-block"
    assert "No location found" not in control.label


def test_a_wrapping_label_labels_only_its_first_labelable_descendant():
    """Per HTML, a `<label>`'s labeled control is its FIRST labelable descendant.
    Labelling all of them gave three controls named "Address" — and once two of
    them are filtered out for any reason, the survivor is a *unique* wrong
    match."""
    html = '<label>Address<input name="street"><input name="city"><input name="zip"></label>'
    parsed = parse_controls(html)
    assert [(c.name, c.label, c.label_source) for c in parsed] == [
        ("street", "Address", "label"),
        ("city", "city", "name"),
        ("zip", "zip", "name"),
    ]


def test_a_wrapping_labels_text_after_the_control_is_not_glued_on():
    """Source-order matters even when the trailing chrome is a bare text node
    rather than an element — the earlier walker attributed a container's own text
    to "before" regardless of where it sat relative to the input."""
    html = '<label>Current location <input name="loc"> No location found. Try again</label>'
    assert parse_controls(html)[0].label == "Current location"
    html = '<label>A<span>B</span><input name="t">C<span>D</span></label>'
    assert parse_controls(html)[0].label == "AB"


def test_chain_1b_falls_back_to_trailing_text_for_a_wrapped_checkbox():
    """Lever's option labels sit *after* the checkbox, so "before" is empty."""
    html = '<label><input type="checkbox" name="lang" value="x"><span>English (ENG)</span></label>'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("English (ENG)", "label")


def test_chain_1c_dangling_for_matching_a_unique_name():
    html = (
        '<label for="q1">Do you require sponsorship?</label>'
        '<input type="checkbox" name="q1">'  # note: no id="q1" anywhere
    )
    control = parse_controls(html)[0]
    assert control.label == "Do you require sponsorship?"
    assert control.label_source == "label-for-name"


def test_chain_1c_is_refused_when_the_name_is_not_unique():
    """Repairing a dangling reference is only safe when it can point at exactly
    one control."""
    html = (
        '<label for="q1">Pick one</label>'
        '<input type="text" name="q1" placeholder="A">'
        '<input type="text" name="q1" placeholder="B">'
    )
    for control in parse_controls(html):
        assert control.label_source == "placeholder"


def test_chain_1c_real_ashby(controls):
    """Ashby ships `<label for="28ff5b93-…">Will you require company
    sponsorship…</label>` with no element carrying that id — only an
    `<input name="28ff5b93-…">`. Without the repair the label would be a UUID."""
    repaired = [c for c in controls["ashby"] if c.label_source == "label-for-name"]
    assert len(repaired) == 4
    sponsorship = find_control(controls["ashby"], "Will you require company sponsorship")
    assert sponsorship is not None
    assert sponsorship.name == "28ff5b93-a104-45f7-9d46-2d13d3217dca"


# ---------------------------------------------------------------------------
# Chain step 1b: Lever's li.application-question containment
# ---------------------------------------------------------------------------

_LEVER_BLOCK = (
    '<li class="application-question custom-question"><div>'
    '<div class="application-label full-width multiple-choice">'
    '<div class="text">{question}<span class="required">✱</span></div></div>'
    '<div class="application-field full-width required-field">{field}</div>'
    "</div></li>"
)


def test_chain_1b_question_block_reads_a_sibling_label():
    html = _LEVER_BLOCK.format(
        question="High School Name", field='<textarea name="cards[abc][field0]" required></textarea>'
    )
    control = parse_controls(html)[0]
    assert control.label == "High School Name"
    assert control.label_source == "question-block"


def test_the_question_block_is_scoped_to_li_application_question():
    """Scope is the whole safety argument: this is a containment relationship
    inside one question wrapper, not a "nearest preceding text" guess. Remove the
    `li.application-question` requirement and the rule becomes exactly the
    proximity heuristic that was ruled out."""
    # Same label/field divs, but NOT inside an li.application-question.
    loose = (
        '<div><div class="application-label"><div class="text">High School Name</div></div>'
        '<div class="application-field"><textarea name="cards[abc][field0]"></textarea></div></div>'
    )
    control = parse_controls(loose)[0]
    assert control.label_source == "name"
    assert control.label == "cards[abc][field0]"


def test_the_question_block_requires_an_li_not_just_the_class():
    """The scope is `li.application-question` — the element Lever actually uses
    per question. Accepting the class on any element widens the containment to
    whatever a page happens to wrap around several fields, which is how the rule
    would drift back into a proximity guess."""
    html = (
        '<div class="application-question"><div>'
        '<div class="application-label"><div class="text">High School Name</div></div>'
        '<div class="application-field"><textarea name="cards[abc][field0]"></textarea></div>'
        "</div></div>"
    )
    assert parse_controls(html)[0].label_source == "name"
    # ...and the same markup in an `li` is read.
    assert parse_controls(html.replace("div class=\"application-question\"", "li class=\"application-question\"", 1))[0].label_source == "question-block"


def test_the_question_block_requires_an_application_label_element():
    html = (
        '<li class="application-question"><div>'
        '<div class="something-else">High School Name</div>'
        '<textarea name="cards[abc][field0]"></textarea></div></li>'
    )
    assert parse_controls(html)[0].label_source == "name"


def test_the_question_block_class_match_is_token_not_substring():
    """`application-label-wrapper` is a different class from `application-label`."""
    html = (
        '<li class="application-question-group"><div>'
        '<div class="application-label"><div class="text">Nope</div></div>'
        '<textarea name="cards[abc][field0]"></textarea></div></li>'
    )
    assert parse_controls(html)[0].label_source == "name"


def test_real_lever_blocking_questions_are_discovered(questions):
    """The two BLOCKING_KINDS questions on the captured Palantir form. Before the
    containment rule BOTH came back with `label=""` — so Task 7 would have
    reported a form as ready-to-review with work authorization and sponsorship
    silently unlabelled, which is worse than a wrong element because nothing
    signals it."""
    by_label = {q.label: q for q in questions["lever"]}
    work_auth = by_label[
        "Are you legally authorized to work in the country for which you are applying?"
    ]
    sponsorship = by_label[
        "Will you now or in the future require sponsorship for employment visa status "
        "(e.g., H-1B, etc.)?"
    ]
    for question in (work_auth, sponsorship):
        assert question.required is True, question.label
        assert question.kind == "select", question.label  # radio group = single choice
        assert question.options == ["Yes", "No"], question.label
        assert question.key.startswith("cards[1c719ca9-5069-4afe-9e82-39ca420e0edb]")


def test_real_lever_blocking_questions_are_locatable_per_option(controls):
    """Reported is not enough — Task 6 has to be able to act on them. Each option
    is addressed by name+value, which HTML requires to be unique within a group."""
    for question in (
        "Are you legally authorized to work in the country for which you are applying?",
        "Will you now or in the future require sponsorship for employment visa status "
        "(e.g., H-1B, etc.)?",
    ):
        members = locate_dom.find_group_options(controls["lever"], question)
        assert [m.label for m in members] == ["Yes", "No"], question
        assert all(m.selector for m in members), question
        assert all("[value=" in m.selector for m in members), question


def test_real_lever_two_placeholder_twins_are_now_distinct(questions):
    """Both of these are `<input placeholder="Type here…">`, so the placeholder
    tier collapsed them into two questions labelled "Type your response" —
    indistinguishable, and the ambiguity went undetected because it happened one
    layer below `find_control`. Asserting the labels DIFFER is what makes the
    collapse unable to come back silently."""
    labels = [q.label for q in questions["lever"]]
    preferred = "Preferred Name | What would you like us to call you?"
    pronunciation = "Name Pronunciation | How do you pronounce your name?"
    assert preferred in labels
    assert pronunciation in labels
    assert preferred != pronunciation
    assert "Type your response" not in labels
    assert len(labels) == len(set(labels)), "two questions must never share a label"


def test_real_lever_high_school_name_is_read_not_keyed(controls):
    control = find_control(controls["lever"], "High School Name")
    assert control is not None
    assert control.label == "High School Name"
    assert control.kind == "textarea"
    assert not control.label.startswith("cards[")


def test_the_question_block_beats_the_wrapping_label_for_a_non_choice_control():
    """Lever's résumé field has both, and the wrapping label also encloses the
    upload button — so it reads "Resume/CV ✱ATTACH RESUME/CV" where the block
    holds exactly "Resume/CV ✱"."""
    html = (
        '<li class="application-question"><div>'
        '<div class="application-label">Resume/CV <span class="required">✱</span></div>'
        '<div class="application-field"><label>'
        '<a><span class="default-label">ATTACH RESUME/CV</span>'
        '<input id="resume-upload-input" name="resume" type="file"></a></label></div>'
        "</div></li>"
    )
    control = parse_controls(html)[0]
    assert control.label == "Resume/CV"
    assert control.label_source == "question-block"


def test_real_lever_resume_label_has_no_button_text(controls):
    control = find_control(controls["lever"], "Resume/CV")
    assert control is not None
    assert control.label == "Resume/CV"
    assert "ATTACH" not in control.label
    assert control.required is True


def test_the_wrapping_label_beats_the_question_block_for_a_choice_control():
    """The other convention: a wrapping `<label>` around a radio holds the OPTION
    text, and the block holds the question. Swap the order and every option comes
    back labelled with the whole question, so the group's `options` list becomes N
    copies of its own heading."""
    html = _LEVER_BLOCK.format(
        question="Authorized to work?",
        field=(
            "<ul>"
            '<li><label><input type="radio" name="cards[x][f0]" value="Yes" required/>'
            '<span>Yes</span></label></li>'
            '<li><label><input type="radio" name="cards[x][f0]" value="No" required/>'
            '<span>No</span></label></li></ul>'
        ),
    )
    parsed = parse_controls(html)
    assert [c.label for c in parsed] == ["Yes", "No"]
    assert all(c.group_label == "Authorized to work?" for c in parsed)
    question = discover_questions(html)[0]
    assert question.label == "Authorized to work?"
    assert question.options == ["Yes", "No"]


def test_required_comes_from_the_question_blocks_span_required():
    """`span.required` renders the ✱ inside the label text, so the existing
    trailing-marker signal picks it up — which is why the marker is stripped only
    AFTER the required inference has seen it."""
    html = _LEVER_BLOCK.format(question="Why us?", field='<textarea name="cards[x][f0]"></textarea>')
    control = parse_controls(html)[0]
    assert control.required is True
    assert control.required_source == "label-marker"
    # ...and without the marker, the control is not claimed to be required.
    html = html.replace('<span class="required">✱</span>', "")
    assert parse_controls(html)[0].required is False


def test_the_control_own_required_attribute_still_wins_over_the_marker():
    html = _LEVER_BLOCK.format(
        question="Why us?", field='<textarea name="cards[x][f0]" required="required"></textarea>'
    )
    assert parse_controls(html)[0].required_source == "required-attr"


# ---------------------------------------------------------------------------
# Required markers never reach a stored label
# ---------------------------------------------------------------------------

_MARKER_CHARS = "*✱＊∗٭"


@pytest.mark.parametrize("board", BOARDS)
def test_no_stored_label_contains_a_required_marker(board, controls):
    """The stored label is what Task 3 phrase-matches on and what Task 7 shows a
    human, so a stray "✱"/"*" is not cosmetic. Greenhouse rendered "First Name*"
    and Lever "Full name✱"."""
    for control in controls[board]:
        for text in (control.label, control.group_label):
            assert not any(ch in text for ch in _MARKER_CHARS), (control.label_source, text)


@pytest.mark.parametrize("board", BOARDS)
def test_no_discovered_question_label_contains_a_required_marker(board, questions):
    for question in questions[board]:
        assert not any(ch in question.label for ch in _MARKER_CHARS), question.label
        for option in question.options:
            assert not any(ch in option for ch in _MARKER_CHARS), option


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Full name✱", "Full name"),
        ("First Name*", "First Name"),
        ("  Email  ", "Email"),
        ("✱ Email", "Email"),
        ("Email (required)", "Email (required)"),  # words are not a marker char
        ("Resume/CV ✱", "Resume/CV"),
        ("*", ""),
        ("✱", ""),
    ],
)
def test_clean_label_strips_only_marker_characters(raw, expected):
    assert locate_dom._clean_label(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Email (required)", "email"),
        ("Email (Required)", "email"),
        ("Email required", "email"),
        ("Work authorization required", "work authorization"),
        ("Email *", "email"),
        ("Email ✱", "email"),
    ],
)
def test_normalize_label_strips_a_spelled_out_required_suffix(raw, expected):
    """Ruling 3 required tolerance of a trailing "(required)", and
    `_REQUIRED_SUFFIX_RE` is the ONLY thing that provides it — `_NON_WORD_RE`
    folds "*"/"✱" to a space but leaves the WORD "required" as a token, so
    "Email (required)" would normalize to "email required".

    Asserted as an equality on `normalize_label` rather than through
    `find_control`, because the prefix tier masks it: "email" is a token prefix of
    "email required" either way, so every lookup-level test passed with this
    substitution deleted.
    """
    assert normalize_label(raw) == expected


def test_the_marker_character_lists_in_code_and_tests_agree():
    """U+00B7 MIDDLE DOT was in the module's marker list and not in this file's,
    which is exactly why nothing caught a label like "Team ·" being reported
    required. Pinning them equal makes that class of drift impossible."""
    assert set(locate_dom._REQUIRED_MARK_CHARS) == set(_MARKER_CHARS)


@pytest.mark.parametrize("label", ["Team ·", "Role·", "Notes · ", "A · B ·"])
def test_a_separator_is_not_a_required_marker(label):
    """An interpunct is a separator, not an asterisk variant. It used to make a
    label report `required=True, required_source="label-marker"` out of nowhere."""
    control = parse_controls(f'<label for="x">{label}</label><input id="x" type="text">')[0]
    assert control.required is False
    assert control.required_source == ""
    assert "·" in control.label, "the separator stays in the label; it is content"


def test_a_label_whose_whole_content_is_a_marker_falls_through_the_chain():
    """`<label for="x">*</label>` reads as blank to a human, so the chain must
    CONTINUE rather than accept it and then drop the control entirely."""
    html = '<label for="x">*</label><input id="x" type="text" placeholder="you@example.com">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("you@example.com", "placeholder")


def test_a_marker_only_aria_label_falls_through_to_the_placeholder():
    """The OUTER `_clean_label` guard in `_derive_label`, as distinct from the
    sub-step guard in `_associated_label_text`. The documented `<label for=x>*`
    example is caught by the inner one, which left the outer half untested and
    deletable — it IS reachable, via a marker-only `aria-label`/`placeholder`/
    `name`, and without it the chain stops at the useless value and the control
    is dropped instead of falling through."""
    control = parse_controls('<input type="text" aria-label="*" placeholder="you@example.com">')[0]
    assert (control.label, control.label_source) == ("you@example.com", "placeholder")


def test_a_marker_only_placeholder_falls_through_to_the_name():
    control = parse_controls('<input type="text" placeholder="✱" name="_systemfield_email">')[0]
    assert (control.label, control.label_source) == ("_systemfield_email", "name")


def test_a_control_whose_every_label_source_is_a_marker_is_dropped():
    assert parse_controls('<input type="text" aria-label="*" placeholder="✱" name="*">') == []


def test_a_marker_only_label_falls_through_to_the_NEXT_step_1_substep():
    """The fall-through has to happen inside step 1's sub-chain, not just at the
    top level. Here `<label for="x">*</label>` is useless but a wrapping `<label>`
    two lines down holds the real text; accepting the marker-only label and only
    then rejecting it would skip straight past the wrapping label to
    `placeholder`."""
    html = (
        '<label for="x">*</label>'
        '<label>Email Address<input id="x" type="text" placeholder="you@example.com"></label>'
    )
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("Email Address", "label")


# ---------------------------------------------------------------------------
# The weak tiers refuse duplicates
# ---------------------------------------------------------------------------


def test_two_controls_sharing_a_placeholder_are_both_refused():
    """Two DIFFERENT questions wearing one label is the ambiguity `find_control`
    refuses, arriving one layer earlier. This is what produced two Lever questions
    both labelled "Type your response"."""
    html = (
        '<input type="text" name="a" placeholder="Type your response">'
        '<input type="text" name="b" placeholder="Type your response">'
    )
    assert parse_controls(html) == []
    assert discover_questions(html) == []


def test_a_unique_placeholder_is_still_accepted():
    html = (
        '<input type="text" name="a" placeholder="Type your response">'
        '<input type="text" name="b" placeholder="Your website">'
    )
    assert sorted(c.label for c in parse_controls(html)) == ["Type your response", "Your website"]


def test_duplicate_checking_does_not_apply_to_strong_label_sources():
    """Two option labels reading "Yes" in two different groups is normal and must
    keep working — the demotion is only for placeholder/`name`."""
    html = """
    <fieldset><legend>Authorized to work</legend>
      <input type="radio" id="a1" name="g1"><label for="a1">Yes</label>
      <input type="radio" id="a2" name="g1"><label for="a2">No</label></fieldset>
    <fieldset><legend>Willing to relocate</legend>
      <input type="radio" id="b1" name="g2"><label for="b1">Yes</label>
      <input type="radio" id="b2" name="g2"><label for="b2">No</label></fieldset>
    """
    assert len(parse_controls(html)) == 4
    assert len(discover_questions(html)) == 2


def test_real_ashby_placeholder_survives_because_it_is_unique(controls):
    """The demotion must not cost Ashby its location combobox, whose placeholder
    is genuinely the only label available."""
    placeholder_labelled = [c for c in controls["ashby"] if c.label_source == "placeholder"]
    assert len(placeholder_labelled) == 1
    assert placeholder_labelled[0].label == "Start typing..."


# ---------------------------------------------------------------------------
# Lever's EEO block is excluded structurally
# ---------------------------------------------------------------------------

_EEO_SURVEY = (
    '<div class="section page-centered eeo-survey"><ul>'
    '<li class="application-question"><div>'
    '<div class="application-label"><div class="text">What is your gender?'
    '<span class="required">✱</span></div></div>'
    '<div class="application-field"><ul>'
    '<li><label><input type="radio" name="eeo[gender]" value="Female"><span>Female</span></label></li>'
    '<li><label><input type="radio" name="eeo[gender]" value="Male"><span>Male</span></label></li>'
    '<li><label><input type="radio" name="eeo[gender]" value="decline">'
    "<span>Decline to self-identify</span></label></li>"
    "</ul></div></div></li></ul></div>"
)
_REAL_QUESTION = (
    '<li class="application-question"><div>'
    '<div class="application-label"><div class="text">Full name</div></div>'
    '<div class="application-field"><input type="text" name="name"></div></div></li>'
)


def test_nothing_under_eeo_survey_is_discovered():
    """`.eeo-survey` reuses the same `application-label` classes as the rest of the
    form, so the containment rule would read "What is your gender?" out of it as
    happily as any other question. Excluded structurally, ahead of the text screen —
    and note the *options* here ("Female"/"Male"/"Decline to self-identify") match
    no EEO term, so a text-only screen would not have caught this block."""
    parsed = parse_controls(_EEO_SURVEY + _REAL_QUESTION)
    assert [c.label for c in parsed] == ["Full name"]
    assert [q.label for q in discover_questions(_EEO_SURVEY + _REAL_QUESTION)] == ["Full name"]


def test_the_eeo_block_exclusion_leaves_nothing_in_any_bucket():
    """Unlike the text screen, this one is a structural refusal to look: these are
    not "withheld questions", they are not questions this module reports at all."""
    html = _EEO_SURVEY + _REAL_QUESTION
    assert locate_dom.unreadable_questions(html) == []
    assert locate_dom.excluded_eeo_questions(html) == []
    assert len(discover_questions(html)) == 1


def test_the_eeo_block_class_match_is_a_whole_token():
    """A class literally named `eeo-survey-results` is a different class."""
    html = (
        '<div class="not-eeo-surveyor">'
        '<label for="a">Preferred pastry</label><input id="a" type="text"></div>'
    )
    assert len(parse_controls(html)) == 1


def test_the_captured_lever_fixture_has_no_eeo_block_in_its_markup():
    """Measured, and worth pinning because it is easy to assume otherwise: this
    Palantir posting renders NO EEO block. `eeo-survey` appears 386 times in the
    fixture but every one is a CSS selector inside an inline `<style>`, never a
    class on an element. So the exclusion above is defensive, exercised only by
    synthetic HTML, and the fixture's counts do not depend on it."""
    html = _html("lever")
    markup = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S | re.I)
    assert "eeo-survey" in html
    assert "eeo-survey" not in markup


def test_chain_2_aria_label():
    html = '<input type="text" aria-label="Email Address" name="e">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("Email Address", "aria-label")


def test_chain_2_real_greenhouse(controls):
    """Greenhouse's intl-tel-input country search box has no `<label>`, only
    `aria-label="Search"`."""
    control = find_control(controls["greenhouse"], "Search")
    assert control is not None
    assert (control.label, control.label_source) == ("Search", "aria-label")


def test_chain_2_aria_labelledby_is_dereferenced():
    html = '<div id="h">Cover Letter</div><input type="text" aria-labelledby="h" name="c">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("Cover Letter", "aria-labelledby")


def test_chain_2_aria_labelledby_ignores_a_dangling_reference():
    html = '<input type="text" aria-labelledby="missing" placeholder="Type here" name="c">'
    control = parse_controls(html)[0]
    assert control.label_source == "placeholder"


def test_chain_3_placeholder():
    html = '<input type="text" placeholder="you@example.com" name="e">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("you@example.com", "placeholder")


def test_chain_3_real_ashby(controls):
    """Ashby's location autocomplete has no label, no aria-label, no name — a
    placeholder is genuinely all there is."""
    control = find_control(controls["ashby"], "Start typing")
    assert control is not None
    assert control.label_source == "placeholder"
    # ...and nothing unique to address it by, so it stays the human's to fill.
    assert control.selector is None


def test_chain_4_name_is_the_last_resort():
    html = '<input type="text" name="_systemfield_custom">'
    control = parse_controls(html)[0]
    assert (control.label, control.label_source) == ("_systemfield_custom", "name")


def test_the_name_tier_no_longer_fires_on_any_real_board(controls):
    """It used to fire on 12 of Lever's 65 controls, emitting labels like
    `cards[d54adf7b-…][field0]`. The question-block rule reads the real text, so
    the `name` tier is now unreachable on all three captured boards — it survives
    for boards we have not seen, not as a load-bearing path."""
    for board in BOARDS:
        assert [c for c in controls[board] if c.label_source == "name"] == []
    # ...and it still works when it is genuinely the only thing available.
    assert parse_controls('<input type="text" name="_systemfield_custom">')[0].label_source == "name"


def test_chain_order_label_beats_aria_beats_placeholder_beats_name():
    html = (
        '<label for="x">Visible Label</label>'
        '<input id="x" type="text" aria-label="Aria Label" placeholder="Placeholder" name="the_name">'
    )
    assert parse_controls(html)[0].label == "Visible Label"

    html = '<input id="x" type="text" aria-label="Aria Label" placeholder="Placeholder" name="the_name">'
    assert parse_controls(html)[0].label == "Aria Label"

    html = '<input id="x" type="text" placeholder="Placeholder" name="the_name">'
    assert parse_controls(html)[0].label == "Placeholder"

    html = '<input id="x" type="text" name="the_name">'
    assert parse_controls(html)[0].label == "the_name"


def test_a_control_with_no_derivable_label_is_dropped():
    html = '<input type="text"><textarea></textarea>'
    assert parse_controls(html) == []


def test_all_label_sources_are_declared():
    assert set(locate_dom.LABEL_SOURCES) == {
        "label", "question-block", "label-for-name",
        "aria-label", "aria-labelledby", "placeholder", "name",
    }


@pytest.mark.parametrize("board", BOARDS)
def test_every_discovered_control_names_a_declared_label_source(board, controls):
    for control in controls[board]:
        assert control.label_source in locate_dom.LABEL_SOURCES


# ---------------------------------------------------------------------------
# Identity questions, per board, from the real fixtures
# ---------------------------------------------------------------------------


def test_lever_identity_questions(questions):
    by = _by_label(questions["lever"])
    assert by["full name"].kind == "text" and by["full name"].required
    assert by["email"].kind == "text" and by["email"].required
    assert by["phone"].kind == "text"
    resume = find_control(parse_controls(_html("lever")), "Resume")
    assert resume is not None and resume.kind == "file"
    assert resume.selector == '[id="resume-upload-input"]'


def test_ashby_identity_questions(questions):
    by = _by_label(questions["ashby"])
    assert by["full name"].kind == "text" and by["full name"].required
    assert by["email"].kind == "text" and by["email"].required
    assert by["phone number"].kind == "text" and by["phone number"].required
    assert by["resume"].kind == "file" and by["resume"].required


def test_greenhouse_identity_questions(questions):
    by = _by_label(questions["greenhouse"])
    for label in ("first name", "last name", "email", "phone"):
        assert by[label].kind == "text", label
        assert by[label].required is True, label
    assert by["resume cv"].kind == "file" and by["resume cv"].required


@pytest.mark.parametrize(
    "board,query,expected_selector",
    [
        ("lever", "Full name", 'input[name="name"]'),
        ("lever", "Email", 'input[name="email"]'),
        ("lever", "Phone", 'input[name="phone"]'),
        ("lever", "Resume", '[id="resume-upload-input"]'),
        ("ashby", "Full Name", '[id="_systemfield_name"]'),
        ("ashby", "Email", '[id="_systemfield_email"]'),
        ("ashby", "Phone", '[id="3fdc76ed-6ac2-497a-a6cc-e07ed517ec1d"]'),
        ("ashby", "Resume", '[id="_systemfield_resume"]'),
        ("greenhouse", "First Name", '[id="first_name"]'),
        ("greenhouse", "Last Name", '[id="last_name"]'),
        ("greenhouse", "Email", '[id="email"]'),
        ("greenhouse", "Phone", '[id="phone"]'),
        ("greenhouse", "Resume/CV", '[id="resume"]'),
        ("greenhouse", "Cover Letter", '[id="cover_letter"]'),
    ],
)
def test_label_lookup_finds_the_right_element(board, query, expected_selector, controls):
    assert find_selector(controls[board], query) == expected_selector


@pytest.mark.parametrize("board", BOARDS)
@pytest.mark.parametrize("query", ["Favourite Dinosaur", "Blood Type", "Shoe Size", "Attach"])
def test_an_unmatched_or_ambiguous_query_returns_none(board, query, controls):
    """None of these is a question on any of the three real forms — and "Attach"
    is ambiguous (Greenhouse labels two file inputs that) rather than absent.
    Both must yield `None`."""
    assert find_control(controls[board], query) is None


def test_a_bare_short_query_is_not_a_safe_way_to_address_a_field(controls):
    """Documented hazard, deliberately NOT patched over — read this before
    hardcoding a short query anywhere.

    On Greenhouse and Ashby, "Name" is ambiguous (First/Last/Legal Name) or
    absent, so it returns `None`. On Lever it now resolves — to
    "Name Pronunciation | How do you pronounce your name?", because after the
    containment rule that is the one and only label whose first token is "name",
    so the prefix tier has exactly one hit and no ambiguity to detect. It is not
    the field a caller asking for "Name" wants.

    The rule is doing what it was specified to do; the mistake would be querying
    with a short handle at all. `discover_questions` hands back each question's
    FULL label, and locating by that hits the exact tier — which is what Task 6
    must do. Tightening the prefix tier (e.g. capping how many tokens a label may
    add) would fix this case but would also break "Resume" -> "Resume/CV and
    supporting documents", so it needs a ruling rather than a quiet heuristic.
    """
    assert find_control(controls["greenhouse"], "Name") is None
    assert find_control(controls["ashby"], "Name") is None
    lever_hit = find_control(controls["lever"], "Name")
    assert lever_hit is not None
    assert lever_hit.label.startswith("Name Pronunciation")
    # Querying by the full label is exact and lands on the right field every time.
    for question in discover_questions(_html("lever")):
        found = find_control(controls["lever"], question.label)
        if found is not None:  # None only for multi-option groups, by design
            assert found.label == question.label or found.group_label == question.label


def test_a_short_label_is_not_reachable_by_a_longer_query(controls):
    """Ashby labels the upload just "Resume", so a query of "Resume/CV" finds
    nothing. Documented, deliberate: prefix matching is one-directional, and
    inventing the reverse direction is how "Name" would match "First Name"."""
    assert find_control(controls["ashby"], "Resume/CV") is None
    assert find_control(controls["ashby"], "Resume") is not None


# ---------------------------------------------------------------------------
# `kind` inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html,expected_kind",
    [
        ('<label for="x">L</label><input id="x" type="file">', "file"),
        ('<label for="x">L</label><textarea id="x"></textarea>', "textarea"),
        ('<label for="x">L</label><select id="x"><option>A</option></select>', "select"),
        ('<label for="x">L</label><input id="x" type="checkbox">', "checkbox"),
        ('<label for="x">L</label><input id="x" type="radio">', "select"),
        ('<label for="x">L</label><input id="x" type="text">', "text"),
        ('<label for="x">L</label><input id="x" type="email">', "text"),
        ('<label for="x">L</label><input id="x" type="tel">', "text"),
        ('<label for="x">L</label><input id="x">', "text"),
        ('<label for="x">L</label><input id="x" type="weird-future-type">', "text"),
    ],
)
def test_kind_is_inferred_from_the_element(html, expected_kind):
    assert parse_controls(html)[0].kind == expected_kind


def test_select_options_come_from_the_option_elements():
    html = (
        '<label for="y">Year</label>'
        '<select id="y"><option value="">Select...</option>'
        '<option value="2026">2026</option><option value="Other">Other</option></select>'
    )
    question = discover_questions(html)[0]
    assert question.kind == "select"
    assert question.options == ["2026", "Other"]  # the empty placeholder is not an answer


def test_options_without_a_value_attribute_are_still_options():
    """HTML says an `<option>`'s value defaults to its text, so a `<select>`
    written without any `value=` attributes is perfectly normal. The earlier
    placeholder check ("empty value and nothing collected yet") latched on such a
    select and returned NO options at all — a select question the resolver could
    never answer. Every existing test used explicit `value=`, so it hid."""
    html = '<label for="y">Authorized?</label><select id="y"><option>Yes</option><option>No</option></select>'
    assert discover_questions(html)[0].options == ["Yes", "No"]


def test_only_a_leading_explicitly_empty_option_is_treated_as_a_placeholder():
    html = (
        '<label for="y">Y</label><select id="y">'
        '<option value="">Select…</option><option>Yes</option><option>No</option></select>'
    )
    assert discover_questions(html)[0].options == ["Yes", "No"]
    # A later empty-valued option is not a leading placeholder, and an option
    # with no text at all is not an answer either way.
    html = (
        '<label for="y">Y</label><select id="y">'
        '<option>Yes</option><option value="">None of these</option></select>'
    )
    assert discover_questions(html)[0].options == ["Yes", "None of these"]


def test_real_lever_select_options(questions):
    """Lever's "Year of High School Graduation" dropdown, options read straight
    off the `<option>` elements."""
    year = next(q for q in questions["lever"] if q.kind == "select" and len(q.options) == 12)
    assert year.options[0] == "2020" and year.options[-1] == "Other"


def test_kinds_stay_inside_the_task_2_vocabulary(questions):
    """Reusing `schema_greenhouse.Question` is pointless if this module invents
    a sixth `kind` that the resolver has never heard of."""
    allowed = {"text", "textarea", "file", "select", "checkbox"}
    for board in BOARDS:
        assert {q.kind for q in questions[board]} <= allowed


# ---------------------------------------------------------------------------
# Radio / checkbox grouping
# ---------------------------------------------------------------------------


def test_a_radio_group_is_one_question_with_its_options():
    html = """
    <fieldset>
      <legend>Are you authorized to work in the US?</legend>
      <input type="radio" id="r1" name="auth"><label for="r1">Yes</label>
      <input type="radio" id="r2" name="auth"><label for="r2">No</label>
    </fieldset>
    """
    found = discover_questions(html)
    assert len(found) == 1
    assert found[0].label == "Are you authorized to work in the US?"
    assert found[0].kind == "select"  # a single choice from a fixed list
    assert found[0].options == ["Yes", "No"]


def test_real_ashby_radio_group_is_one_question(questions):
    """Ashby renders each radio question as a `<fieldset>` whose heading is a
    `<label>` with a dangling `for`. Three radios, one question."""
    us_person = next(q for q in questions["ashby"] if q.label.startswith("A “U.S. person”"))
    assert us_person.kind == "select"
    assert len(us_person.options) == 3
    assert us_person.options[0] == "I am a U.S. person"


def test_real_lever_checkbox_group_is_one_labelled_question(questions):
    """Lever's 33 language checkboxes are one question. Its title lives in a
    sibling `div.application-label` inside the same `li.application-question`, so
    the containment rule reads it — this used to come back with 33 options and NO
    label at all."""
    language = next(q for q in questions["lever"] if len(q.options) == 33)
    assert language.kind == "checkbox"
    assert language.label == "Language Skill(s) (Check all that apply)"
    assert language.required is True
    assert "English (ENG)" in language.options
    assert "Choose not to disclose" in language.options


def test_a_multi_option_group_is_located_per_option_not_as_one_element():
    """There is no single element that *is* a radio group, so `find_control` on
    the group heading correctly refuses (all N members match it). The bridge is
    `find_group_options`, which hands back the members so a caller can pick the
    one matching its answer."""
    html = """
    <fieldset>
      <legend>Are you authorized to work in the US?</legend>
      <input type="radio" id="r1" name="auth"><label for="r1">Yes</label>
      <input type="radio" id="r2" name="auth"><label for="r2">No</label>
    </fieldset>
    """
    parsed = parse_controls(html)
    assert find_control(parsed, "Are you authorized to work in the US?") is None
    members = locate_dom.find_group_options(parsed, "Are you authorized to work in the US?")
    assert [(c.label, c.selector) for c in members] == [("Yes", '[id="r1"]'), ("No", '[id="r2"]')]
    # The individual option is directly locatable too.
    assert find_control(parsed, "Yes").selector == '[id="r1"]'


def test_find_group_options_refuses_a_query_spanning_two_groups():
    html = """
    <fieldset><legend>Authorized to work</legend>
      <input type="radio" id="a1" name="g1"><label for="a1">Yes</label>
      <input type="radio" id="a2" name="g1"><label for="a2">No</label></fieldset>
    <fieldset><legend>Authorized to relocate</legend>
      <input type="radio" id="b1" name="g2"><label for="b1">Yes</label>
      <input type="radio" id="b2" name="g2"><label for="b2">No</label></fieldset>
    """
    parsed = parse_controls(html)
    assert locate_dom.find_group_options(parsed, "Authorized") == []
    assert len(locate_dom.find_group_options(parsed, "Authorized to work")) == 2
    assert locate_dom.find_group_options(parsed, "Nonexistent") == []


def test_real_ashby_group_questions_are_locatable_per_option(controls, questions):
    """Every answerable multi-option question on the real Ashby form must be
    reachable somehow, or `discover_questions` is reporting work nobody can do."""
    groups = [q for q in questions["ashby"] if len(q.options) > 1]
    assert groups, "expected at least one multi-option Ashby question"
    for question in groups:
        members = locate_dom.find_group_options(controls["ashby"], question.label)
        assert len(members) == len(question.options), question.label
        assert all(m.selector for m in members), question.label


def test_a_lone_radio_in_a_fieldset_keeps_its_option_text():
    """A radio has no `<option>` children — its option text is its own label. An
    earlier version read `first.options` here and returned `[]`."""
    html = (
        "<fieldset><legend>Authorized?</legend>"
        '<input type="radio" id="r" name="a"><label for="r">Yes</label></fieldset>'
    )
    question = discover_questions(html)[0]
    assert question.label == "Authorized?"
    assert question.options == ["Yes"]


def test_a_lone_consent_checkbox_keeps_its_own_label():
    html = '<label for="c">I agree to the privacy policy</label><input id="c" type="checkbox" name="c">'
    question = discover_questions(html)[0]
    assert question.label == "I agree to the privacy policy"
    assert question.kind == "checkbox"
    assert question.options == []


def test_real_greenhouse_consent_checkbox_uses_its_group_heading(questions):
    consent = next(q for q in questions["greenhouse"] if q.kind == "checkbox")
    assert consent.label.startswith("Please review and acknowledge Cloudflare")
    assert consent.options == ["Acknowledge/Confirm"]
    assert consent.required is True


def test_a_group_heading_is_not_taken_from_a_label_that_labels_a_real_control():
    """Inside a `<fieldset>` the group heading is the label that belongs to NO
    control. Dropping that guard makes the first option's own label the group's
    heading — so a Yes/No question comes back titled "Yes"."""
    html = """
    <fieldset>
      <input type="radio" id="r1" name="auth"><label for="r1">Yes</label>
      <input type="radio" id="r2" name="auth"><label for="r2">No</label>
      <label for="auth-heading">Are you authorized to work?</label>
    </fieldset>
    """
    question = discover_questions(html)[0]
    assert question.label == "Are you authorized to work?"
    assert question.options == ["Yes", "No"]


def test_a_group_heading_does_not_leak_onto_unrelated_controls():
    """Greenhouse wraps a country combobox, a search box and the tel input in
    one `role="group"` labelled "Phone", none of them carrying a `name`. All
    three once came back labelled "Phone"."""
    html = """
    <div role="group" aria-label="Phone">
      <label for="country">Country</label><input id="country" type="text">
      <input id="search" type="text" aria-label="Search">
      <label for="tel">Phone</label><input id="tel" type="tel">
    </div>
    """
    labels = {c.element_id: c.label for c in parse_controls(html)}
    assert labels == {"country": "Country", "search": "Search", "tel": "Phone"}


def test_real_greenhouse_phone_group_labels_stay_distinct(controls):
    by_id = {c.element_id: c.label for c in controls["greenhouse"]}
    assert by_id["country"] == "Country"
    assert by_id["phone"] == "Phone"
    assert by_id["iti-0__search-input"] == "Search"


def test_question_keys_are_unique_per_board(questions):
    for board in BOARDS:
        keys = [q.key for q in questions[board]]
        assert len(keys) == len(set(keys)), board


def test_colliding_keys_get_a_stable_suffix():
    html = (
        '<label for="a">First Comment</label><textarea id="a" name="comment"></textarea>'
        '<label for="b">Second Comment</label><textarea id="b" name="comment"></textarea>'
    )
    keys = [q.key for q in discover_questions(html)]
    assert keys == ["comment", "comment__2"]


# ---------------------------------------------------------------------------
# `required` inference and which signal actually fires
# ---------------------------------------------------------------------------


def test_required_from_the_bare_attribute():
    html = '<label for="x">Email</label><input id="x" type="text" required>'
    control = parse_controls(html)[0]
    assert control.required is True and control.required_source == "required-attr"


def test_required_from_an_empty_string_attribute():
    """Ashby writes `required=""`. HTML boolean attributes are true by presence,
    so a naive truthiness check on the value would read this as optional."""
    html = '<label for="x">Email</label><input id="x" type="text" required="">'
    assert parse_controls(html)[0].required is True


def test_required_from_aria_required():
    html = '<label for="x">Email</label><input id="x" type="text" aria-required="true">'
    control = parse_controls(html)[0]
    assert control.required is True and control.required_source == "aria-required"


def test_aria_required_false_is_not_required():
    html = '<label for="x">Email</label><input id="x" type="text" aria-required="false">'
    assert parse_controls(html)[0].required is False


def test_required_equals_false_is_still_required_because_html_says_so():
    """`required="false"` IS required: HTML boolean attributes are true by
    presence, and every browser treats it that way. Pinning the intent so the
    "obvious fix" of reading the value is not applied by mistake — the aria-
    attribute is the one that takes a real true/false value, and it is handled
    separately above."""
    html = '<label for="x">Email</label><input id="x" type="text" required="false">'
    control = parse_controls(html)[0]
    assert control.required is True
    assert control.required_source == "required-attr"


def test_required_from_a_group_aria_required():
    html = (
        '<div role="group" aria-labelledby="h" aria-required="true">'
        '<div id="h">Resume/CV</div><label for="r">Attach</label><input id="r" type="file">'
        "</div>"
    )
    control = parse_controls(html)[0]
    assert control.required is True and control.required_source == "group-aria-required"


@pytest.mark.parametrize("marker", ["*", "✱", " *", " (required)", " required"])
def test_required_from_a_trailing_label_marker(marker):
    html = f'<label for="x">Email{marker}</label><input id="x" type="text">'
    control = parse_controls(html)[0]
    assert control.required is True, marker
    assert control.required_source == "label-marker"


def test_a_label_that_is_only_a_marker_does_not_prove_required():
    html = '<label for="x">*</label><input id="x" type="text" name="n">'
    # No label survives normalization, so the control falls through to `name`
    # and nothing claims it is required.
    assert parse_controls(html)[0].required is False


def test_required_signal_priority():
    """A bare `required` outranks aria-, which outranks the label marker — so a
    board that writes several does not report a confusing source."""
    html = '<label for="x">Email*</label><input id="x" type="text" required aria-required="true">'
    assert parse_controls(html)[0].required_source == "required-attr"
    html = '<label for="x">Email*</label><input id="x" type="text" aria-required="true">'
    assert parse_controls(html)[0].required_source == "aria-required"


def test_which_required_signals_the_real_fixtures_actually_use(controls):
    """Locks in the measured reality (2026-08-01 captures), because it is not
    what you would guess: **no real board here marks required-ness with a
    trailing label marker that this module ends up using** — Lever and Ashby use
    the `required` attribute, Greenhouse uses `aria-required` (plus one
    `group-aria-required` for Resume/CV and one bare `required` on its consent
    checkbox). The `label-marker` step is exercised only by synthetic HTML, and
    exists for the boards we have not captured."""
    counts = {
        board: dict(collections.Counter(c.required_source for c in controls[board]))
        for board in BOARDS
    }
    assert counts["lever"] == {"required-attr": 54, "label-marker": 1, "": 10}
    assert counts["ashby"] == {"required-attr": 5, "": 14}
    assert counts["greenhouse"] == {
        "aria-required": 9,
        "": 4,
        "group-aria-required": 1,
        "required-attr": 1,
    }


def test_which_label_sources_the_real_fixtures_actually_use(controls):
    counts = {
        board: dict(collections.Counter(c.label_source for c in controls[board]))
        for board in BOARDS
    }
    assert counts["lever"] == {"question-block": 23, "label": 42}
    assert counts["ashby"] == {"label": 14, "label-for-name": 4, "placeholder": 1}
    assert counts["greenhouse"] == {"label": 14, "aria-label": 1}


# ---------------------------------------------------------------------------
# Greenhouse: the DOM path must agree with the Task 2 schema path
# ---------------------------------------------------------------------------


def test_greenhouse_dom_keys_agree_with_the_schema(controls):
    """`greenhouse-questions.json` and `greenhouse-form.html` are the same
    posting (Cloudflare 8077075). Greenhouse renders each field's schema `name`
    as the input's `id`, so every schema question must be locatable by key —
    this is the Greenhouse fallback path, and it is an exact identifier match,
    not prose matching."""
    payload = json.loads((FIXTURES / "greenhouse-questions.json").read_text())
    schema_questions = parse_questions(payload)
    assert schema_questions, "schema fixture yielded no questions"

    unlocatable = []
    for question in schema_questions:
        control = find_by_key(controls["greenhouse"], question.key)
        if control is None or control.selector is None:
            unlocatable.append(question.key)
    assert unlocatable == []


def test_find_by_key_is_exact_and_refuses_ties():
    html = (
        '<label for="a">A</label><input id="a" type="text" name="dup">'
        '<label for="b">B</label><input id="b" type="text" name="dup">'
    )
    parsed = parse_controls(html)
    assert find_by_key(parsed, "dup") is None  # two controls share the name
    assert find_by_key(parsed, "A") is None    # exact, never normalized
    assert find_by_key(parsed, "") is None
    # `a` is an id, but that control declares name="dup" — a control that says
    # what its name is has told us it is not "a".
    assert find_by_key(parsed, "a") is None


def test_find_by_key_uses_id_only_when_the_control_has_no_conflicting_name():
    """Greenhouse's visible inputs carry an `id` and no `name`, so the `id` route
    must work — but it must not override a control that declares a different
    `name`."""
    assert find_by_key(parse_controls('<label for="e">Email</label><input id="e">'), "e") is not None
    html = '<label for="e">Full name</label><input id="e" name="applicant_name">'
    assert find_by_key(parse_controls(html), "e") is None
    assert find_by_key(parse_controls(html), "applicant_name") is not None


def test_find_by_key_is_not_fooled_by_a_filtered_out_control_freeing_its_name():
    """A hidden `<input name="email">` is filtered out of the control list, which
    used to free the key "email" to be answered by an unrelated element's `id` —
    and this is the Greenhouse path, so the address would have gone into the
    full-name box."""
    html = (
        '<input type="hidden" name="email" value="x">'
        '<label for="email">Full name</label><input id="email" name="applicant_name" type="text">'
    )
    assert find_by_key(parse_controls(html), "email") is None


def test_find_by_key_prefers_name_over_id():
    """Greenhouse's schema keys are field `name`s, so `name` is checked first."""
    html = (
        '<label for="x">X</label><input id="x" type="text" name="y">'
        '<label for="y">Y</label><input id="y" type="text" name="z">'
    )
    control = find_by_key(parse_controls(html), "y")
    assert control.element_id == "x" and control.name == "y"


def test_greenhouse_dom_reports_comboboxes_as_text_not_select(questions):
    """Why Greenhouse keeps using its JSON schema for question *shape* and the
    DOM only for locating: its React comboboxes render as `<input type=text>`
    with no `<option>` anywhere, so the DOM cannot see that
    "Do you now or will you in the future require immigration sponsorship" is a
    select with a fixed option list. The schema can."""
    sponsorship = next(
        q for q in questions["greenhouse"] if q.label.startswith("Do you now or will you")
    )
    assert sponsorship.kind == "text"
    assert sponsorship.options == []

    payload = json.loads((FIXTURES / "greenhouse-questions.json").read_text())
    from_schema = next(
        q for q in parse_questions(payload) if q.label.startswith("Do you now or will you")
    )
    assert from_schema.kind == "select"
    assert from_schema.options == ["Yes", "No"]


# ---------------------------------------------------------------------------
# Parser robustness (real board HTML is minified and not well-formed)
# ---------------------------------------------------------------------------


def test_script_and_style_text_never_becomes_a_label():
    """Lever ships 687 KB of inline `<style>` and Ashby 21 KB of `<script>`
    inside the page; a text collector that swallowed those would produce
    enormous nonsense labels."""
    html = (
        "<label for=\"x\">Email<style>.a{content:'STYLE TEXT'}</style>"
        '<script>var s = "SCRIPT TEXT";</script></label><input id="x" type="text">'
    )
    control = parse_controls(html)[0]
    assert control.label == "Email"


def test_stray_and_unclosed_tags_do_not_break_discovery():
    html = '<div></span><p><label for="x">Email</label><input id="x" type="text"><div>'
    assert parse_controls(html)[0].label == "Email"


def test_self_closed_input_is_handled():
    html = '<label><input type="checkbox" name="c" value="Yes" /><span>Yes</span></label>'
    assert parse_controls(html)[0].label == "Yes"


def test_character_references_and_nbsp_are_decoded():
    html = '<label for="x">Email&nbsp;&amp;&nbsp;Phone</label><input id="x" type="text">'
    assert parse_controls(html)[0].label == "Email & Phone"


def test_a_quote_in_a_selector_value_is_escaped():
    html = '<label for="x">L</label><input id="x" type="text" name=\'a"b\'>'
    control = parse_controls(html)[0]
    assert control.selector == '[id="x"]'
    html = '<label>L<input type="text" name=\'a"b\'></label>'
    assert parse_controls(html)[0].selector == 'input[name="a\\"b"]'


def test_a_filtered_out_twin_does_not_free_its_name_for_a_selector():
    """`selector` must resolve to exactly one element IN THE DOCUMENT, not among
    the controls this module chose to report. A hidden twin sharing the `name` is
    invisible to `parse_controls` and perfectly visible to a CSS selector, so
    counting only the survivors declared `input[name="agree"]` unique when it
    matched two elements — on the one code path Task 6 calls directly.

    Same class of bug as `find_by_key`'s: a filtered-out control freeing its
    identifier.
    """
    html = (
        '<input type="hidden" name="agree" value="0">'
        "<label>I agree<input type=\"checkbox\" name=\"agree\" value=\"1\"></label>"
    )
    control = parse_controls(html)[0]
    assert control.selector != 'input[name="agree"]'
    # The `value` half still distinguishes it from the hidden twin.
    assert control.selector == 'input[name="agree"][value="1"]'


@pytest.mark.parametrize(
    "twin",
    [
        '<input type="hidden" name="dup">',
        '<input type="text" name="dup" aria-hidden="true">',
        '<div style="display:none"><input type="text" name="dup"></div>',
        '<template><input type="text" name="dup"></template>',
        '<textarea name="g-recaptcha-response"></textarea><input type="text" name="dup">',
    ],
)
def test_every_filter_reason_still_counts_toward_name_uniqueness(twin):
    """Whatever the reason a control was filtered out, it still occupies its
    `name` as far as a selector is concerned."""
    html = twin + '<label for="v">Visible</label><input id="v" type="text" name="dup">'
    control = find_control(parse_controls(html), "Visible")
    assert control is not None
    # `id` is unique here, so that route wins — the point is that the `name`
    # route must NOT be what answers, since `name="dup"` is not unique.
    assert control.selector == '[id="v"]'
    no_id = twin + "<label>Visible<input type=\"text\" name=\"dup\"></label>"
    hit = find_control(parse_controls(no_id), "Visible")
    if hit is not None:
        assert hit.selector != 'input[name="dup"]'


def test_two_group_members_sharing_name_and_value_get_no_selector():
    """HTML imposes NO requirement that radio/checkbox group members have
    distinct `value`s — an earlier justification for the name+value route claimed
    it does, which is false. So the pair is uniqueness-checked like anything else,
    and deleting that check is not covered by the live `count()==1` guard in
    `PageLocator`, because `Control.selector` and `find_selector` are public and
    bypass it entirely.
    """
    html = (
        '<input type="radio" name="g" value="Yes" style="display:none">'
        "<label>Yes<input type=\"radio\" name=\"g\" value=\"Yes\"></label>"
    )
    assert parse_controls(html)[0].selector is None

    # Two *visible* members sharing name+value: neither is addressable.
    both = (
        "<label>Yes<input type=\"radio\" name=\"g\" value=\"Yes\"></label>"
        "<label>Yes please<input type=\"radio\" name=\"g\" value=\"Yes\"></label>"
    )
    assert [c.selector for c in parse_controls(both)] == [None, None]


def test_the_name_value_route_still_addresses_a_normal_group(controls):
    """The other half of the contract: distinct values ARE addressable, which is
    what makes Lever's 33 language checkboxes and its Yes/No radios actionable."""
    members = locate_dom.find_group_options(
        controls["lever"],
        "Are you legally authorized to work in the country for which you are applying?",
    )
    assert [m.selector for m in members] == [
        'input[name="cards[1c719ca9-5069-4afe-9e82-39ca420e0edb][field0]"][value="Yes"]',
        'input[name="cards[1c719ca9-5069-4afe-9e82-39ca420e0edb][field0]"][value="No"]',
    ]


@pytest.mark.parametrize("board", BOARDS)
def test_no_selector_in_a_real_fixture_matches_two_elements(board, controls):
    """The invariant `_selector_for` promises, checked against the real captures by
    re-parsing and counting matches by hand — no browser needed. It happens to
    hold in all three today, which is why the hidden-twin bug was latent rather
    than live."""
    html = _html(board)
    for control in controls[board]:
        if control.selector is None:
            continue
        if control.selector.startswith("[id="):
            wanted = control.element_id
            hits = len(re.findall(r'\bid="' + re.escape(wanted) + r'"', html))
        else:
            wanted = control.name
            hits = len(re.findall(r'\bname="' + re.escape(wanted) + r'"', html))
            if "[value=" in control.selector:
                continue  # name+value pairs are checked by construction above
        assert hits == 1, (control.selector, hits)


@pytest.mark.parametrize("value", ["a\nb", "a\tb", "a\x00b", "a>>b", "a\x1fb"])
def test_a_value_that_cannot_be_safely_embedded_yields_no_selector(value):
    """A raw control character is a CSS string parse error and `>>` is
    Playwright's own selector-chaining operator — either could make one selector
    address a different element. `None` ("the human fills this one") is the safe
    failure; handing out a broken selector is not."""
    control = parse_controls(f'<label>L<input type="text" name="{value}"></label>')[0]
    assert control.selector is None


def test_deep_nesting_does_not_raise():
    """`html.parser` never auto-closes, so 4000 unclosed `<li>`s nest 4000 deep.
    A recursive tree walk raised RecursionError out of `parse_controls`, turning a
    malformed page into a crash instead of "nothing locatable"."""
    html = "<li>" * 4000 + '<label for="x">Email</label><input id="x" type="text">'
    assert len(parse_controls(html)) == 1
    html = "<div>" * 3000 + '<label for="x">Email</label><input id="x">' + "</div>" * 3000
    assert len(parse_controls(html)) == 1


def test_lever_bracketed_names_survive_into_a_selector(controls):
    """Lever names its URL fields `urls[LinkedIn]`. Brackets are legal inside a
    quoted CSS attribute value, but only if the value stays quoted."""
    control = find_control(controls["lever"], "LinkedIn URL")
    assert control is not None
    assert control.selector == 'input[name="urls[LinkedIn]"]'


@pytest.mark.parametrize("board", BOARDS)
def test_discovery_finds_a_plausible_number_of_questions(board, questions):
    counts = {"lever": 29, "ashby": 16, "greenhouse": 15}
    assert len(questions[board]) == counts[board]


@pytest.mark.parametrize("board", BOARDS)
def test_no_question_is_lost_between_the_three_buckets(board):
    """`discover_questions` + `unreadable_questions` + `excluded_eeo_questions`
    must partition the form. A question that appears in none of them has
    silently vanished, and a handoff report built on these counts would be a
    lie."""
    html = _html(board)
    answerable = discover_questions(html)
    unreadable = locate_dom.unreadable_questions(html)
    withheld = locate_dom.excluded_eeo_questions(html)
    total = {"lever": 29, "ashby": 16, "greenhouse": 15}[board]
    assert len(answerable) + len(unreadable) + len(withheld) == total
    keys = [q.key for q in answerable + unreadable + withheld]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("board", BOARDS)
def test_no_real_fixture_has_an_unreadable_question(board):
    """Was 5 on Lever (the 33-checkbox language group and four yes/no radio
    pairs) before the containment rule. All three boards are now fully readable,
    which is the point: `unreadable_questions` should be an escape hatch that
    real forms do not need, not a bucket a third of the form falls into."""
    assert locate_dom.unreadable_questions(_html(board)) == []


# ---------------------------------------------------------------------------
# EEO screening — the same backstop the schema path applies
# ---------------------------------------------------------------------------


def test_an_eeo_question_is_withheld_from_discovery():
    """Greenhouse hands its demographic questions over in a separate array that
    Task 2 simply never reads. Lever and Ashby have no such separation — one
    DOM, everything in it — so the label screen is all that stands between a
    protected-characteristic question and the resolver."""
    html = (
        '<label for="a">Email</label><input id="a" type="text">'
        '<label for="b">What is your gender?</label><input id="b" type="text">'
        '<label for="c">Are you a protected veteran?</label><input id="c" type="text">'
    )
    assert [q.label for q in discover_questions(html)] == ["Email"]


def test_a_withheld_eeo_question_is_still_retrievable():
    """Withheld, not vanished: a caller must be able to say "N questions were
    withheld" — it just must never hand them to a resolver."""
    html = (
        '<label for="a">Email</label><input id="a" type="text">'
        '<label for="b">What is your gender?</label><input id="b" type="text">'
    )
    withheld = locate_dom.excluded_eeo_questions(html)
    assert [q.label for q in withheld] == ["What is your gender?"]
    assert withheld[0].kind == "text"  # parsed the same way, not a stub


def test_the_eeo_screen_is_shared_with_the_schema_path_not_reimplemented():
    """Two copies of the term list would drift, and the copy that drifted would
    be the one that let a question through."""
    source = pathlib.Path(locate_dom.__file__).read_text()
    assert "is_eeo_label" in source

    # No EEO term may appear ANYWHERE INSIDE a non-docstring string literal in
    # this module. Two details, both of which this test got wrong before:
    #
    #   * SUBSTRING, not set membership. The old version collected literals into
    #     a set and asserted `term not in literals`, which only ever caught a
    #     literal spelled exactly "veteran" — adding
    #     `("veteran status", "disability status", "your pronouns")` left the
    #     suite green while re-implementing the very list it was guarding.
    #   * docstrings excluded, which is what makes substring matching usable at
    #     all: `discover_questions`' own docstring lists these words on purpose.
    #
    # The terms come from `schema_greenhouse` itself rather than a hand-copy, so
    # this cannot drift from the list it is protecting either.
    literals = [text.lower() for text in _code_string_literals(pathlib.Path(locate_dom.__file__))]
    for term in schema_greenhouse._EEO_TERMS:
        for text in literals:
            assert term not in text, (
                f"the EEO term list must live in schema_greenhouse only; "
                f"found {term!r} inside the literal {text!r}"
            )
    assert schema_greenhouse.is_eeo_label("What is your gender?") is True
    assert schema_greenhouse.is_eeo_label("Milwaukee") is False


def test_a_label_less_eeo_question_cannot_slip_past_the_label_screen():
    """The hole this closes: the EEO screen matches on `label`, so a question
    with NO label cannot be screened at all — and Lever produces exactly that
    shape, with the title in a sibling `<div class="application-label">` that
    decision 1 forbids reading. A label-less "Male / Female / Decline to
    self-identify" group therefore matched no EEO term and sat in the answerable
    list while the withheld count said zero. Unlabelled questions are now
    excluded from `discover_questions` outright."""
    lever_shape = (
        '<li><div><div class="application-label"><div class="text">Gender</div></div>'
        '<div class="application-field"><ul>'
        '<li><label><input type="radio" name="g" value="Male"><span>Male</span></label></li>'
        '<li><label><input type="radio" name="g" value="Female"><span>Female</span></label></li>'
        '<li><label><input type="radio" name="g" value="x"><span>Decline to self-identify</span></label></li>'
        "</ul></div></div></li>"
    )
    assert discover_questions(lever_shape) == []
    unreadable = locate_dom.unreadable_questions(lever_shape)
    assert len(unreadable) == 1
    assert unreadable[0].label == ""
    assert unreadable[0].options == ["Male", "Female", "Decline to self-identify"]


def test_no_answerable_question_ever_has_an_empty_label():
    """The invariant that makes the label-based EEO screen meaningful: if a
    question is in the answerable list, there was a label to screen."""
    for board in BOARDS:
        for question in discover_questions(_html(board)):
            assert normalize_label(question.label), (board, question.key)


def test_the_eeo_screen_is_still_word_boundary_aware():
    """The shared screen uses `\\b` boundaries, so a benign label containing
    "Essex" or "embrace" is not withheld. Verifying it here too, because this
    module now depends on that property."""
    html = (
        '<label for="a">Which Essex office do you prefer?</label><input id="a" type="text">'
        '<label for="b">Do you embrace ambiguity?</label><input id="b" type="text">'
    )
    assert len(discover_questions(html)) == 2
    assert locate_dom.excluded_eeo_questions(html) == []


@pytest.mark.parametrize("board", BOARDS)
def test_no_real_fixture_leaks_an_eeo_question(board):
    """Measured: none of the three captured forms asks a protected-characteristic
    question in its main body, so the screen withholds nothing here. Asserting
    it anyway means a re-capture that DOES include one gets noticed."""
    assert locate_dom.excluded_eeo_questions(_html(board)) == []


# ---------------------------------------------------------------------------
# End-to-end with Task 3's resolver — still pure, still no browser
# ---------------------------------------------------------------------------

_FAKE_PROFILE = {
    "full_name": "Test Applicant",
    "email": "applicant@example.invalid",
    "phone": "+1 555 0100",
    "location": "San Francisco, CA",
    "work_auth": "citizen",
    "needs_sponsorship": False,
}


def test_the_lever_blocking_questions_reach_the_resolvers_blocking_list():
    """The reason the containment rule matters, stated as an outcome rather than
    a label: work authorization and sponsorship must arrive at
    `resolver.blocking()` so Task 7's handoff can refuse to call the form ready.
    While they had `label=""` they were unclassifiable and simply absent."""
    from agents.job_applier import resolver

    questions = discover_questions(_html("lever"))
    blocking = resolver.blocking(resolver.resolve(questions, _FAKE_PROFILE))
    labels = [a.question.label for a in blocking]
    assert any("legally authorized to work" in label for label in labels)
    assert any("require sponsorship for employment visa status" in label for label in labels)


@pytest.mark.parametrize("board", BOARDS)
def test_discovered_questions_are_resolvable_without_error(board):
    """The DOM path and the schema path must hand the resolver the same shape."""
    from agents.job_applier import resolver

    answers = resolver.resolve(discover_questions(_html(board)), _FAKE_PROFILE)
    assert len(answers) == len(discover_questions(_html(board)))


# ---------------------------------------------------------------------------
# The Playwright adapter, exercised with a stub Page (still no browser)
# ---------------------------------------------------------------------------


class _StubLocator:
    def __init__(self, selector: str, matches: int) -> None:
        self.selector = selector
        self._matches = matches

    def count(self) -> int:
        return self._matches


class _StubPage:
    """Exposes exactly `content()` and `locator()`. Anything the adapter tries
    to call beyond that — `fill`, `click`, `set_input_files` — raises
    AttributeError, so the test fails loudly rather than silently acting on a
    page."""

    def __init__(self, html: str, matches: int = 1) -> None:
        self._html = html
        self._matches = matches
        self.content_calls = 0
        self.requested: list[str] = []

    def content(self) -> str:
        self.content_calls += 1
        return self._html

    def locator(self, selector: str) -> _StubLocator:
        self.requested.append(selector)
        return _StubLocator(selector, self._matches)


def test_page_locator_returns_a_locator_for_a_matched_label():
    page = _StubPage(_html("ashby"))
    locator = PageLocator(page).locator_for_label("Email")
    assert locator is not None
    assert page.requested == ['[id="_systemfield_email"]']


def test_page_locator_returns_none_for_an_unmatched_label():
    page = _StubPage(_html("ashby"))
    assert PageLocator(page).locator_for_label("Favourite Dinosaur") is None
    assert page.requested == []


def test_page_locator_refuses_a_selector_matching_two_live_elements():
    """Belt and braces: even a selector the pure layer thought was unique is
    re-checked against the live page, and two hits is a miss, not "take the
    first"."""
    page = _StubPage(_html("ashby"), matches=2)
    assert PageLocator(page).locator_for_label("Email") is None


def test_page_locator_by_key_is_the_greenhouse_path():
    page = _StubPage(_html("greenhouse"))
    locator = PageLocator(page).locator_for_key("first_name")
    assert locator is not None
    assert page.requested == ['[id="first_name"]']


def test_page_locator_caches_the_dom_until_refreshed():
    """These forms mount fields progressively, so the caller controls when to
    re-read — but a single lookup must not re-serialize a 1.9 MB DOM per query."""
    page = _StubPage(_html("greenhouse"))
    locator = PageLocator(page)
    locator.locator_for_label("Email")
    locator.locator_for_label("Phone")
    assert page.content_calls == 1
    locator.refresh()
    assert page.content_calls == 2


def test_page_locator_questions_reads_the_live_dom():
    page = _StubPage(_html("greenhouse"))
    found = PageLocator(page).questions()
    assert [q.key for q in found][:3] == ["first_name", "last_name", "email"]


class _MutatingStubPage(_StubPage):
    """A page whose DOM CHANGES between reads — which is what a progressively
    mounting Ashby form actually does."""

    def __init__(self, first: str, second: str) -> None:
        super().__init__(first)
        self._second = second

    def content(self) -> str:
        html = super().content()
        self._html = self._second
        return html


def test_every_accessor_uses_one_snapshot():
    """`questions()` used to call `page.content()` itself, so `controls` and
    `questions()` could describe two DIFFERENT DOMs of the same form — and a
    caller would then act on selectors from one snapshot using questions from
    another. Correctness, not performance."""
    first = '<label for="a">Email</label><input id="a" type="text">'
    second = (
        '<label for="a">Email</label><input id="a" type="text">'
        '<label for="b">Phone</label><input id="b" type="text">'
    )
    page = _MutatingStubPage(first, second)
    locator = PageLocator(page)
    assert [c.label for c in locator.controls] == ["Email"]
    assert [q.label for q in locator.questions()] == ["Email"], "must not re-read the page"
    assert page.content_calls == 1
    # ...and refresh() is the only way to see the newer DOM.
    locator.refresh()
    assert [q.label for q in locator.questions()] == ["Email", "Phone"]


def test_all_three_buckets_share_a_single_parse():
    """Task 7 wants answerable + unreadable + withheld together. One
    `page.content()` for all three, and they still partition the form."""
    page = _StubPage(_html("lever"))
    locator = PageLocator(page)
    answerable = locator.questions()
    unreadable = locator.unreadable()
    withheld = locator.withheld_eeo()
    assert page.content_calls == 1
    assert len(answerable) == 29
    assert unreadable == [] and withheld == []
    keys = [q.key for q in answerable + unreadable + withheld]
    assert len(keys) == len(set(keys))


def test_the_adapter_buckets_agree_with_the_module_level_functions():
    """The cached path must not become a second, drifting implementation."""
    html = _html("lever")
    locator = PageLocator(_StubPage(html))
    assert locator.questions() == discover_questions(html)
    assert locator.unreadable() == locate_dom.unreadable_questions(html)
    assert locator.withheld_eeo() == locate_dom.excluded_eeo_questions(html)


def test_refresh_invalidates_the_question_cache_too():
    page = _StubPage(_html("greenhouse"))
    locator = PageLocator(page)
    locator.questions()
    assert page.content_calls == 1
    locator.refresh()
    locator.questions()
    assert page.content_calls == 2


# ---------------------------------------------------------------------------
# Form sections
# ---------------------------------------------------------------------------
# Added for Task 5. `Question.section` is a coarser grouping than `group_label`
# — several unrelated questions under one heading — and it exists for two
# consumers: `drafting.not_prose_reason` needs it as evidence (Lever states
# "submit a URL to an unlisted YouTube video" ONLY in the section heading, never
# in the two textarea labels it governs) and Task 7's handoff report groups what
# it shows the human by it. Measured values are pinned the way the question
# counts above are, so a fixture recapture or a parser change surfaces here.

# Section heading -> number of answerable questions under it, in the real
# captured Lever form. Measured 2026-08-01.
_LEVER_SECTIONS = {
    "Submit your application": 6,
    "Links": 3,
    "Supplementary Questions": 3,
    "Work Authorization": 2,
    "High School Name & Graduation Year": 2,
    "University": 1,
    "How did you hear about this internship opportunity?": 1,
    "Year of Graduation": 1,
    "Month of Graduation": 1,
    "An Inflection Point": 2,
    "Additional Questions": 2,
    "AU Clearance Confirmation": 2,
    "Additional information": 1,
}
_LEVER_VIDEO_SECTION_PREFIX = (
    "Video Prompts: After recording your clips, please submit a URL to an "
    "unlisted YouTube video"
)


def test_lever_questions_all_carry_their_section_heading(questions):
    """Lever wraps each card in `div.section` with an `<h4>` heading, so every
    question on that form has one, read through a CONTAINMENT relationship.

    On THIS fixture the positional alternative ("nearest preceding heading in
    document order") produces the identical answer — measured, 0 of 65 controls
    differ — so Lever is not what justifies containment. Ashby and Greenhouse are:
    see `test_what_the_positional_section_rule_would_have_produced`, where the
    positional rule files 19 of Ashby's 20 fields under a widget's title and all 15
    of Greenhouse's under the form's own H2. An earlier version of this docstring
    claimed the positional rule would stamp Ashby's `<h3>WHAT WE EXPECT :` onto the
    whole form; that was false and is corrected in `locate_dom`."""
    counts = collections.Counter(q.section for q in questions["lever"])
    video = [s for s in counts if s.startswith(_LEVER_VIDEO_SECTION_PREFIX)]
    assert len(video) == 1, "the video-prompt section heading is read verbatim"
    assert counts.pop(video[0]) == 2, "and it covers exactly the two prompts"
    assert dict(counts) == _LEVER_SECTIONS
    assert all(q.section for q in questions["lever"])


@pytest.mark.parametrize("board", ("ashby", "greenhouse"))
def test_a_board_with_no_section_heading_reports_no_section(board, questions):
    """Not a gap to be filled with a guess. Greenhouse groups nothing at all, and
    Ashby's one form section is
    `_section_5yu8i_86 ashby-application-form-section-container`.

    That class name fails the container test for a reason this assertion alone
    cannot distinguish from "it has no heading anyway" — both are true of Ashby.
    So each is pinned separately below:
    `test_a_class_containing_section_as_a_substring_is_not_a_section` covers the
    token match, `test_a_section_with_no_heading_reports_no_section` covers the
    missing heading. Without those two, a substring class match
    (`"section" in class`) passes this test unchanged."""
    assert [q.section for q in questions[board]] == [""] * len(questions[board])


def test_the_greenhouse_schema_path_reports_no_section():
    """Greenhouse's JSON carries `label`, `required`, `fields` and an optional
    per-question `description`, but nothing grouping questions under a shared
    heading — so `parse_questions` must not invent one."""
    payload = json.loads((FIXTURES / "greenhouse-questions.json").read_text())
    parsed = parse_questions(payload)
    assert parsed, "premise: the payload has questions"
    assert all(q.section == "" for q in parsed)


def test_section_is_optional_so_every_existing_construction_site_still_works():
    """The field was added strictly additively. A `Question` built without it —
    which is every call site outside `locate_dom` — must still be constructible
    and must report "" rather than raising."""
    assert Question(key="k", label="L", required=False, kind="text").section == ""
    assert Control(
        tag="input", input_type="text", label="L", label_source="label",
        kind="text", required=False, required_source="",
    ).section == ""


def test_a_nested_section_heading_does_not_leak_up_to_its_parent():
    """A heading inside a nested section describes THAT section. Inheriting it
    upward would give the outer section a heading that belongs to part of it."""
    html = (
        '<div class="section"><div class="section"><h4>Inner</h4>'
        '<input id="a" type="text"><label for="a">A</label></div>'
        '<input id="b" type="text"><label for="b">B</label></div>'
    )
    found = {c.label: c.section for c in parse_controls(html)}
    assert found == {"A": "Inner", "B": ""}


def test_the_nearest_section_wins_when_both_have_headings():
    html = (
        '<section><h2>Outer</h2><section><h3>Inner</h3>'
        '<input id="a" type="text"><label for="a">A</label></section>'
        '<input id="b" type="text"><label for="b">B</label></section>'
    )
    found = {c.label: c.section for c in parse_controls(html)}
    assert found == {"A": "Inner", "B": "Outer"}


def test_a_section_heading_is_cleaned_of_its_required_marker():
    html = ('<section><h3>Video Prompts ✱</h3>'
            '<input id="a" type="text"><label for="a">A</label></section>')
    assert parse_controls(html)[0].section == "Video Prompts"


def test_a_class_containing_section_as_a_substring_is_not_a_section():
    """`_is_section` uses `has_class`, a whitespace-TOKEN match. A substring test
    (`"section" in node.attr("class")`) passes every existing test in this file —
    including the Ashby/Greenhouse assertions, since neither has a heading to pick
    up either way — while making `sectional`, `subsection` and Ashby's own
    `ashby-application-form-section-container` all count as sections. Ashby then
    stops reporting "" for the reason the docstring claims."""
    for klass in ("sectional", "subsection", "form-section-container",
                  "ashby-application-form-section-container", "section-header"):
        html = (f'<div class="{klass}"><h4>Heading</h4>'
                '<input id="a" type="text"><label for="a">A</label></div>')
        assert parse_controls(html)[0].section == "", klass
    # And the token form still is one, so this is not just "nothing matches".
    html = ('<div class="page section wide"><h4>Heading</h4>'
            '<input id="a" type="text"><label for="a">A</label></div>')
    assert parse_controls(html)[0].section == "Heading"


def test_a_section_with_no_heading_reports_no_section():
    """The other half of the Ashby claim, pinned independently: a real section
    container that simply has no heading element yields "" rather than reaching
    outward for one."""
    html = ('<div class="section"><div>not a heading</div>'
            '<input id="a" type="text"><label for="a">A</label></div>')
    assert parse_controls(html)[0].section == ""


def test_a_section_takes_its_FIRST_own_heading_not_its_last():
    """`_own_heading_text` documents "first"; returning the last passed every test,
    because no case had two own headings in one section. Real forms do: a card with
    a title and a sub-title, or a legend followed by a note. The first is the one a
    human reads as the section's name, and for drafting it is the one carrying the
    "submit a URL" instruction — a later sub-heading would displace it."""
    html = (
        '<div class="section"><h4>Video Prompts: submit a URL</h4>'
        '<h5>Prompt guidance</h5>'
        '<input id="a" type="text"><label for="a">A</label></div>'
    )
    assert parse_controls(html)[0].section == "Video Prompts: submit a URL"


def test_a_control_outside_every_section_has_no_section():
    """The concrete divergence from the positional rule, which no captured fixture
    happens to contain: "nearest preceding heading in document order" hands this
    control the heading of a section it is not in. With "Video Prompts" as that
    heading, drafting would refuse a perfectly draftable question."""
    html = (
        '<div class="section"><h4>Video Prompts: submit a URL</h4>'
        '<textarea id="a"></textarea><label for="a">Prompt 1</label></div>'
        '<textarea id="b"></textarea><label for="b">Anything else?</label>'
    )
    found = {c.label: c.section for c in parse_controls(html)}
    assert found == {"Prompt 1": "Video Prompts: submit a URL", "Anything else?": ""}


# What "nearest preceding heading in document order" actually produces on each
# captured fixture, measured. Lever agrees with containment exactly; the other two
# do not, and what they produce is not a section heading at all.
_POSITIONAL_RULE_MEASURED = {
    "lever": None,  # agrees with containment on all 65 controls
    # 19 of 20 fields would be filed under a WIDGET's title ("Autofill from
    # resume"), and one under job-posting metadata from the page header.
    "ashby": {"Autofill from resume": 19, "Department": 1},
    # Every field under the form's own H2 — which is the whole form, not a section.
    "greenhouse": {"Apply for this job": 15},
}


def _positional_sections(board: str) -> list[str]:
    """The section each control would get under the positional rule. Local to this
    test so the rule being rejected is not implemented in the module."""
    root = locate_dom._parse(_html(board))
    nodes = list(root.descendants())
    order = {id(n): i for i, n in enumerate(nodes)}
    headings = [
        (order[id(n)], locate_dom._clean_label(locate_dom._text_of(n)))
        for n in nodes
        if n.tag in locate_dom._SECTION_HEADING_TAGS and locate_dom._text_of(n).strip()
    ]
    out = []
    for node in nodes:
        if not locate_dom._is_fillable(node):
            continue
        best = ""
        for pos, text in headings:
            if pos < order[id(node)]:
                best = text
        out.append(best)
    return out


@pytest.mark.parametrize("board", BOARDS)
def test_what_the_positional_section_rule_would_have_produced(board):
    """The measured justification for reading sections by CONTAINMENT, replacing a
    justification that was simply false (see the test below).

    On LEVER the two rules agree on every one of the 65 controls — so a fixture
    comparison alone does not choose between them, and it is worth saying so rather
    than implying Lever settles it. On the other two boards the positional rule
    attaches a heading that is not a section heading at all: a widget's title
    ("Autofill from resume") to 19 of Ashby's 20 fields, and the form's own H2
    ("Apply for this job") to all 15 of Greenhouse's. Task 7 would then group every
    Greenhouse question under one meaningless heading, and drafting would treat a
    widget title as evidence about what a box wants.
    """
    positional = _positional_sections(board)
    containment = [c.section for c in parse_controls(_html(board))]
    assert len(positional) >= len(containment) > 0

    expected = _POSITIONAL_RULE_MEASURED[board]
    if expected is None:
        assert positional == _positional_sections(board)
        assert collections.Counter(positional) == collections.Counter(
            locate_dom._section_heading(n)
            for n in locate_dom._parse(_html(board)).descendants()
            if locate_dom._is_fillable(n)
        ), "lever: the two rules agree, so this fixture does not choose between them"
    else:
        assert dict(collections.Counter(positional)) == expected
        assert set(containment) == {""}, "containment reports no section, correctly"


def test_ashby_has_no_what_we_expect_heading_in_its_parsed_markup():
    """Pins the correction. The prose here and in `locate_dom` used to justify the
    containment rule by claiming Ashby's `<h3>WHAT WE EXPECT :` would be stamped on
    the whole form. It would not: that string lives only inside `<script>` payloads,
    which `html.parser` hands over as CDATA text, so it is never a node. A false
    justification gets checked, found false, and the logic it defends gets removed."""
    root = locate_dom._parse(_html("ashby"))
    headings = [
        locate_dom._clean_label(locate_dom._text_of(n))
        for n in root.descendants()
        if n.tag in locate_dom._SECTION_HEADING_TAGS
    ]
    assert headings == [
        "Software Engineer Intern - Berlin (2026)",
        "Location", "Employment Type", "Department", "Autofill from resume",
    ]
    assert "WHAT WE EXPECT" in _html("ashby"), "it IS in the file — inside <script>"


# ===========================================================================
# Task 6: the fill executor
# ===========================================================================
# The first code in this project that TYPES into anything. Everything above
# only reads, so the guards below are the ones that actually have teeth.
#
# HERMETIC: no browser and no network. Every test drives `PageLocator` (or the
# executor directly) with the `_FormPage` stub, exactly the way the
# `PageLocator` tests above drive `_StubPage`. The one real headed-Chromium run
# that this task required was a manual, one-off prerequisite probe against a
# `file://` copy of the Lever fixture; its findings are encoded here as
# assertions (see `test_the_fakepath_form_of_a_filename_read_back_is_accepted`)
# rather than repeated at test time.

from agents.job_applier import drafting, resolver  # noqa: E402
from agents.job_applier.nodes import fill as fill_mod  # noqa: E402
from agents.job_applier.nodes.fill import (  # noqa: E402
    ATTACHED,
    FillOutcome,
    BLANK,
    CHANGED,
    FILLED,
    MAX_ATTEMPTS,
    attach_resume,
    fill_form,
    fill_one,
    find_resume_input,
    is_typeable,
)
from agents.job_applier.resolver import BLOCKING_KINDS, Answer  # noqa: E402

FILL_MODULE = pathlib.Path(fill_mod.__file__)


# --------------------------------------------------------------------------
# A page stub that can be written to, and records every write
# --------------------------------------------------------------------------


class _Element:
    """One fake form element.

    `swallow` is the whole reason this stub exists: a React-controlled input
    that accepts a programmatic write and then silently throws it away is the
    failure mode the read-back was built for, and it cannot be reproduced with
    a stub that just stores whatever it is given.
    """

    def __init__(
        self,
        *,
        value: str = "",
        count: int = 1,
        visible: bool = True,
        swallow: bool = False,
        swallow_typing: bool = False,
        transform=None,
        raises: Exception | None = None,
        fakepath: bool = False,
        no_file_api: bool = False,
        option_values: dict[str, str] | None = None,
        reverts: bool = False,
        live_tag: str | None = None,
        live_type: str | None = None,
        no_shape_api: bool = False,
        vanishes: bool = False,
    ) -> None:
        # What the LIVE element reports for `tagName` / the `type` attribute,
        # overriding whatever the snapshot HTML said. `None` means "the live DOM
        # agrees with the snapshot", which is the case for every test that is not
        # about a re-mount. `no_shape_api=True` models a driver whose `evaluate`
        # cannot answer at all — a *don't know*, which must not be read as "this
        # is not a radio". See `fill._live_choice_refusal`.
        self.live_tag = live_tag
        self.live_type = live_type
        self.no_shape_api = no_shape_api
        # Greenhouse REMOVES the hidden <input type=file> once a file is
        # attached and renders the name as a chip instead. Measured live
        # 2026-08-05: set_input_files succeeds, then every read of that node
        # times out because it is gone.
        self.vanishes = vanishes
        self.vanished = False
        self.value = value
        # `reverts` models a React-controlled input that re-renders from its own
        # state after ANY write, including the clearing one — so the field snaps
        # back to what it already held rather than going empty. `swallow` is the
        # weaker version (the value-write is dropped, a clear still lands), and
        # the two produce genuinely different outcomes, which is the point.
        self.reverts = reverts
        self._initial = value
        # A `<select>`'s option `value` ATTRIBUTE, which on real boards differs
        # from the option's visible label ("4021" vs "Bachelor's Degree"). Kept
        # separate from `self.value` so `input_value()` and the
        # `selectedOptions[0].label` read return DIFFERENT things — without that,
        # the two code paths are indistinguishable and the test that exists to
        # prove the label path is used cannot fail.
        self.option_values = dict(option_values or {})
        self.value_attribute: str | None = None
        self.count = count
        self.visible = visible
        self.swallow = swallow
        self.swallow_typing = swallow_typing
        self.transform = transform
        self.raises = raises
        self.checked = False
        self.files: list[str] = []
        self.fakepath = fakepath
        self.no_file_api = no_file_api


class _FormLocator:
    def __init__(self, page: "_FormPage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def _el(self) -> _Element:
        return self._page.element(self._selector)

    def _record(self, method: str, arg) -> None:
        self._page.writes.append((method, self._selector, arg))

    # -- reads ------------------------------------------------------------
    def count(self) -> int:
        return self._el.count

    def is_visible(self, timeout: int | None = None) -> bool:
        self._page.visibility_checks.append(self._selector)
        self._page.read_timeouts.append(("is_visible", timeout))
        return self._el.visible

    def input_value(self, timeout: int | None = None) -> str:
        self._page.read_timeouts.append(("input_value", timeout))
        el = self._el
        if el.vanished:
            raise TimeoutError("locator.input_value: element is not attached")
        if el.files:
            # Measured on real headed Chromium: a file input's `value` is the
            # spec's deliberate fake path, Windows separator and all, on macOS.
            return f"C:\\fakepath\\{el.files[0]}" if el.fakepath else el.files[0]
        if el.value_attribute is not None:
            return el.value_attribute
        return el.value

    def is_checked(self, timeout: int | None = None) -> bool:
        self._page.read_timeouts.append(("is_checked", timeout))
        return self._el.checked

    def evaluate(self, script: str, arg=None, timeout: int | None = None):
        self._page.read_timeouts.append(("evaluate", timeout))
        el = self._el
        if el.vanished:
            raise TimeoutError("locator.evaluate: element is not attached")
        if "tagName" in script:
            # The LIVE shape. Defaults to what the snapshot HTML says for this
            # selector, so the overwhelming majority of tests — the ones where
            # nothing re-mounted — need configure nothing; `live_tag`/`live_type`
            # simulate a page that changed under the agent.
            if el.no_shape_api:
                raise RuntimeError("this driver's evaluate cannot read tagName")
            tag, input_type = self._page.snapshot_shape(self._selector)
            if el.live_tag is not None:
                tag = el.live_tag
            if el.live_type is not None:
                input_type = el.live_type
            return f"{tag.upper()}|{input_type}"
        if "selectedOptions" in script:
            # The option's LABEL — deliberately not the same string
            # `input_value()` returns when `option_values` is configured.
            return el.value
        if "files" in script:
            if el.no_file_api:
                raise RuntimeError("no File API in this stub")
            return el.files[0] if el.files else ""
        raise AssertionError(f"unexpected evaluate: {script}")

    # -- writes -----------------------------------------------------------
    def fill(self, value: str, timeout: int | None = None) -> None:
        self._record("fill", value)
        el = self._el
        if el.raises:
            raise el.raises
        if el.reverts:
            el.value = el._initial
            return
        if el.swallow and value:
            return
        el.value = el.transform(value) if (el.transform and value) else value

    def press_sequentially(self, value: str, delay: int = 0, timeout: int | None = None) -> None:
        self._record("press_sequentially", value)
        el = self._el
        if el.raises:
            raise el.raises
        if el.reverts:
            el.value = el._initial
            return
        if el.swallow_typing:
            return
        el.value = el.transform(value) if el.transform else value

    def select_option(self, label: str | None = None, timeout: int | None = None) -> None:
        self._record("select_option", label)
        el = self._el
        if el.raises:
            raise el.raises
        if el.swallow:
            return
        el.value = label or ""
        el.value_attribute = el.option_values.get(el.value)

    def check(self, timeout: int | None = None) -> None:
        self._record("check", True)
        el = self._el
        if el.raises:
            raise el.raises
        if not el.swallow:
            el.checked = True

    def set_input_files(self, path: str, timeout: int | None = None) -> None:
        self._record("set_input_files", path)
        el = self._el
        if el.raises:
            raise el.raises
        if el.swallow:
            return
        el.files = [pathlib.Path(path).name]
        if el.vanishes:
            el.vanished = True
            self._page.rendered_chips.append(el.files[0])


class _FormPage:
    """A writable page stub. Selectors are auto-registered on first use, so a
    test only configures the elements it cares about."""

    def __init__(self, html: str, **elements: _Element) -> None:
        self._html = html
        self._elements: dict[str, _Element] = dict(elements)
        self._shapes: dict[str, tuple[str, str]] | None = None
        self.writes: list[tuple[str, str, object]] = []
        self.visibility_checks: list[str] = []
        self.read_timeouts: list[tuple[str, int | None]] = []
        self.content_calls = 0
        #: Filenames the board DISPLAYS after removing the input on upload.
        self.rendered_chips: list[str] = []

    def element(self, selector: str) -> _Element:
        return self._elements.setdefault(selector, _Element())

    def snapshot_shape(self, selector: str) -> tuple[str, str]:
        """`(tag, input_type)` for `selector` according to the snapshot HTML.

        Parsed LAZILY — the Lever fixture is 687 KB and most tests never take the
        code path that asks. This is what makes the stub's default behaviour
        "the live DOM agrees with the snapshot" rather than "the live DOM is
        blank", so a test only has to say something when it wants a disagreement.
        """
        if self._shapes is None:
            self._shapes = {
                c.selector: (c.tag, c.input_type)
                for c in parse_controls(self._html) if c.selector
            }
        return self._shapes.get(selector, ("", ""))

    def content(self) -> str:
        """Snapshot HTML, plus any filename the board now displays.

        Greenhouse removes the file input on upload and renders the name
        instead, so that rendered name is the only evidence left that the
        attach worked.
        """
        self.content_calls += 1
        chips = "".join(f'<span class="chip">{c}</span>' for c in self.rendered_chips)
        return self._html + chips

    def locator(self, selector: str) -> _FormLocator:
        return _FormLocator(self, selector)

    @property
    def methods(self) -> list[str]:
        return [m for m, _, _ in self.writes]


def _q(label: str, *, key: str = "", kind: str = "text", options=None, required=False):
    return Question(
        key=key or label.lower().replace(" ", "_"),
        label=label, required=required, kind=kind, options=list(options or []),
    )


def _answer(label: str, value: str, *, kind: str = "text", source: str = "profile",
            akind: str = "other", options=None) -> Answer:
    return Answer(
        question=_q(label, kind=kind, options=options),
        value=value, source=source, note="", kind=akind,
    )


# --------------------------------------------------------------------------
# THE ONE RULE — the source guard, proved in both directions
# --------------------------------------------------------------------------
# The read-only guard above (`test_module_has_no_mutating_call`) cannot cover
# this module: it legitimately calls `.fill(` and `set_input_files(`. So the
# guard for the executor is a DIFFERENT one — filling is permitted, clicking is
# forbidden — and both halves of that distinction are asserted.

_SUBMIT_RE = re.compile(r"submit|apply\s*now|send\s+application", re.IGNORECASE)

# Every Playwright method that can dispatch a click or a bare keystroke.
# `press` is here and `press_sequentially` is NOT: `press("Enter")` in a text
# input triggers HTML's implicit form submission, while `press_sequentially` is
# the executor's legitimate character-by-character typing retry. AST attribute
# equality keeps the two apart; a substring grep would not.
#
# `dispatch_event` is here because it fabricates an event on an element with NO
# actionability checks at all — `dispatch_event("click")` on a submit button is
# a click by any honest reading, and it was the widest hole in the first version
# of this guard.
#
# `check`, `uncheck` and `type` were ABSENT from the first three versions of this
# set, and each absence was a live hole rather than a stylistic gap:
#   * `loc.type("yes\n")` is the deprecated alias of `press_sequentially` and goes
#     through the SAME driver alias map, so it presses Enter on a `\n`/`\r` — and
#     no newline gate consults it, because `_may_type_character_by_character` runs
#     only inside `_write_text`. The Enter hole, reopened under another name.
#   * `uncheck()` performs a real click, exactly as `check()` does.
#   * `check()` is the one clicking call this package legitimately needs (there is
#     no non-clicking way to tick a radio), so it is banned HERE and allowed at
#     exactly one named call site via `_ALLOWED_CLICKS` — not omitted from the
#     ban list. Omitting it meant the scan gave no answer at all about a call
#     that clicks, in the one module that writes.
# The drag family is here for the same reason `hover` is: a mousedown/mouseup
# pair aimed at a control is an input-device action on it, and nothing in this
# package drags. `select_text` focuses and selects, and `focus` is already banned.
_CLICK_METHODS = frozenset({
    "click", "dblclick", "tap", "submit", "press", "hover", "focus_and_click",
    "dispatch_event", "set_checked", "request_submit",
    "check", "uncheck", "type",
    "drag_to", "drag_and_drop", "drop", "select_text", "focus",
})

#: The clicking calls a NAMED module is allowed, keyed on file name. Everything
#: not in here gets none. `check` is allowed only in `fill.py`, and only because
#: Playwright offers no way to tick a radio without a click; the target is proved
#: to be a real radio/checkbox twice, once from the DOM snapshot and once from a
#: live `evaluate` read (`fill._live_choice_refusal`). The allowance is per FILE
#: and per METHOD so that widening it is a visible edit here, and
#: `test_the_only_clicking_call_allowed_in_the_package_is_one_check` pins that it
#: is not vacuous — that fill.py really does have exactly one such call and no
#: other module has any.
_ALLOWED_CLICKS: dict[str, frozenset[str]] = {"fill.py": frozenset({"check"})}


def _allowance_for(path: pathlib.Path) -> frozenset[str]:
    return _ALLOWED_CLICKS.get(path.name, frozenset())

# Attributes that hand control of the input devices to the caller wholesale.
# `page.keyboard.down("Enter")` presses Enter with no method name this guard
# would otherwise recognise.
_INPUT_DEVICE_ATTRS = frozenset({"keyboard", "mouse", "touchscreen"})

# Calls whose string argument is a SELECTOR. A submit-shaped literal reaching
# one of these is a submit control being addressed, even if nothing clicks it
# on the line you are reading.
_SELECTOR_CALLS = frozenset({
    "locator", "query_selector", "query_selector_all", "wait_for_selector",
    "get_by_role", "get_by_text", "get_by_label", "get_by_title", "eval_on_selector",
    # The rest of the `get_by_*` family and the two locator-narrowing calls. A
    # submit-shaped literal is addressing a submit control whichever accessor
    # spells it: `get_by_test_id("submit-application")` is not different in kind
    # from `get_by_role("button", name="Apply now")`.
    "get_by_placeholder", "get_by_alt_text", "get_by_test_id",
    "frame_locator", "filter",
})

# Calls that execute ARBITRARY JavaScript in the page. `fill.py` legitimately
# uses `locator.evaluate` twice (reading a select's option label and a file
# input's filename), so these cannot simply be banned — which makes them the
# single most likely future regression, since `document.forms[0].submit()`
# inside one is a submit with no Python-level click anywhere.
_EVALUATE_CALLS = frozenset({
    "evaluate", "evaluate_handle", "evaluate_all", "eval_on_selector",
    "eval_on_selector_all", "add_init_script", "add_script_tag",
    # `wait_for_function` takes a JS expression and polls it in the page, so
    # `wait_for_function("() => document.forms[0].submit()")` submits the form
    # with no click and no `evaluate` anywhere.
    "wait_for_function",
})

# JavaScript that submits or clicks. Deliberately broader than `_SUBMIT_RE`:
# inside a `<script>` payload, ANY `.click()` or `.submit()` is out of bounds,
# not only one aimed at something spelled "submit".
_JS_MUTATION_RE = re.compile(
    r"\.\s*click\s*\(|\.\s*submit\s*\(|requestSubmit|\bform\s*\.\s*submit"
    r"|dispatchEvent|KeyboardEvent",
    re.IGNORECASE,
)

# What this scan CANNOT see. Written down rather than glossed over, because the
# first version's docstring claimed "no submit-shaped string may appear in the
# code" — which was false, and the module's own `_SUBMITISH_RE` proves it.
_UNSCANNABLE = """
  * A selector held in a plain VARIABLE. `page.locator(control.selector)` is
    exactly what `fill.py` does, so a bare Name/Attribute argument cannot be
    banned. (A selector CONSTRUCTED in place — f-string, concatenation, a call —
    IS banned, so `page.locator(f"button[type={x}]")` is caught.) Backstop:
    `_single_locator` is the only place that calls `page.locator`, and it
    refuses any control `_is_submitish` matches — so the runtime value is
    checked even though the source cannot be.
  * Dynamic attribute access: `getattr(loc, "cl" + "ick")()`. No AST scan sees
    that. Backstop: nothing in this package builds attribute names.
  * A helper in ANOTHER module that clicks. Backstop:
    `test_every_applier_node_module_is_scanned` covers every node module, and
    `test_the_fill_executor_only_imports_scanned_or_pure_modules` pins that
    fill.py's package imports are all modules that are themselves guarded.
  * What Playwright does INTERNALLY. `check()` clicks. That is expected, it is
    the ONE clicking call allowed anywhere in the package (`_ALLOWED_CLICKS`),
    and `_fill_choice_group` refuses any target that is not a real
    radio/checkbox input — twice: once from the DOM snapshot, and once from a
    live `evaluate` read, because the snapshot predates the drafting model calls
    and `[id="x"]` matches any tag.
"""


def _submit_click_violations(source: str, *, allow: frozenset[str] = frozenset()) -> list[str]:
    """Every way `source` could click a submit control. Empty list == clean.

    Four rules. Each one alone is defeatable, which is why there are four —
    and even together they are not complete; see `_UNSCANNABLE`.

      1. ANY click-or-keystroke-family call at all, except the method names in
         `allow`. A rule that only rejected clicks whose literal argument looked
         submit-ish would miss `page.locator(sel).click()`.
      2. Any use of `page.keyboard` / `mouse` / `touchscreen`.
      3. Any `evaluate`-family call whose JavaScript submits or clicks — or
         whose JavaScript is not a plain string literal at all, since a
         constructed script cannot be read here.
      4. Any submit-shaped string literal handed to a selector lookup.

    `allow` exists because ONE clicking call is unavoidable — there is no
    non-clicking Playwright API for ticking a radio — and the honest way to
    handle that is to keep `check` in `_CLICK_METHODS` and name the single
    exception at the single call site that has it (`_ALLOWED_CLICKS`), rather
    than dropping it from the ban list and leaving every other module in the
    package unscanned for it. Default empty: a module gets no allowance unless
    someone writes one down.
    """
    tree = _strip_docstrings(ast.parse(source))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _INPUT_DEVICE_ATTRS:
            bad.append(f"touches .{node.attr}")
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr in _CLICK_METHODS and attr not in allow:
            bad.append(f"calls .{attr}()")
        if attr in _EVALUATE_CALLS:
            script = node.args[0] if node.args else None
            if not (isinstance(script, ast.Constant) and isinstance(script.value, str)):
                bad.append(f".{attr}(<non-literal script>)")
            elif _JS_MUTATION_RE.search(script.value) or _SUBMIT_RE.search(script.value):
                bad.append(f".{attr}({script.value[:40]!r})")
        if attr in _SELECTOR_CALLS:
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if _SUBMIT_RE.search(arg.value):
                        bad.append(f".{attr}({arg.value!r})")
                elif isinstance(arg, (ast.JoinedStr, ast.BinOp, ast.Call)):
                    # A selector BUILT here, whose value this scan cannot read:
                    # `page.locator(f"button[type={x}]")`. Banned outright.
                    # A bare Name/Attribute (`control.selector`) is allowed —
                    # fill.py needs it, and `_single_locator` checks it at
                    # runtime instead.
                    bad.append(f".{attr}(<constructed selector>)")
    return bad


def test_the_fill_executor_never_clicks_a_submit_control():
    """THE ONE RULE, for the one module that can break it.

    The allowance is `check`, and only `check`. What it is NOT allowed is pinned
    right below by `test_the_only_clicking_call_allowed_in_the_package_is_one_check`,
    which runs this same scan with no allowance at all and requires the single
    violation to be exactly one `.check()` — so a second one, or a `.click()`
    smuggled in beside it, still fails.
    """
    assert _submit_click_violations(
        FILL_MODULE.read_text(), allow=_allowance_for(FILL_MODULE)
    ) == []


def test_the_fill_executor_is_allowed_to_fill_and_to_attach():
    """The other half of the distinction. This module is SUPPOSED to write, and
    a guard copied from the read-only files would have banned `.fill(` outright
    — which is the kind of over-broad rule that gets deleted wholesale, taking
    the click ban with it. Asserting the permitted calls are present also pins
    that the guard above is running against a module that actually writes."""
    code = _executable_source(FILL_MODULE)
    assert ".fill(" in code
    assert "set_input_files(" in code
    assert ".press_sequentially(" in code


def test_the_submit_guard_accepts_filling_and_rejects_clicking():
    """The guard proved in BOTH directions on synthetic sources.

    A source-scanning guard that has never rejected anything is indistinguishable
    from one whose pattern no longer matches. These four cases are what make the
    green result above mean something."""
    fills_only = (
        "def go(page, value):\n"
        "    page.locator('#email').fill(value)\n"
        "    page.locator('#resume').set_input_files('/tmp/cv.pdf')\n"
        "    page.locator('#bio').press_sequentially(value)\n"
    )
    assert _submit_click_violations(fills_only) == []

    clicks_submit = (
        "def go(page):\n"
        "    page.locator('button[type=submit]').click()\n"
    )
    assert _submit_click_violations(clicks_submit), "a submit click must be caught"

    clicks_by_role = "def go(page):\n    page.get_by_role('button', name='Apply now')\n"
    assert _submit_click_violations(clicks_by_role), "an 'Apply now' lookup must be caught"

    presses_enter = "def go(page):\n    page.locator('#email').press('Enter')\n"
    assert _submit_click_violations(presses_enter), "Enter submits a form implicitly"


@pytest.mark.parametrize(
    "method, why",
    [
        ("type", "`type` is the deprecated alias of press_sequentially and uses the "
                 "SAME driver alias map, so it presses Enter on a \\n or \\r — and no "
                 "newline gate consults it"),
        ("uncheck", "`uncheck` performs a real click, exactly as `check` does"),
        ("check", "`check` clicks; it is allowed at ONE named call site, never by "
                  "being absent from the ban list"),
        ("drag_to", "a mousedown/mouseup pair aimed at a control is an input-device "
                    "action on it"),
        ("focus", "focus is an input-device action and nothing here focuses"),
    ],
)
def test_the_guard_rejects_the_page_mutating_apis_that_reopened_the_enter_hole(method, why):
    """The three-versions-late half of the guard, proved one API at a time.

    `Locator.type`, `Locator.uncheck` and `Locator.check` all EXIST on the
    installed driver and none of them was in `_CLICK_METHODS`, so
    `loc.type("yes\\n")` — a value carrying a newline typed character by
    character, pressing Enter, triggering HTML implicit submission — passed the
    scan clean. Parametrised so the failure message says which API regressed.
    """
    source = f"def go(page):\n    page.locator('#q').{method}('x')\n"
    assert _submit_click_violations(source), why


def test_the_typing_alias_is_caught_even_though_press_sequentially_is_allowed():
    """The distinction the guard has to make, in one test.

    `press_sequentially` is the executor's legitimate retry and must stay
    allowed; `type` is its alias and must not. AST attribute equality is what
    keeps them apart — a substring grep for "type" would ban `input_type`,
    `content_type` and half the codebase, and one for "press" would ban both.
    """
    assert _submit_click_violations(
        "def go(loc, v):\n    loc.press_sequentially(v)\n"
    ) == []
    assert _submit_click_violations("def go(loc, v):\n    loc.type(v)\n") == [
        "calls .type()"
    ]


# The Playwright surface, ENUMERATED. Every public method name on the five types
# a caller could reach from `fill.py`'s `page` argument, sorted into exactly one
# bucket by hand and reviewed once. The point is the equality assertion in
# `test_the_playwright_surface_is_fully_classified_by_the_one_rule_guard`: a
# Playwright upgrade that adds a method FAILS the suite until a human decides
# which bucket it belongs in, instead of arriving as a silent new hole. That is
# the difference between a guard built by enumeration and one built by guessing
# which method names sounded dangerous — the latter is how `type`, `uncheck`,
# `check` and `wait_for_function` were all missing at once.
#
# Measured against playwright as installed in this venv on 2026-08-03.
_PLAYWRIGHT_TYPES = ("Locator", "Page", "Frame", "FrameLocator", "ElementHandle")

#: Writes this package legitimately performs (`clear` is `fill("")` — no keys).
_PERMITTED_WRITES = frozenset({
    "fill", "set_input_files", "press_sequentially", "select_option", "clear",
})

#: Reads, navigation, waits, event plumbing, network routing and handle
#: bookkeeping. Nothing here dispatches a pointer event or a keystroke at a
#: control, and nothing here executes caller-supplied JavaScript.
_HARMLESS_SURFACE = frozenset({
    "add_locator_handler", "add_style_tag", "all", "all_inner_texts",
    "all_text_contents", "and_", "aria_snapshot", "as_element", "blur",
    "bounding_box", "bring_to_front", "cancel_pick_locator", "child_frames",
    "clear_console_messages", "clear_page_errors", "clock", "close",
    "console_messages", "content", "content_frame", "context", "count",
    "describe", "description", "dispose", "element_handle", "element_handles",
    "emulate_media", "expect_console_message", "expect_download", "expect_event",
    "expect_file_chooser", "expect_navigation", "expect_popup", "expect_request",
    "expect_request_finished", "expect_response", "expect_websocket",
    "expect_worker", "expose_binding", "expose_function", "first", "frame",
    "frame_element", "frames", "get_attribute", "get_properties", "get_property",
    "go_back", "go_forward", "goto", "hide_highlight", "highlight", "inner_html",
    "inner_text", "input_value", "is_checked", "is_closed", "is_detached",
    "is_disabled", "is_editable", "is_enabled", "is_hidden", "is_visible",
    "json_value", "last", "local_storage", "main_frame", "name", "normalize",
    "nth", "on", "once", "opener", "or_", "owner", "owner_frame", "page",
    "page_errors", "parent_frame", "pause", "pdf", "pick_locator", "reload",
    "remove_listener", "remove_locator_handler", "request", "request_gc",
    "requests", "route", "route_from_har", "route_web_socket", "screencast",
    "screenshot", "scroll_into_view_if_needed", "session_storage", "set_content",
    "set_default_navigation_timeout", "set_default_timeout",
    "set_extra_http_headers", "set_viewport_size", "text_content", "title",
    "unroute", "unroute_all", "url", "video", "viewport_size", "wait_for",
    "wait_for_element_state", "wait_for_event", "wait_for_load_state",
    "wait_for_timeout", "wait_for_url", "workers",
})


def _playwright_surface() -> set[str]:
    sync_api = pytest.importorskip(
        "playwright.sync_api",
        reason="playwright is an optional heavy dependency; the rest of the suite "
               "runs without it and so must this audit",
    )
    names: set[str] = set()
    for type_name in _PLAYWRIGHT_TYPES:
        cls = getattr(sync_api, type_name)
        names |= {n for n in dir(cls) if not n.startswith("_")}
    return names


def test_the_one_rule_guard_names_no_method_that_does_not_exist():
    """A guard entry that matches nothing is a guard entry that has stopped
    working, and it looks identical to one that works. Every name this scan bans
    or permits has to be a real attribute on the real driver — which is also what
    proves the additions above (`type`, `uncheck`, `check`, `wait_for_function`,
    the rest of `get_by_*`) were APIs and not guesses.

    `focus_and_click`, `submit` and `request_submit` are the deliberate
    exceptions: they are not Playwright methods at all. They are banned because a
    helper or a wrapper in this repo could be named that, and a `.submit()` in
    this package is never something the scan should have to reason about.
    """
    surface = _playwright_surface()
    not_playwright = {"focus_and_click", "submit", "request_submit"}
    for name in sorted((_CLICK_METHODS - not_playwright) | _EVALUATE_CALLS
                       | _INPUT_DEVICE_ATTRS | _SELECTOR_CALLS | _PERMITTED_WRITES):
        assert name in surface, f"the guard names .{name}(), which the driver does not have"
    for name in sorted(not_playwright):
        assert name not in surface, (
            f".{name}() is a real Playwright method now — move it out of the "
            f"'not a Playwright method' list and re-justify it"
        )


def test_the_playwright_surface_is_fully_classified_by_the_one_rule_guard():
    """The enumeration itself: no method on the surface is unaccounted for.

    This is the test that would have caught `type`/`uncheck`/`check`/
    `wait_for_function` on the day they were missed, and it is the only one here
    that scales — the guard's coverage stops being a matter of whether anyone
    thought of a method name and becomes a matter of whether the sets add up.

    A failure means Playwright grew (or renamed) a method. Do NOT paste it into
    `_HARMLESS_SURFACE` to go green: decide whether it can dispatch a pointer
    event or a keystroke at an element (`_CLICK_METHODS`), execute
    caller-supplied JavaScript (`_EVALUATE_CALLS`), address an element by a
    string (`_SELECTOR_CALLS`), or none of those (`_HARMLESS_SURFACE`).
    """
    surface = _playwright_surface()
    classified = (
        _CLICK_METHODS | _EVALUATE_CALLS | _INPUT_DEVICE_ATTRS | _SELECTOR_CALLS
        | _PERMITTED_WRITES | _HARMLESS_SURFACE
    )
    unclassified = sorted(surface - classified)
    assert not unclassified, (
        f"Playwright methods no ONE RULE bucket knows about: {unclassified}"
    )
    # And the audit is not vacuous in the other direction either: the harmless
    # list must not have quietly absorbed a name that is also banned.
    assert not (_HARMLESS_SURFACE & (_CLICK_METHODS | _EVALUATE_CALLS))


def test_the_fill_executor_imports_no_playwright():
    """Same property the pure modules have: the executor is handed a page, it
    never constructs one, so the whole suite runs without Chromium."""
    source = FILL_MODULE.read_text()
    assert "import playwright" not in source
    assert "from playwright" not in source


def test_a_control_that_looks_like_submit_is_refused_at_runtime():
    """Belt and braces on the source scan. `locate_dom` never discovers a
    `type=submit`, but a board could ship a text input labelled "Submit
    application" and the executor still declines to touch it."""
    html = '<label for="s">Submit application</label><input id="s" type="text">'
    page = _FormPage(html)
    controls = parse_controls(html)
    out = fill_one(page, controls, _answer("Submit application", "yes"))
    assert out.status == BLANK
    assert "never acts on one" in out.note
    assert page.writes == []


# --------------------------------------------------------------------------
# Nothing the resolver refused is ever written
# --------------------------------------------------------------------------


def test_is_typeable_refuses_every_blocking_kind():
    """Pure, and exhaustive over `BLOCKING_KINDS` rather than over the two
    examples someone thought of — a kind added to that frozenset is refused by
    construction, not by remembering to update this test."""
    for kind in sorted(BLOCKING_KINDS):
        answer = _answer("Anything", "Yes", akind=kind)
        assert not is_typeable(answer), kind


def test_a_blocking_answer_never_reaches_a_write_call():
    """Proved by ABSENCE OF A CALL, not by the outcome.

    Task 5 shipped a refusal test that asserted the *outcome* was blank, and a
    mutation routing a refused question straight through passed all 56 tests —
    because a value that is written and then read back wrong also produces a
    blank. The spy is the only assertion that distinguishes "refused" from
    "wrote it and disliked the result".

    The work-authorization case is the one that matters most: the resolver DOES
    produce "Yes" from the profile for it, so there is a real value sitting
    there ready to be typed, and the executor must still not type it."""
    html = (
        '<label for="a">Are you authorized to work in the US?</label>'
        '<input id="a" type="text">'
        '<label for="b">I agree to the privacy policy</label>'
        '<input id="b" type="text">'
        '<label for="c">Resume/CV</label><input id="c" type="file">'
    )
    controls = parse_controls(html)
    answers = [
        Answer(question=_q("Are you authorized to work in the US?"),
               value="Yes", source="profile", kind="work_auth"),
        Answer(question=_q("I agree to the privacy policy"),
               value="Yes", source="profile", kind="consent"),
        Answer(question=_q("Resume/CV", kind="file"),
               value="", source="blank", kind="file_upload"),
    ]
    page = _FormPage(html)
    outs = [fill_one(page, controls, a) for a in answers]
    assert page.writes == [], "a refused answer reached a write call"
    assert [o.status for o in outs] == [BLANK, BLANK, BLANK]
    # ...and the refused-but-resolved value is still SHOWN, so the human can act.
    assert "Yes" in outs[0].note


def test_a_resolver_blank_is_never_typed():
    """The second, independent gate: source must be profile/drafted."""
    html = '<label for="a">Phone</label><input id="a" type="text">'
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html),
                   Answer(question=_q("Phone"), value="", source="blank",
                          note="“phone” is empty in your profile", kind="phone"))
    assert page.writes == []
    assert out.status == BLANK and "empty in your profile" in out.note


def test_an_unknown_answer_source_is_not_typed():
    """Default-deny on the source too. A future `source="guessed"` must not be
    typed just because nobody remembered to add it to a denylist."""
    html = '<label for="a">Phone</label><input id="a" type="text">'
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html),
                   Answer(question=_q("Phone"), value="555-0100", source="guessed",
                          kind="phone"))
    assert page.writes == [] and out.status == BLANK


# --------------------------------------------------------------------------
# Read-back, and the retry policy
# --------------------------------------------------------------------------

_TEXT_HTML = '<label for="e">Email</label><input id="e" type="text">'


def test_a_verified_fill_is_reported_filled():
    page = _FormPage(_TEXT_HTML)
    out = fill_one(page, parse_controls(_TEXT_HTML),
                   _answer("Email", "testy@example.invalid", akind="email"))
    assert out.status == FILLED
    assert out.value == "testy@example.invalid"
    assert out.strategy == "fill" and out.attempts == 1
    assert page.methods == ["fill"]


def test_a_swallowed_fill_is_retried_once_with_a_different_strategy():
    """A React-controlled input that accepts `fill()` and silently throws the
    value away. The retry must be a DIFFERENT mechanism — typing, which emits
    real key events — because repeating `fill()` would repeat the failure."""
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(_TEXT_HTML),
                   _answer("Email", "testy@example.invalid", akind="email"))
    assert out.status == FILLED
    assert out.strategy == "type" and out.attempts == 2
    assert page.methods == ["fill", "fill", "press_sequentially"]
    # The clearing fill is empty; the value is never set twice.
    assert [a for m, _, a in page.writes if m == "fill"] == ["testy@example.invalid", ""]


def test_a_field_that_never_accepts_its_value_is_not_retried_forever():
    """Bounded at `MAX_ATTEMPTS`, reported blank WITH the value so the human can
    paste it, and the page is left alone after that."""
    page = _FormPage(_TEXT_HTML,
                     **{'[id="e"]': _Element(swallow=True, swallow_typing=True)})
    out = fill_one(page, parse_controls(_TEXT_HTML),
                   _answer("Email", "testy@example.invalid", akind="email"))
    assert out.status == BLANK
    assert out.attempts == MAX_ATTEMPTS == 2
    assert page.methods.count("fill") == 2 and page.methods.count("press_sequentially") == 1
    assert "testy@example.invalid" in out.note
    assert "would not accept" in out.note


def test_a_reformatting_field_is_reported_changed_not_blank():
    """A phone mask DID accept the value; calling that "blank" is untrue, and an
    untrue note costs the user trust in every other note the handoff shows. It
    is also not retried — typing again would append to what is already there."""
    html = '<label for="p">Phone</label><input id="p" type="text">'
    page = _FormPage(html, **{'[id="p"]': _Element(transform=lambda v: f"({v[:3]}) {v[3:]}")})
    out = fill_one(page, parse_controls(html), _answer("Phone", "5550100", akind="phone"))
    assert out.status == CHANGED
    assert out.value == "(555) 0100" and out.intended == "5550100"
    assert page.methods == ["fill"], "a reformatting field must not be retried"


def test_a_write_that_raises_is_reported_blank_with_the_reason():
    page = _FormPage(_TEXT_HTML,
                     **{'[id="e"]': _Element(raises=TimeoutError("timeout 5000ms"))})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", "x@y.invalid"))
    assert out.status == BLANK
    assert "TimeoutError" in out.note and "x@y.invalid" in out.note


def test_a_multiline_value_is_never_typed_character_by_character():
    """The hazard a click-scanning guard would not catch: `press_sequentially`
    on a value containing a newline sends Enter, and Enter in a text input
    inside a `<form>` triggers HTML's implicit submission. So the typing retry
    is refused outright for a multi-line value."""
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", "line one\nline two"))
    assert out.status == BLANK
    assert page.methods == ["fill"], "no keystroke may reach a single-line input"
    assert "Enter keystroke" in out.note


def test_the_typing_retry_is_refused_in_a_textarea_too_because_the_tag_is_stale():
    """The textarea exemption is GONE, and this is the test that used to assert
    the opposite (`test_a_multiline_value_is_typed_into_a_textarea_on_retry`).

    The old reasoning — Enter in a textarea inserts a newline and submits nothing
    — is true about textareas and irrelevant to the decision, because the tag it
    trusted is a snapshot fact. `page.content()` is read ONCE for the whole graph,
    `draft` then makes a model call per question, and `[id="t"]` is not
    tag-scoped: `_single_locator`'s live `count() == 1` is satisfied just as well
    by an `<input type="text" id="t">` that re-mounted in the meantime. Typing a
    multi-line draft into that, inside Lever's `method="POST"` form (which ships
    an `<input type="submit" class="hidden">`), submits the application.

    So the exemption cost the one guarantee this feature rests on and bought a
    second retry strategy on one control type. A textarea whose `fill()` was
    swallowed is now `blank` with the text to paste, which is honest and cheap.
    """
    html = '<label for="t">Cover letter</label><textarea id="t"></textarea>'
    page = _FormPage(html, **{'[id="t"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(html), _answer("Cover letter", "para one\npara two",
                                                       kind="textarea"))
    assert out.status == BLANK
    assert page.methods == ["fill"], "no keystroke may reach ANY field for a \\n value"
    assert "Enter keystroke" in out.note
    assert "para one" in out.note, "the value she has to paste must be in the note"
    # And the single-line retry still works for a value with no newline in it —
    # the refusal is about the VALUE now, so it must not have become blanket.
    single = _FormPage(html, **{'[id="t"]': _Element(swallow=True)})
    ok = fill_one(single, parse_controls(html), _answer("Cover letter", "one paragraph",
                                                       kind="textarea"))
    assert ok.status == FILLED and ok.strategy == "type"


# --------------------------------------------------------------------------
# The AI-draft marker survives
# --------------------------------------------------------------------------


def test_a_drafted_value_is_typed_marker_and_all():
    """The marker is what a recruiter sees if the human skips the review step —
    that is the fail-loud outcome we want, so it is typed in verbatim. This
    module must never strip it, and the check uses the exported `is_marked()`
    rather than re-hardcoding the literal."""
    drafted = drafting.mark("I am drawn to this role because of the platform work.")
    html = '<label for="t">Why do you want to work here?</label><textarea id="t"></textarea>'
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html),
                   _answer("Why do you want to work here?", drafted,
                           kind="textarea", source="drafted", akind="free_text"))
    typed = [a for m, _, a in page.writes if m == "fill"][0]
    assert typed == drafted
    assert drafting.is_marked(typed)
    assert out.status == FILLED and out.drafted is True
    assert drafting.DRAFT_MARKER in out.value


def test_the_executor_does_not_hardcode_the_draft_marker():
    """Re-spelling the marker literal here would let the two copies drift, and
    the drifted one would silently stop matching."""
    assert drafting.DRAFT_MARKER not in FILL_MODULE.read_text()


# --------------------------------------------------------------------------
# Locating: always through a Question, never a bare string
# --------------------------------------------------------------------------


def test_every_lookup_goes_through_a_question_object():
    """Ruling 1, enforced structurally rather than by review.

    `find_control` prefix-matches, so a short literal query can resolve to the
    wrong field with no ambiguity to detect. A token cap was considered and
    rejected — it breaks the legitimate "Resume" -> "Resume/CV and supporting
    documents" match — so the rule is instead that the executor never passes a
    string CONSTANT to a locator lookup at all."""
    tree = _strip_docstrings(ast.parse(FILL_MODULE.read_text()))
    finders = {"find_control", "find_by_key", "find_group_options", "find_selector"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in finders:
            continue
        for arg in node.args[1:]:
            assert not isinstance(arg, ast.Constant), (
                f"{name} called with the literal {getattr(arg, 'value', arg)!r} — "
                f"lookups must be driven by Question.label"
            )


def test_the_short_query_hazard_is_real_and_the_executor_avoids_it(controls):
    """The concrete failure ruling 1 exists to prevent, on the real Lever form,
    plus the executor doing the right thing on the same page.

    `find_control(controls, "Name")` returns the PRONUNCIATION field, because
    after the containment rule that is the one and only label whose first token
    is "name". Driving from `Question.label` hits the exact tier instead."""
    wrong = find_control(controls["lever"], "Name")
    assert wrong is not None and "Pronunciation" in wrong.label

    html = _html("lever")
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html), _answer("Full name", "Testy McTestface"))
    assert out.status == FILLED
    assert page.writes == [("fill", 'input[name="name"]', "Testy McTestface")]


def test_the_greenhouse_key_fallback_locates_by_identifier():
    """Greenhouse's questions come from JSON, whose key IS the DOM id — an exact
    identifier match, not prose matching, so it is not the hazard above."""
    html = _html("greenhouse")
    page = _FormPage(html)
    answer = Answer(question=Question(key="first_name", label="Not The DOM Label",
                                      required=True, kind="text"),
                    value="Testy", source="profile", kind="first_name")
    out = fill_one(page, parse_controls(html), answer)
    assert out.status == FILLED
    assert page.writes == [("fill", '[id="first_name"]', "Testy")]


def test_an_unlocatable_field_is_reported_blank_with_its_value():
    html = '<label for="e">Email</label><input id="e" type="text">'
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html), _answer("Favourite Dinosaur", "Stegosaurus"))
    assert out.status == BLANK and page.writes == []
    assert "Stegosaurus" in out.note


def test_a_selector_matching_two_live_elements_is_a_miss():
    """Same rule `PageLocator._single` applies: two hits is a miss, never "take
    the first"."""
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(count=2)})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", "x@y.invalid"))
    assert out.status == BLANK and page.writes == []


# --------------------------------------------------------------------------
# Groups and selects
# --------------------------------------------------------------------------

_RADIO_HTML = """
<li class="application-question">
  <div class="application-label"><div class="text">Do you have a driver's licence?</div></div>
  <ul>
    <li><label><input type="radio" name="q1" value="Yes"><span>Yes</span></label></li>
    <li><label><input type="radio" name="q1" value="No"><span>No</span></label></li>
  </ul>
</li>
"""


def test_a_radio_group_is_answered_through_its_options():
    """A group has no single element — `find_control` on the heading correctly
    returns None — so the members are located individually and the one matching
    the answer is ticked."""
    controls = parse_controls(_RADIO_HTML)
    assert find_control(controls, "Do you have a driver's licence?") is None
    page = _FormPage(_RADIO_HTML)
    out = fill_one(page, controls,
                   _answer("Do you have a driver's licence?", "Yes",
                           kind="select", options=["Yes", "No"]))
    assert out.status == FILLED and out.value == "Yes"
    assert page.writes == [("check", 'input[name="q1"][value="Yes"]', True)]


def test_a_radio_answer_matching_no_option_ticks_nothing():
    page = _FormPage(_RADIO_HTML)
    out = fill_one(page, parse_controls(_RADIO_HTML),
                   _answer("Do you have a driver's licence?", "Maybe",
                           kind="select", options=["Yes", "No"]))
    assert out.status == BLANK and page.writes == []
    assert "no option" in out.note


def test_the_tag_is_re_read_live_before_anything_is_ticked():
    """`check()` is the only call in this module that dispatches a click, and the
    evidence that its target is a real radio must not be a snapshot fact.

    ONE `page.content()` is taken for the whole graph, before `draft` makes a
    model call per question, and `input[name="q1"][value="Yes"]` is not
    tag-scoped. So `count() == 1` can be satisfied by a `<button role="radio">`
    that re-mounted in the same slot while the model was thinking, and clicking
    a button is the one thing THE ONE RULE forbids — with the source scan green,
    because the click happens inside Playwright.

    Three assertions, and the third is the one that makes this non-vacuous: the
    same fixture with nothing re-mounted DOES tick (above), the re-mounted one
    does not, and the page was never written to.
    """
    page = _FormPage(
        _RADIO_HTML,
        **{'input[name="q1"][value="Yes"]': _Element(live_tag="button", live_type="")},
    )
    out = fill_one(page, parse_controls(_RADIO_HTML),
                   _answer("Do you have a driver's licence?", "Yes",
                           kind="select", options=["Yes", "No"]))
    assert out.status == BLANK
    assert page.writes == [], "check() reached an element that is no longer an input"
    assert "<button>" in out.note and "not the radio or checkbox" in out.note
    # A text input in the same slot is refused for the same reason — the snapshot
    # said radio, the page says otherwise, and the page wins.
    retyped = _FormPage(
        _RADIO_HTML,
        **{'input[name="q1"][value="Yes"]': _Element(live_tag="input", live_type="text")},
    )
    out2 = fill_one(retyped, parse_controls(_RADIO_HTML),
                    _answer("Do you have a driver's licence?", "Yes",
                            kind="select", options=["Yes", "No"]))
    assert out2.status == BLANK and retyped.writes == []
    assert "type=text" in out2.note


def test_an_unreadable_live_shape_does_not_block_a_legitimate_tick():
    """The asymmetry, stated as a test: a *don't know* is not a refusal.

    A read that positively disagrees with the snapshot is new information and
    stops the tick. A read that could not be performed at all is not information,
    and refusing on it would silently stop ticking every radio on a driver whose
    `evaluate` behaves unexpectedly — while buying nothing, because the hazard the
    stale snapshot actually opened (an Enter keystroke) is refused
    unconditionally in `_may_type_character_by_character`, not here.
    """
    page = _FormPage(_RADIO_HTML,
                     **{'input[name="q1"][value="Yes"]': _Element(no_shape_api=True)})
    out = fill_one(page, parse_controls(_RADIO_HTML),
                   _answer("Do you have a driver's licence?", "Yes",
                           kind="select", options=["Yes", "No"]))
    assert out.status == FILLED
    assert page.writes == [("check", 'input[name="q1"][value="Yes"]', True)]
    # And the helper's own contract, directly: "" tag means don't-know, not "no".
    assert fill_mod._live_shape(_FormLocator(page, "nope")) == ("", "")


def test_the_live_shape_read_is_a_plain_literal_the_guard_can_read():
    """The live re-read is an `evaluate`, and `evaluate` is the single most
    dangerous call in this module — `document.forms[0].submit()` inside one is a
    submission with no Python-level click anywhere. The ONE RULE scan therefore
    only tolerates an `evaluate` whose script it can READ, which means every one
    of them has to be an inline string constant rather than a named module
    constant. Hoisting this script to `_SHAPE_SCRIPT` flipped the guard from
    "the JavaScript is `el => …tagName…`" to "the JavaScript is unknown", and the
    scan caught it. This pins that it stays inline and stays inert.
    """
    tree = _strip_docstrings(ast.parse(FILL_MODULE.read_text()))
    scripts = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate" and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert len(scripts) == 3, f"every evaluate must be a literal; found {scripts}"
    assert any("tagName" in s for s in scripts)
    for script in scripts:
        assert not _JS_MUTATION_RE.search(script), script
        assert not _SUBMIT_RE.search(script), script


def test_a_tick_that_does_not_stick_is_reported_blank():
    page = _FormPage(_RADIO_HTML,
                     **{'input[name="q1"][value="Yes"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(_RADIO_HTML),
                   _answer("Do you have a driver's licence?", "Yes",
                           kind="select", options=["Yes", "No"]))
    assert out.status == BLANK and "did not stay ticked" in out.note


def test_a_select_is_read_back_by_option_label_not_by_value_attribute():
    """`input_value()` on a `<select>` returns the option's `value` ATTRIBUTE,
    which on real boards is routinely a numeric id while the answer we hold is
    the option's text. Comparing those would report every successful selection
    as a failure."""
    html = (
        '<label for="s">Highest degree</label>'
        '<select id="s"><option value="">Select…</option>'
        '<option value="4021">Bachelor\u2019s Degree</option></select>'
    )
    page = _FormPage(html)
    out = fill_one(page, parse_controls(html),
                   _answer("Highest degree", "Bachelor\u2019s Degree", kind="select",
                           options=["Bachelor\u2019s Degree"]))
    assert out.status == FILLED
    assert page.writes == [("select_option", '[id="s"]', "Bachelor\u2019s Degree")]


def test_a_select_that_refuses_the_option_is_reported_blank_and_not_retried():
    html = ('<label for="s">Highest degree</label>'
            '<select id="s"><option>PhD</option></select>')
    page = _FormPage(html, **{'[id="s"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(html),
                   _answer("Highest degree", "PhD", kind="select", options=["PhD"]))
    assert out.status == BLANK
    assert page.methods == ["select_option"], "selects get no retry — the only other"\
        " strategy is clicking the option, and this module clicks nothing"


# --------------------------------------------------------------------------
# Visibility, and the file-input exemption
# --------------------------------------------------------------------------


def test_a_hidden_text_input_is_not_filled():
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(visible=False)})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", "x@y.invalid"))
    assert out.status == BLANK and page.writes == []
    assert "not visible" in out.note


def test_a_hidden_file_input_is_still_attached_to(tmp_path):
    """Ruling 6, in the direction that matters: Ashby and Greenhouse both keep
    the REAL `<input type=file>` visually hidden behind a styled button, so a
    visibility gate on the attach would make résumé upload impossible on two of
    the three boards. `set_input_files` does not need visibility."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    cv = tmp_path / "testy-cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    page = _FormPage(html, **{'[id="r"]': _Element(visible=False)})
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == ATTACHED and out.value == "testy-cv.pdf"
    assert page.methods == ["set_input_files"]


def test_the_visibility_gate_exempts_file_inputs_and_nothing_else():
    """The exemption stated as a property of the gate itself, so it cannot be
    reintroduced by a well-meaning edit that "adds the missing check"."""
    for kind in ("text", "textarea", "select", "checkbox"):
        assert fill_mod._needs_visibility_check(
            Control(tag="input", input_type="text", label="x", label_source="label",
                    kind=kind, required=False, required_source="")
        ), kind
    assert not fill_mod._needs_visibility_check(
        Control(tag="input", input_type="file", label="Resume", label_source="label",
                kind="file", required=False, required_source="")
    )


# --------------------------------------------------------------------------
# The résumé: attached by the agent, LAST, label-matched, verified
# --------------------------------------------------------------------------

_FULL_FORM_HTML = """
<label for="n">Full name</label><input id="n" type="text">
<label for="e">Email</label><input id="e" type="text">
<label for="r">Resume/CV</label><input id="r" type="file">
"""


@pytest.fixture
def cv(tmp_path):
    path = tmp_path / "testy-mctestface-cv.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return path


def test_the_resume_is_attached_last(cv):
    """Kayla's ruling, 2026-08-01. Greenhouse and Lever run a parse-and-prefill
    on upload that overwrites already-filled fields, so attaching last is what
    makes the agent's typed values win.

    Asserted on the PAGE MUTATION ORDER, not on the report's ordering: a report
    that lists the résumé last while the attach happened first would satisfy the
    weaker check and lose every filled value on a real Greenhouse form."""
    page = _FormPage(_FULL_FORM_HTML)
    report = fill_form(
        PageLocator(page),
        [_answer("Full name", "Testy McTestface"),
         _answer("Email", "testy@example.invalid", akind="email")],
        resume_path=str(cv),
    )
    assert page.methods == ["fill", "fill", "set_input_files"]
    assert page.methods[-1] == "set_input_files"
    assert report.outcomes[-1] is report.resume
    assert report.resume.status == ATTACHED
    assert [o.status for o in report.outcomes] == [FILLED, FILLED, ATTACHED]


def test_the_resume_outcome_is_present_even_when_nothing_else_filled():
    """So the handoff can always say what happened to the résumé."""
    page = _FormPage(_FULL_FORM_HTML)
    report = fill_form(PageLocator(page), [], resume_path=None)
    assert len(report.outcomes) == 1
    assert report.resume.status == BLANK
    assert page.writes == []


def test_an_ambiguous_file_input_gets_nothing_attached(cv):
    """Two plausible résumé slots. A résumé in the wrong slot is worse than an
    empty slot, so the agent refuses and says which two it could not choose
    between."""
    html = ('<label for="a">Resume</label><input id="a" type="file">'
            '<label for="b">CV</label><input id="b" type="file">')
    page = _FormPage(html)
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == BLANK and page.writes == []
    assert "will not guess" in out.note
    assert "“Resume”" in out.note and "“CV”" in out.note


def test_a_form_with_no_resume_slot_gets_nothing_attached(cv):
    html = ('<label for="a">Cover Letter</label><input id="a" type="file">'
            '<label for="b">Transcript</label><input id="b" type="file">')
    page = _FormPage(html)
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == BLANK and page.writes == []
    assert "is labelled as a résumé" in out.note


def test_a_label_naming_two_document_kinds_is_not_a_resume_slot(cv):
    """"Cover letter or resume" contains the word "resume" and is still not an
    unambiguous résumé slot. A positive keyword alone is not enough."""
    html = '<label for="a">Cover letter or resume</label><input id="a" type="file">'
    page = _FormPage(html)
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == BLANK and page.writes == []


@pytest.mark.parametrize("board,expected", [
    ("lever", "Resume/CV"),
    ("ashby", "Resume"),
    ("greenhouse", "Resume/CV"),
])
def test_the_resume_slot_is_found_on_all_three_real_boards(board, expected, controls):
    """The refusal rules have to still say YES on the forms this is built for.
    Ashby's second file input is "Additional Attachments" and Greenhouse's is
    "Cover Letter"; neither becomes a candidate, so all three resolve to one."""
    control, reason = find_resume_input(controls[board])
    assert control is not None, reason
    assert (control.group_label or control.label) == expected


def test_the_fakepath_form_of_a_filename_read_back_is_accepted(cv):
    """Measured on real headed Chromium, macOS, against a `file://` page:
    after `set_input_files("/tmp/.../fake-resume.pdf")`, `input_value()`
    returns `'C:\\fakepath\\fake-resume.pdf'` — the HTML spec's deliberate fake
    path, Windows separator and all. A naive `input_value() == path` check would
    have reported every successful attach as a failure, so the primary read-back
    is `el.files[0].name` and this is the documented fallback."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    page = _FormPage(html, **{'[id="r"]': _Element(fakepath=True, no_file_api=True)})
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == ATTACHED
    assert out.value == "testy-mctestface-cv.pdf"


def test_an_attach_that_does_not_land_is_reported_blank(cv):
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    page = _FormPage(html, **{'[id="r"]': _Element(swallow=True)})
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == BLANK
    assert "did not land" in out.note


def test_a_missing_resume_file_attaches_nothing(tmp_path):
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    page = _FormPage(html)
    out = attach_resume(page, parse_controls(html), str(tmp_path / "nope.pdf"))
    assert out.status == BLANK and page.writes == []
    assert "was not found" in out.note


def test_the_resume_outcome_never_leaks_an_absolute_path(cv, tmp_path):
    """Task 7 renders these notes to the user; a handoff has no business
    printing `/Users/<name>/…` back at them. Both the success and the
    file-missing path carry the basename only."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    ok = attach_resume(_FormPage(html), parse_controls(html), str(cv))
    assert ok.status == ATTACHED
    missing = attach_resume(_FormPage(html), parse_controls(html), str(tmp_path / "nope.pdf"))
    assert missing.status == BLANK
    for out in (ok, missing):
        assert str(tmp_path) not in out.note
        assert str(tmp_path) not in out.intended
        assert "/" not in out.intended and "\\" not in out.intended


def test_attach_resume_does_not_raise_on_a_nonsense_path():
    """"Never raises" has to include the argument handling: `os.fspath` throws
    TypeError on a non-path."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    out = attach_resume(_FormPage(html), parse_controls(html), 12345)  # type: ignore[arg-type]
    assert out.status == BLANK and "could not be read" in out.note


def test_attaching_a_file_is_not_submitting(cv):
    """Stated in the plan and therefore worth a test: the attach path writes
    exactly one thing to the page, and it is the file."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    page = _FormPage(html)
    attach_resume(page, parse_controls(html), str(cv))
    assert page.methods == ["set_input_files"]


# --------------------------------------------------------------------------
# The whole pass
# --------------------------------------------------------------------------


def test_fill_form_reads_one_dom_snapshot_for_the_whole_pass():
    """Re-reading mid-pass would mean acting on selectors from one DOM using
    questions from another — the bug `PageLocator` already documents."""
    page = _FormPage(_FULL_FORM_HTML)
    fill_form(PageLocator(page),
              [_answer("Full name", "Testy McTestface"),
               _answer("Email", "testy@example.invalid")])
    assert page.content_calls == 1


def test_fill_form_reports_every_answer_in_order(cv):
    page = _FormPage(_FULL_FORM_HTML, **{'[id="e"]': _Element(swallow=True,
                                                              swallow_typing=True)})
    report = fill_form(
        PageLocator(page),
        [_answer("Full name", "Testy McTestface"),
         _answer("Email", "testy@example.invalid", akind="email"),
         Answer(question=_q("I agree to the privacy policy"), value="Yes",
                source="profile", kind="consent")],
        resume_path=str(cv),
    )
    assert [o.label for o in report.outcomes] == [
        "Full name", "Email", "I agree to the privacy policy", "Resume/CV",
    ]
    assert [o.status for o in report.outcomes] == [FILLED, BLANK, BLANK, ATTACHED]
    assert {o.label for o in report.needs_review} == {"Email", "I agree to the privacy policy"}
    assert [o.label for o in report.by_status(FILLED)] == ["Full name"]


def test_fill_form_against_the_real_lever_form_writes_only_what_it_resolved(cv):
    """End to end on a captured 1.9 MB Lever page, with the real resolver: every
    write goes to a field the resolver produced a value for, no work-eligibility
    or consent field is touched, and the résumé attach is the last mutation."""
    html = _html("lever")
    page = _FormPage(html)
    locator = PageLocator(page)
    profile = {
        "full_name": "Testy McTestface",
        "email": "testy@example.invalid",
        "phone": "555-0100",
        "linkedin_url": "https://linkedin.invalid/in/testy",
    }
    answers = resolver.resolve(locator.questions(), profile)
    report = fill_form(locator, answers, resume_path=str(cv))

    written = [(m, s) for m, s, _ in page.writes]
    assert written[-1][0] == "set_input_files"
    assert [m for m, _ in written].count("set_input_files") == 1

    # The résumé input is itself a `file_upload` (blocking) question, and the
    # attach is the ONE deliberate exception to "never touch a blocking field".
    # So it is excluded from the set below and checked separately: the only
    # thing ever written to it is the file, never a typed value.
    resume_control, _reason = find_resume_input(locator.controls)
    assert resume_control is not None
    assert [m for m, s in written if s == resume_control.selector] == ["set_input_files"]

    blocking_selectors = {
        c.selector
        for a in answers if a.kind in BLOCKING_KINDS
        for c in locator.controls
        if c.label == a.question.label or c.group_label == a.question.label
    } - {resume_control.selector}
    assert not ({s for _, s in written} & blocking_selectors)
    # Lever's work-authorization and sponsorship radios are in that set, so this
    # is not a vacuous assertion.
    assert len(blocking_selectors) >= 4
    assert report.resume.status == ATTACHED
    assert [o.status for o in report.outcomes if o.status == FILLED]


def test_the_resolvers_file_upload_note_does_not_contradict_the_attach():
    """`resolver.py`'s note used to say "nothing is uploaded automatically",
    which stopped being true on 2026-08-01. The resolver still refuses the
    question — pure, blocking, blank — but its explanation must match what the
    executor actually does, or the handoff tells the user the opposite of what
    happened."""
    answers = resolver.resolve([_q("Resume/CV", kind="file")], {})
    note = answers[0].note
    assert answers[0].source == "blank" and answers[0].kind == "file_upload"
    assert answers[0].kind in BLOCKING_KINDS
    assert "nothing is uploaded automatically" not in note
    assert "attaches your résumé itself" in note


def test_the_resolver_is_still_pure_after_the_note_change():
    """The note is prose; the module must not have grown a dependency on the
    executor to say it."""
    source = pathlib.Path(resolver.__file__).read_text()
    for banned in ("import os", "import pathlib", "from agents.job_applier.nodes",
                   "import requests", "import httpx", "import sqlite3"):
        assert banned not in source, banned


def test_a_raising_multiline_write_reports_the_error_not_the_retry_refusal():
    """When a write actually errored, saying "we declined to retry by typing"
    explains the wrong thing. The real error is the more useful note, so it
    wins."""
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(raises=TimeoutError("timeout"))})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", "one\ntwo"))
    assert out.status == BLANK
    assert "TimeoutError" in out.note
    assert "Enter keystroke" not in out.note
    assert out.attempts == 1


# ===========================================================================
# Task 6, fix round 1 — items an independent review found
# ===========================================================================


# --------------------------------------------------------------------------
# CRITICAL: \r is an Enter alias too
# --------------------------------------------------------------------------


def test_every_enter_producing_character_is_refused_not_just_newline():
    """The first version of this rule checked `"\\n"` only.

    Playwright's driver holds ONE character->key alias map — `aliases` in
    `playwright/driver/package/lib/coreBundle.js`, whose only character entry is
    `["Enter", ["\\n", "\\r"]]`. So `\\r` presses Enter exactly as `\\n` does, and a
    value carrying a classic-Mac or stray CR would have been typed
    character-by-character into a single-line input, triggering HTML's implicit
    submission with the source scan green.

    That pair is EXHAUSTIVE, not a guess, which is what makes this test an
    invariant rather than two examples: it is asserted against the module's own
    `_ENTER_CHARS`, so a character added there without a matching refusal fails
    here.

    The rule is now TAG-BLIND — `_may_type_character_by_character` takes only the
    value — which is why there is no textarea case here any more. See
    `test_the_typing_retry_is_refused_in_a_textarea_too_because_the_tag_is_stale`.
    """
    assert fill_mod._ENTER_CHARS == frozenset({"\n", "\r"})
    for ch in sorted(fill_mod._ENTER_CHARS):
        assert not fill_mod._may_type_character_by_character(f"a{ch}b"), repr(ch)
    # Characters that are NOT Enter aliases go through insertText and press no
    # key, so they must not be refused — over-refusing would silently disable
    # the retry for ordinary unicode text.
    for ch in (" ", " ", "\t", " ", "é", "。"):
        assert fill_mod._may_type_character_by_character(f"a{ch}b"), repr(ch)


def test_the_newline_refusal_takes_no_control_at_all():
    """Structural, and the point of the whole fix: the gate CANNOT consult the
    snapshot's tag, because it is not given one.

    A behavioural test can be satisfied by a gate that still reads the tag and
    happens to refuse anyway; only the signature proves the tag is out of reach.
    Restoring the parameter — the mutation this pins — fails here even if every
    behavioural test above were somehow still green.
    """
    params = inspect.signature(fill_mod._may_type_character_by_character).parameters
    assert list(params) == ["value"], (
        "the newline gate must not be able to see a Control: a tag read from the "
        "pre-draft DOM snapshot is not evidence about the live element"
    )


@pytest.mark.parametrize("value", ["bad\r", "\rbad", "a\rb", "a\r\nb", "a\nb"])
def test_a_carriage_return_is_never_typed_into_a_single_line_input(value):
    """End to end, through the executor, for every CR/LF arrangement."""
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': _Element(swallow=True)})
    out = fill_one(page, parse_controls(_TEXT_HTML), _answer("Email", value))
    assert out.status == BLANK
    assert page.methods == ["fill"], f"a keystroke reached a single-line input for {value!r}"
    assert "Enter keystroke" in out.note


# --------------------------------------------------------------------------
# IMPORTANT 1: the résumé field is reported exactly once
# --------------------------------------------------------------------------


def test_the_resume_field_is_reported_exactly_once(cv):
    """The résumé input is itself a question, so the resolver emits a
    `file_upload` answer for it whose note says "attach it yourself". Reported
    alongside the attach outcome, Task 7 would tell the user
    "Resume/CV — blank, attach it yourself" about a slot the agent had just
    successfully attached to. Two contradictory statements about one field."""
    html = _FULL_FORM_HTML
    page = _FormPage(html)
    locator = PageLocator(page)
    answers = resolver.resolve(locator.questions(), {"full_name": "Testy McTestface"})
    # The resolver really does emit one, so this test is not vacuous.
    assert [a.kind for a in answers].count("file_upload") == 1

    report = fill_form(locator, answers, resume_path=str(cv))
    resume_entries = [o for o in report.outcomes if normalize_label(o.label) == "resume cv"]
    assert len(resume_entries) == 1, resume_entries
    assert resume_entries[0].status == ATTACHED
    assert report.superseded == ["r"], "the suppressed question's key, recorded not lost"
    # ...and nothing claims the user must attach it.
    assert not any("attach it yourself" in o.note for o in report.outcomes)


def test_the_resolver_answer_survives_when_the_agent_will_not_attach():
    """The suppression mirrors `attach_resume`'s own preconditions. With no
    résumé path there IS no attach, so "attach it yourself" is once again the
    true thing to say and the resolver's answer must not be dropped."""
    page = _FormPage(_FULL_FORM_HTML)
    locator = PageLocator(page)
    answers = resolver.resolve(locator.questions(), {})
    report = fill_form(locator, answers, resume_path=None)
    assert report.superseded == []
    # The resolver's answer for the field (labelled from the DOM) AND the attach
    # path's own refusal (which never got as far as identifying a field, so it is
    # labelled generically). Both blank, and neither contradicts the other.
    assert [o.label for o in report.outcomes if o.kind == "file_upload"] == [
        "Resume/CV", "Résumé",
    ]
    assert {o.status for o in report.outcomes if o.kind == "file_upload"} == {BLANK}


def test_other_file_fields_keep_their_resolver_answer(cv):
    """Suppression is keyed on the control the attach CLAIMED, not on the kind,
    so a cover-letter slot is still reported as the human's to do."""
    html = ('<label for="r">Resume/CV</label><input id="r" type="file">'
            '<label for="c">Cover Letter</label><input id="c" type="file">')
    page = _FormPage(html)
    locator = PageLocator(page)
    answers = resolver.resolve(locator.questions(), {})
    report = fill_form(locator, answers, resume_path=str(cv))
    labels = {o.label: o.status for o in report.outcomes}
    assert labels["Cover Letter"] == BLANK
    assert labels["Resume/CV"] == ATTACHED
    assert report.superseded == ["r"]


# --------------------------------------------------------------------------
# IMPORTANT 2: a reverted write is a swallowed write, not a reformat
# --------------------------------------------------------------------------


def test_a_reverted_write_on_a_prepopulated_field_is_retried_not_called_changed():
    """Browser autofill, ATS session-restore and apply-with-LinkedIn prefill all
    leave a field pre-populated. A React-controlled input that then re-renders
    from its own state produces "non-empty and different from what we wrote" —
    the same shape as a phone mask reformatting the value, and the opposite
    situation.

    Before the pre-write value was captured, this was reported `changed` with
    the note "the field accepted the value … it reformatted or truncated it" and
    the retry was SKIPPED, leaving a stale wrong value in a real application and
    describing it to the user as a reformat."""
    stale = _Element(value="stale@old.invalid", reverts=True)
    page = _FormPage(_TEXT_HTML, **{'[id="e"]': stale})
    out = fill_one(page, parse_controls(_TEXT_HTML),
                   _answer("Email", "testy@example.invalid", akind="email"))
    # The retry DID happen — that is the whole point.
    assert page.methods == ["fill", "fill", "press_sequentially"]
    assert out.status == BLANK, "a swallowed write is not a reformat"
    assert out.value == "stale@old.invalid"
    assert "snapped back" in out.note and "NOT your value" in out.note
    assert "testy@example.invalid" in out.note


def test_a_reverted_write_that_the_retry_rescues_is_reported_filled():
    """The other direction: the typing retry is exactly what a swallowed write on
    a pre-populated field needs, so when it works the outcome is `filled`."""
    page = _FormPage(_TEXT_HTML,
                     **{'[id="e"]': _Element(value="stale@old.invalid", swallow=True)})
    out = fill_one(page, parse_controls(_TEXT_HTML),
                   _answer("Email", "testy@example.invalid", akind="email"))
    assert out.status == FILLED and out.strategy == "type"


def test_a_prepopulated_field_that_reformats_is_still_changed():
    """And a genuine reformat on a pre-populated field is still `changed`, not
    mistaken for a revert — the discriminator is "reads back as what was there
    BEFORE", not "was pre-populated"."""
    page = _FormPage('<label for="p">Phone</label><input id="p" type="text">',
                     **{'[id="p"]': _Element(value="555-9999",
                                             transform=lambda v: f"({v[:3]}) {v[3:]}")})
    out = fill_one(page, parse_controls('<label for="p">Phone</label><input id="p" type="text">'),
                   _answer("Phone", "5550100", akind="phone"))
    assert out.status == CHANGED and out.value == "(555) 0100"
    assert page.methods == ["fill"]


# --------------------------------------------------------------------------
# IMPORTANT 3: reads are bounded too, not just writes
# --------------------------------------------------------------------------


def test_every_read_back_passes_an_explicit_timeout():
    """`DEFAULT_TIMEOUT_MS` bounded only the writes; `input_value()`,
    `evaluate()` and `is_checked()` all ran at Playwright's 30 s default, and a
    read waits for an attached element exactly as a write does. One detached node
    cost 30 s for one field — the hung-agent scenario the constant exists to
    prevent, caused by whichever call waits longest."""
    forms = [
        (_TEXT_HTML, _answer("Email", "x@y.invalid")),
        ('<label for="s">Degree</label><select id="s"><option>PhD</option></select>',
         _answer("Degree", "PhD", kind="select", options=["PhD"])),
        (_RADIO_HTML, _answer("Do you have a driver's licence?", "Yes",
                              kind="select", options=["Yes", "No"])),
    ]
    for html, answer in forms:
        page = _FormPage(html)
        fill_one(page, parse_controls(html), answer, timeout_ms=1234)
        assert page.read_timeouts, f"no reads recorded for {answer.question.label}"
        for name, timeout in page.read_timeouts:
            assert timeout == 1234, f"{name} ran unbounded on {answer.question.label}"


def test_the_resume_read_back_passes_an_explicit_timeout(cv):
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    page = _FormPage(html)
    attach_resume(page, parse_controls(html), str(cv), timeout_ms=4321)
    assert page.read_timeouts
    assert {t for _, t in page.read_timeouts} == {4321}


def test_no_read_helper_calls_playwright_without_a_timeout():
    """Structural backstop for the above: every read-back call in the source
    passes `timeout=`. A new read helper added without one is caught here even if
    no behavioural test happens to exercise it."""
    tree = _strip_docstrings(ast.parse(FILL_MODULE.read_text()))
    reads = {"input_value", "is_checked", "is_visible", "evaluate", "count"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in reads:
            continue
        if node.func.attr == "count":
            continue  # `count()` does not wait; it has no timeout parameter
        assert any(k.arg == "timeout" for k in node.keywords), (
            f".{node.func.attr}() is called without an explicit timeout"
        )


# --------------------------------------------------------------------------
# IMPORTANT 5 + the surviving mutations
# --------------------------------------------------------------------------


def test_a_select_read_back_uses_the_option_label_not_the_value_attribute():
    """M-E. The previous version of this test could not fail: the stub returned
    `el.value` from BOTH `evaluate()` and `input_value()`, so deleting
    `_read_selected_label`'s evaluate branch changed nothing and the
    `4021`-vs-`Bachelor's Degree` distinction it is named for was never
    exercised. `option_values` now makes the two paths return different strings."""
    label = "Bachelor’s Degree"
    html = ('<label for="s">Highest degree</label>'
            f'<select id="s"><option value="">Select…</option>'
            f'<option value="4021">{label}</option></select>')
    element = _Element(option_values={label: "4021"})
    page = _FormPage(html, **{'[id="s"]': element})
    out = fill_one(page, parse_controls(html),
                   _answer("Highest degree", label, kind="select", options=[label]))
    # The stub is genuinely discriminating: the two reads disagree.
    loc = page.locator('[id="s"]')
    assert loc.input_value() == "4021"
    assert loc.evaluate("el => el.selectedOptions[0].label") == label
    assert out.status == FILLED and out.value == label


def test_needs_review_includes_every_changed_field():
    """M-D. A `changed` phone-mask field silently vanishing from Task 7's review
    list is one token away, and nothing caught it. The field reformatted the
    value, so the human is the only one who can decide whether the result is
    acceptable — dropping it from review defeats the point of the status."""
    report = fill_mod.FillReport(outcomes=[
        FillOutcome(key="a", label="A", status=FILLED),
        FillOutcome(key="b", label="B", status=CHANGED, value="(555) 0100"),
        FillOutcome(key="c", label="C", status=BLANK),
        FillOutcome(key="d", label="D", status=FILLED, drafted=True),
        FillOutcome(key="e", label="E", status=ATTACHED),
    ])
    assert {o.key for o in report.needs_review} == {"b", "c", "d"}
    assert fill_mod.CHANGED in {o.status for o in report.needs_review}


def test_two_group_options_with_the_same_label_tick_nothing():
    """M-L. Ambiguity within a group is a refusal, exactly as ambiguity across
    them is — `find_control` and `find_group_options` both refuse rather than
    take the first, and this path must not be the exception."""
    html = """
    <li class="application-question">
      <div class="application-label"><div class="text">Pick one</div></div>
      <ul>
        <li><label><input type="radio" name="q" value="a"><span>Yes</span></label></li>
        <li><label><input type="radio" name="q" value="b"><span>Yes.</span></label></li>
      </ul>
    </li>
    """
    controls = parse_controls(html)
    # Both options normalize to "yes", so the answer matches two of them.
    assert [normalize_label(c.label) for c in controls] == ["yes", "yes"]
    page = _FormPage(html)
    out = fill_one(page, controls,
                   _answer("Pick one", "Yes", kind="select", options=["Yes", "Yes."]))
    assert out.status == BLANK and page.writes == []
    assert "2 options" in out.note


def test_the_typing_timeout_scales_with_the_value_length():
    """M-J. A 1,500-character drafted answer typed at 5 ms/char cannot finish
    inside the flat 5 s default, so an unscaled timeout would turn a WORKING
    retry into a spurious failure — the opposite of the read-back's purpose."""
    flat = fill_mod.DEFAULT_TIMEOUT_MS
    assert fill_mod._typing_timeout("short", flat) == flat, "short values keep the floor"
    long_value = "x" * 1500
    scaled = fill_mod._typing_timeout(long_value, flat)
    assert scaled > flat
    # It must comfortably exceed the wall-clock cost of typing it.
    assert scaled >= len(long_value) * 5


def test_a_choice_target_that_is_not_a_radio_or_checkbox_is_refused():
    """`check()` is the one write here that dispatches a click, so it is aimed
    only at an element that definitionally is not a button."""
    control = Control(tag="input", input_type="text", label="Yes", label_source="label",
                      kind="text", required=False, required_source="",
                      name="q", selector='input[name="q"]', group_label="Pick one")
    page = _FormPage("")
    out = fill_mod._fill_choice_group(
        [control], _answer("Pick one", "Yes", kind="select", options=["Yes"]),
        page, fill_mod.DEFAULT_TIMEOUT_MS)
    assert out.status == BLANK and page.writes == []
    assert "not a radio or checkbox" in out.note


# --------------------------------------------------------------------------
# Guard coverage: every node module, every named pin
# --------------------------------------------------------------------------

NODES_DIR = pathlib.Path(fill_mod.__file__).parent
_NODE_MODULES = sorted(p for p in NODES_DIR.glob("*.py") if p.name != "__init__.py")

#: EVERY module in the package, recursively — not just `nodes/*.py`.
#:
#: The glob used to stop at `nodes/`, which left three kinds of hole. Task 8's
#: `graph.py` and `state.py` sit one level up and got no four-rule scan at all
#: (a bespoke banned-attribute list in `tests/test_applier_graph.py` stood in for
#: it, and `add_init_script("document.forms[0].submit()")` walked straight past
#: that). `browser.py` is imported by a node module and holds the live
#: `BrowserContext`, and was named by `_UNSCANNABLE` as "a helper in ANOTHER
#: module" with no scan covering it. And any module added to this package in
#: future would have repeated the same mistake.
#:
#: Scanning the whole package costs nothing — the pure modules have no browser
#: calls to begin with — and makes the coverage question answer itself.
PACKAGE_DIR = NODES_DIR.parent
_GUARDED_MODULES = sorted(PACKAGE_DIR.rglob("*.py"))


@pytest.mark.parametrize("path", _GUARDED_MODULES, ids=lambda p: str(p.relative_to(PACKAGE_DIR)))
def test_every_applier_module_obeys_the_one_rule(path):
    """Parametrised over a RECURSIVE glob of the whole package, not over a
    hardcoded path. The guard was anchored to `fill.py` alone, then to
    `nodes/*.py`; either way the next module added anywhere else in
    `agents/job_applier/` would have got no ONE-RULE scan."""
    assert _submit_click_violations(
        path.read_text(), allow=_allowance_for(path)
    ) == [], path.name


def test_the_only_clicking_call_allowed_in_the_package_is_one_check():
    """`_ALLOWED_CLICKS` is an exception, so it has to be measured, not asserted.

    Scans EVERY module with NO allowance and requires the complete result to be
    exactly one `.check()` in `fill.py`. That makes three separate things fail
    loudly instead of silently:

      * a second `check()` call appearing in fill.py (the allowance is per
        method, so a per-method allowance would have let an unbounded number of
        them through);
      * `check()` appearing in any OTHER module in the package;
      * the allowance going stale — if fill.py ever stops calling `check()`, this
        fails and `_ALLOWED_CLICKS` gets deleted rather than sitting there as a
        permanently open door nothing needs.
    """
    found = {
        str(path.relative_to(PACKAGE_DIR)): _submit_click_violations(path.read_text())
        for path in _GUARDED_MODULES
    }
    assert {k: v for k, v in found.items() if v} == {"nodes/fill.py": ["calls .check()"]}


def test_every_applier_module_is_scanned():
    """The equivalent of `test_the_capture_probe_is_covered_by_the_one_rule_guard`
    for the write side: if the glob ever comes back empty, or a module is renamed
    out of it, this fails loudly rather than silently covering nothing.

    The four names are asserted individually because each one is a hole this
    guard has actually had: `fill.py` (the only module that writes), `graph.py`
    and `state.py` (added by Task 8, outside the old `nodes/*.py` glob), and
    `browser.py` (imported by a node, holds the live context, named by
    `_UNSCANNABLE` with no scan behind it)."""
    names = {p.name for p in _GUARDED_MODULES}
    for required in ("fill.py", "handoff.py", "graph.py", "state.py", "browser.py"):
        assert required in names, required
    assert set(_GUARDED_MODULES) == set(PACKAGE_DIR.rglob("*.py"))
    assert set(_NODE_MODULES) <= set(_GUARDED_MODULES)
    for path in _GUARDED_MODULES:
        assert path.is_file(), path


def _imported_module_paths(source: str) -> dict[str, pathlib.Path]:
    """{dotted name: file} for every `agents.*` / root-level module `source`
    imports. `from agents.job_applier import browser` resolves to `browser.py`,
    not to the package's `__init__.py` — getting that wrong is what makes an
    import guard silently vacuous."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agents"):
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agents"):
                    names.add(alias.name)
    out: dict[str, pathlib.Path] = {}
    for name in sorted(names):
        try:
            module = __import__(name, fromlist=["_"])
        except ImportError:
            continue  # `from x import SomeClass` — the leaf is not a module
        file = getattr(module, "__file__", None)
        if file:
            out[name] = pathlib.Path(file)
    return out


@pytest.mark.parametrize("path", _NODE_MODULES, ids=lambda p: p.name)
def test_every_node_module_only_imports_scanned_or_pure_modules(path):
    """One of the holes `_UNSCANNABLE` names is "a helper in another module that
    clicks". This closes it for the imports the node modules actually have.

    Two rules, because the imports fall into two kinds:
      * inside `agents/job_applier/` — must be a module the guard above scans.
        Task 8 added a node importing `browser.py`, the one module in the package
        holding a live `BrowserContext`, and the fill-only version of this test
        said nothing about it.
      * outside it (`agents.job_scraper.store`, `agents.resume_generator.store`)
        — these are DB stores, so instead of a scan they must contain no
        page-mutating call at all. Asserted, not assumed.
    """
    imported = _imported_module_paths(path.read_text())
    assert imported, f"{path.name} imports nothing — the resolver is broken"
    scanned = set(_GUARDED_MODULES) | set(_READ_ONLY_FILES)
    for module, imported_path in sorted(imported.items()):
        if imported_path.is_relative_to(PACKAGE_DIR):
            assert imported_path in scanned, f"{path.name} imports unscanned {module}"
            continue
        code = _executable_source(imported_path)
        for call in _MUTATING:
            assert call not in code, f"{module} (imported by {path.name}) calls {call}"
        assert not re.search(r"^\s*(?:import|from)\s+playwright", code, re.M), module


def test_the_import_guard_resolves_a_submodule_not_its_package():
    """Non-vacuity for the resolver above, which is the part that can silently
    stop covering anything: `from agents.job_applier import browser` must land on
    `browser.py`. Resolved as the PACKAGE it would land on `__init__.py`, which is
    nine lines of docstring and passes everything."""
    resolved = _imported_module_paths(
        "from agents.job_applier import browser\n"
        "from agents.job_applier.resolver import Answer\n"
    )
    assert resolved["agents.job_applier.browser"].name == "browser.py"
    assert resolved["agents.job_applier.resolver"].name == "resolver.py"
    # `Answer` is a class, not a module: skipped rather than mis-resolved.
    assert "agents.job_applier.resolver.Answer" not in resolved


def test_the_fill_executor_only_imports_scanned_or_pure_modules():
    """The narrower, older form of the test above, kept because it pins the EXACT
    import list of the one module that writes to the page — a new import there is
    a fact a reviewer should have to look at, not a set that quietly grows."""
    tree = ast.parse(FILL_MODULE.read_text())
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agents.")
    }
    assert imported == {
        "agents.job_applier.drafting",
        "agents.job_applier.locate_dom",
        "agents.job_applier.resolver",
        "agents.job_applier.schema_greenhouse",
    }
    guarded = {str(p) for p in _READ_ONLY_FILES}
    for module in sorted(imported):
        path = pathlib.Path(__import__(module, fromlist=["_"]).__file__)
        if str(path) in guarded:
            continue
        code = _executable_source(path)
        for call in _MUTATING:
            assert call not in code, f"{path.name} (imported by fill.py) calls {call}"


def test_every_test_named_in_the_fill_module_docstrings_exists():
    """Decision 8 says each numbered decision names the test that fails when it
    stops being true. One of those names was already DEAD
    (`test_the_fill_executor_never_clicks_anything`, which never existed), so the
    convention was documenting a promise nothing kept. This makes the pins
    load-bearing: rename a test and the module's own prose fails the suite."""
    source = FILL_MODULE.read_text()
    named = set(re.findall(r"\btest_[a-z0-9_]+", source))
    assert named, "the docstrings should name their pins"
    here = set(re.findall(r"^def (test_[a-z0-9_]+)", pathlib.Path(__file__).read_text(),
                          re.MULTILINE))
    missing = sorted(named - here)
    assert not missing, f"fill.py names tests that do not exist: {missing}"


def test_the_one_rule_guard_documents_what_it_cannot_catch():
    """The old docstring claimed "no submit-shaped string may appear in the
    code", which was false — only literals reaching a selector lookup are
    rejected, which is why fill.py's own `_SUBMITISH_RE` contains the word and
    passes. Overclaiming is worse than a narrow guarantee, because the next
    reader stops looking for the gap."""
    assert "_SUBMITISH_RE" in FILL_MODULE.read_text()
    assert _submit_click_violations(
        FILL_MODULE.read_text(), allow=_allowance_for(FILL_MODULE)
    ) == []
    for hole in ("VARIABLE", "Dynamic attribute", "ANOTHER module", "INTERNALLY"):
        assert hole in _UNSCANNABLE, hole


def _replace_control(control, **changes):
    import dataclasses
    return dataclasses.replace(control, **changes)


def test_single_locator_refuses_a_submit_control_on_its_own():
    """M-G6. The runtime half of THE ONE RULE lives in `_single_locator`, so that
    obtaining a locator for a submit control is impossible rather than merely
    impolite — but every caller ALSO checks `_refuse_submitish` first, so
    deleting the check inside `_single_locator` left the suite green. Belt and
    braces is only worth having if each strap is tested on its own."""
    page = _FormPage("")
    submitish = Control(tag="input", input_type="text", label="Submit application",
                        label_source="label", kind="text", required=False,
                        required_source="", element_id="s", selector='[id="s"]')
    assert fill_mod._single_locator(page, submitish) is None
    assert page.writes == []
    # The same control with an innocent label DOES resolve, so the refusal is
    # attributable to the label and not to some other property of the fixture.
    innocent = _replace_control(submitish, label="Full name")
    assert fill_mod._single_locator(page, innocent) is not None


@pytest.mark.parametrize("field_name", ["label", "group_label", "name", "element_id"])
def test_single_locator_refuses_on_every_field_it_claims_to_check(field_name):
    """`_is_submitish` is documented as checking label, group label, name, id and
    selector. Asserting only the label would let three of those quietly stop
    being checked."""
    base = Control(tag="input", input_type="text", label="Full name",
                   label_source="label", kind="text", required=False,
                   required_source="", element_id="ok", selector='[id="ok"]')
    assert fill_mod._single_locator(_FormPage(""), base) is not None
    tainted = _replace_control(base, **{field_name: "Submit application"})
    assert fill_mod._single_locator(_FormPage(""), tainted) is None, field_name


def test_the_documented_retry_count_matches_the_constant():
    """`MAX_ATTEMPTS` is quoted in fill.py's own prose as well as defined in its
    code, and a mutation harness aimed at the literal hit the DOCSTRING first —
    which is exactly how a docstring drifts away from the thing it describes."""
    source = FILL_MODULE.read_text()
    quoted = set(re.findall(r"`MAX_ATTEMPTS = (\d+)`", source))
    assert quoted == {str(MAX_ATTEMPTS)}, (
        f"the docstring says {quoted} but the constant is {MAX_ATTEMPTS}"
    )


def test_a_board_that_removes_the_file_input_on_upload_is_not_reported_as_a_failure(tmp_path):
    """Measured live against job-boards.greenhouse.io on 2026-08-05, on Kayla's
    first real assisted-apply run.

    `set_input_files` SUCCEEDS, and Greenhouse's uploader then REMOVES the hidden
    `<input id="resume">` from the DOM and renders the filename as a chip. Both
    read-back strategies then time out on a node that no longer exists,
    `_read_filename` swallows the exception and returns "", and the handoff told
    her “the résumé did not land: the field reads “nothing””.

    Her résumé HAD attached — the page was displaying its name. The write was
    fine; the verification was wrong. A read-back that cannot tell "the value is
    absent" from "the element is gone" reports a false failure on the one field
    the whole feature exists to fill.
    """
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    cv = tmp_path / "master__2026-07-24T21_30_36.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    page = _FormPage(html, **{'[id="r"]': _Element(vanishes=True)})

    out = attach_resume(page, parse_controls(html), str(cv))

    assert out.status == ATTACHED, out.note
    assert out.value == "master__2026-07-24T21_30_36.pdf"
    assert "nothing" not in out.note
    # It must say HOW it knows, since it did not read the input back.
    assert "removed" in out.note.lower() or "displays" in out.note.lower()


def test_a_vanished_input_with_no_filename_on_the_page_is_still_a_failure(tmp_path):
    """The other half, so the fix cannot become "assume success whenever the
    element disappears". No rendered filename means no evidence, and no evidence
    means blank — the same default-deny the rest of this module runs on."""
    html = '<label for="r">Resume/CV</label><input id="r" type="file">'
    cv = tmp_path / "testy-cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")

    class _SilentBoard(_FormPage):
        """Removes the input and renders NOTHING in its place."""

        def content(self) -> str:
            self.content_calls += 1
            return self._html

    page = _SilentBoard(html, **{'[id="r"]': _Element(vanishes=True)})
    out = attach_resume(page, parse_controls(html), str(cv))
    assert out.status == BLANK, out.note
    assert "attach it yourself" in out.note
