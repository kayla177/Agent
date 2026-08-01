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
import json
import pathlib

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


def test_module_has_no_mutating_call():
    """No code path in Phase B may ever click a submit button, and this module
    is supposed to only *read*. Cheap textual guard, but it is the one that
    would actually catch a future edit adding a `.fill()` "just for testing"."""
    tree = ast.parse(pathlib.Path(locate_dom.__file__).read_text())
    # Drop docstrings and comments — the prose legitimately names these calls
    # while explaining why they are absent — and check what is left to execute.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(tree))
    for call in _MUTATING:
        assert call not in code, f"locate_dom.py must never call {call}"


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
    assert (control.label, control.label_source) == ("Current location ✱", "label")


def test_chain_1b_real_lever(controls):
    """Lever wraps the input in an unnamed `<label>` and puts a dropdown's
    "No location found…" chrome *after* it. Only the leading text is the label."""
    control = find_control(controls["lever"], "Current location")
    assert control is not None
    assert control.label == "Current location ✱"
    assert control.label_source == "label"


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


def test_chain_4_real_lever(controls):
    """Lever's custom "card" questions keep their title in a *sibling*
    `<div class="application-label">`, reachable only through a generated class
    name. So those fall all the way through to the `name` attribute — an
    honestly useless label that no sensible query will match, which is the
    intended outcome (see the module docstring)."""
    by_name = [c for c in controls["lever"] if c.label_source == "name"]
    assert len(by_name) == 12
    assert all(c.label.startswith("cards[") for c in by_name)


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
        "label", "label-for-name", "aria-label", "aria-labelledby", "placeholder", "name",
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
@pytest.mark.parametrize(
    "query",
    ["Favourite Dinosaur", "Blood Type", "Shoe Size", "Name", "Attach"],
)
def test_an_unmatched_or_ambiguous_query_returns_none(board, query, controls):
    """None of these is a question on any of the three real forms — and "Name"
    and "Attach" are ambiguous rather than absent. Both must yield `None`."""
    assert find_control(controls[board], query) is None


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


def test_real_lever_checkbox_group_collapses_to_one_unreadable_question():
    """Lever's 33 language checkboxes are one question — but its title lives in
    a sibling `<div class="application-label">`, reachable only via a generated
    class name. So the question has all 33 options and NO label, which makes it
    unanswerable, so it is reported via `unreadable_questions` rather than as an
    answerable question. Deliberate: no label beats a guessed one."""
    unreadable = locate_dom.unreadable_questions(_html("lever"))
    language = next(q for q in unreadable if len(q.options) == 33)
    assert language.kind == "checkbox"
    assert language.label == ""
    assert "English (ENG)" in language.options
    assert "Choose not to disclose" in language.options
    # ...and it is NOT in the answerable list.
    assert all(q.label for q in discover_questions(_html("lever")))


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
    assert by_id["country"] == "Country*"
    assert by_id["phone"] == "Phone*"
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
    assert counts["lever"] == {"required-attr": 54, "": 11}
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
    assert counts["lever"] == {"label": 51, "placeholder": 2, "name": 12}
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
    counts = {"lever": 24, "ashby": 16, "greenhouse": 15}
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


def test_real_lever_unreadable_question_count():
    """5 of Lever's 29 questions cannot be read: the 33-checkbox language group
    and four yes/no dropdown pairs, all titled through a generated class name.
    Pinned so a future improvement that recovers them is noticed."""
    unreadable = locate_dom.unreadable_questions(_html("lever"))
    assert len(unreadable) == 5
    assert [q.kind for q in unreadable] == ["checkbox", "select", "select", "select", "select"]


@pytest.mark.parametrize("board", ["ashby", "greenhouse"])
def test_ashby_and_greenhouse_have_no_unreadable_questions(board):
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
    # No EEO term may appear as a *string literal* in this module — mentioning
    # them in prose is fine, re-listing them in code is the drift risk.
    literals = {
        node.value.lower()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for term in ("veteran", "ethnicity", "disability", "pronoun"):
        assert term not in literals, f"term list must live in schema_greenhouse only ({term})"
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
