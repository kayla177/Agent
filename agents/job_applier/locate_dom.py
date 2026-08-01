"""DOM discovery + label-based element location for Lever / Ashby / Greenhouse.

Greenhouse publishes its application form as structured JSON, so its *schema*
needs no browser at all (see `schema_greenhouse.py`). **Lever and Ashby publish
nothing.** Their questions exist only in the rendered page, so they have to be
discovered from the DOM — and every answer then has to be matched back to the
right element. That matching is the dangerous part: a wrong match types a real
value into the wrong box on a real job application.

THE ONE RULE for all of Phase B — no code path may ever click a submit button —
holds here trivially: this module only *reads*. It has no `.fill()`, no
`.click()`, no `.check()`, no `set_input_files()`. It answers two questions and
nothing else: "what is on this form?" and "which element is this label?".

Design decisions, and why
=========================

**1. Match on the accessible label, never on *generated* CSS classes.** Ashby's
and Greenhouse's class names are build-hashed (`_input_80epu_28`,
`remix-css-1a0ro4n-*`) and change on every deploy; the words a human reads next
to the box do not. So every lookup goes through the label.

  The one deliberate exception is Lever's `li.application-question` /
  `div.application-label` pair, which is a *semantic, stylesheet-stable*
  containment relationship rather than a build hash — see
  `_question_block_label`, which justifies it at length. Refusing to read it was
  not failing safe: it left Lever's work-authorization and sponsorship questions
  with `label=""`, and made two different questions both surface as the
  placeholder "Type your response".

**2. The fallback chain is explicit and ordered.** For each control:

  1. an associated ``<label>`` or its structural equivalent:
     (a) ``<label for="…">`` pointing at the control's ``id``;
     (b) Lever's **question block** — the `div.application-label` inside the
         control's own `li.application-question` (`_question_block_label`);
     (c) a ``<label>`` that *wraps* the control — for its **first labelable
         descendant only** (that is what HTML says a wrapping label labels; the
         alternative stamps one label on every control inside it), and using
         only the text that appears **before** the control, which is where a
         label's words actually sit (Lever's wrapping label also contains widget
         chrome like "ATTACH RESUME/CV" and "No location found. Try entering a
         different location" *after* the input, and gluing that on wrecks it);
     (d) a ``<label for="X">`` where ``X`` matches no element's ``id`` but
         *does* match this control's unique ``name`` — a dangling reference.
         Ashby ships exactly this on its yes/no questions, and repairing it is
         the difference between the label "Will you require company
         sponsorship…" and the label ``28ff5b93-a104-45f7-9d46-2d13d3217dca``.

     (b) and (c) swap places for a **checkbox/radio**, because a wrapping
     ``<label>`` around a choice control conventionally holds the OPTION text
     ("Yes") while the block holds the question — so for those the wrapping
     label is the control's own label and the block becomes its `group_label`.

     This step group is first because it is the only one a human can verify by
     looking at the page. (WAI-ARIA would let ``aria-label`` override a visible
     ``<label>``; we deliberately invert that. If they disagree, the visible
     text is what the applicant is actually answering.)
  2. ``aria-label``, else ``aria-labelledby`` (dereferenced to the referenced
     elements' text). Real forms use these for controls whose visible label is
     an image or is rendered by a widget.
  3. ``placeholder`` — weak (it is a hint, not a label) but it is genuine
     human-readable text, and on real Ashby forms it is sometimes all there is.
  4. the ``name`` attribute — the last resort. Machine-ish
     (``_systemfield_email``), but stable and at least *derived from the field's
     own identity*.

  Tiers 3 and 4 are additionally **duplicate-checked**: a placeholder or `name`
  shared by two controls means two different questions wearing one label, which
  is the same ambiguity `find_control` refuses, so both are demoted to
  unlabelled (see `_WEAK_LABEL_SOURCES`).

  There is deliberately **no step 5 = "the Nth input"**. Positional indexing is
  precisely how a value ends up in the wrong box when a board reorders or
  A/B-tests a form. A control with no derivable label is simply not discovered
  and not locatable; the human fills it in themselves.

  Whatever the source, the stored label is **cleaned of its required marker**
  (`_clean_label`): Lever renders U+2731 and Greenhouse a plain `*` as a child of
  the label element, and that character would otherwise reach Task 3's phrase
  matching and Task 7's report.

**3. Ambiguity returns ``None``, never a best guess.** If two controls match a
query equally well, that is a *failure to locate*, not a coin flip
(`find_control`). Same for a label whose ``for`` is claimed by two controls, and
same for `find_by_key`.

  Ambiguity detection only helps when both candidates are *present*, though, so
  anything that makes a wrong candidate the ONLY candidate is the more dangerous
  bug. Two such holes are closed explicitly: a control hidden by an **ancestor**
  (`aria-hidden`, `hidden`, `inert`, `display:none`, `<template>`) is not
  discovered at all — a collapsed accordion or a mobile/desktop duplicate carries
  the *same* label as the visible field and would win the exact-match tier alone
  — and a wrapping ``<label>`` labels only one control, so filtering its siblings
  cannot promote a wrong one to uniqueness.

**4. Label matching is normalized but boundary-safe.** This repo has shipped
loose-matching bugs twice — `job_scraper/locations.py` matched "uk" inside
"Milwaukee", and the Task 3 resolver matched the pronoun "us" as the United
States. So matching is **exact-or-token-prefix only** (see `_matches`):

  * "Email" matches ``Email``, ``Email Address``, ``E-mail *``  ✓
  * "mail" does NOT match ``Email``            (not a token prefix)
  * "Name" does NOT match ``First Name``       (a *suffix*, not a prefix)
  * "Pr" does NOT match ``Prénom``             (a word fragment)

  Infix/suffix matching is not supported at all, on purpose: "Name" grabbing
  "First Name" is the exact failure mode that puts a full name into a
  first-name box. Normalization is Unicode-aware (``\\w``, casefold, accent
  folding), because an ASCII-only fold both erased non-Latin labels entirely and
  split accented words into fragments that then satisfied the prefix rule.

**5. Every question this module reports as answerable has a readable label.**
`discover_questions` withholds two kinds, each retrievable through its own
accessor so nothing vanishes silently: EEO self-identification questions
(`excluded_eeo_questions`, sharing `schema_greenhouse`'s term list) and questions
with no derivable label (`unreadable_questions`). The second is a safety
property, not tidiness — the EEO screen reads `label`, so a label-less question
cannot be screened at all, and Lever produces exactly that shape.

**6. Pure parsing, thin Playwright adapter.** Everything above operates on an
HTML *string* using the standard library's `html.parser` — no new dependency,
no browser, so it is unit-testable against the captured fixtures in
`tests/fixtures/ats/`. Playwright appears only in `PageLocator`, which is a
~25-line shim: `page.content()` in, CSS selector out. Tree walks are iterative,
because `html.parser` never auto-closes a tag and a page with a few thousand
unclosed `<li>`s would otherwise turn "cannot locate anything" into a
RecursionError.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Any

from agents.job_applier.schema_greenhouse import Question, is_eeo_label

# ---------------------------------------------------------------------------
# HTML parsing (standard library only)
# ---------------------------------------------------------------------------

# Elements that never have children/closing tags. `html.parser` does not know
# this for us — it reports `<input>` via handle_starttag with no matching
# handle_endtag, which would otherwise corrupt the tree we build.
_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)

# Tags whose text content is code, not prose. `html.parser` hands their bodies
# to handle_data like any other text; dropping it keeps 687 KB of Lever's
# inline <style> out of every label we compute.
_NON_TEXT = frozenset({"script", "style", "template", "svg", "noscript"})

# Form controls we consider fillable. `<button>` is NOT here — it is a thing
# you click, and this package never clicks.
_CONTROL_TAGS = frozenset({"input", "textarea", "select"})

# `input` types that are not a question: hidden state, and the buttons.
# `type=submit` in particular must never be discovered as something to fill.
_NON_FILLABLE_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})

# reCAPTCHA injects a hidden <textarea name="g-recaptcha-response"> into every
# board's page (present in all three captured fixtures). It is machine state,
# never a question.
_MACHINE_NAMES = frozenset({"g-recaptcha-response"})


class _Node:
    """One element in a minimal DOM.

    `content` interleaves this element's text runs and child elements in
    **source order** — `["A", <span>, "C", <input>, "D"]` — rather than keeping
    text in one bucket and children in another. That matters: a wrapping
    `<label>`'s question text is the text *before* the control it wraps, and with
    two separate buckets there is no way to know that a bare text node came
    after the input. It cost one indirection and removed a whole class of
    mislabelling.
    """

    __slots__ = ("tag", "attrs", "content", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.content: list[str | _Node] = []
        self.parent = parent

    @property
    def children(self) -> list["_Node"]:
        return [c for c in self.content if isinstance(c, _Node)]

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "") or ""

    def has_class(self, name: str) -> bool:
        """Whitespace-token match on `class`, never a substring: "application-label"
        must not be satisfied by "application-label-wrapper"."""
        return name in self.attr("class").split()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def descendants(self):
        """Iterative, not recursive. `html.parser` never auto-closes a tag, so
        a page with 1200 unclosed `<li>`s nests 1200 deep and a recursive walk
        raises RecursionError — which would turn a malformed page into a crash
        instead of "nothing locatable"."""
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


class _TreeBuilder(HTMLParser):
    """Builds a `_Node` tree. Tolerant by design: real board HTML has unclosed
    tags and stray end tags, and a parse error here would mean "cannot locate
    anything", so nothing raises."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self._stack = [self.root]

    @property
    def _current(self) -> _Node:
        return self._stack[-1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._current)
        self._current.content.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<input ... />` — explicitly self-closed. Never pushed.
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._current)
        self._current.content.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Unwind to the nearest matching open tag; ignore an end tag that never
        # had a start (common in minified board HTML).
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if self._current.tag in _NON_TEXT:
            return
        self._current.content.append(data)


def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _walk_text(
    container: _Node, stop_tags: frozenset[str], target: _Node | None = None
) -> tuple[str, str]:
    """Source-order text of `container`, split at `target`. Iterative.

    Returns (before, after). With `target=None` everything lands in `before`, so
    this doubles as "all the text". Subtrees whose tag is in `stop_tags`, or that
    are script/style-ish, are skipped entirely.

    Iterative rather than recursive so a pathologically nested page (see
    `_Node.descendants`) cannot turn into a RecursionError, and source-order
    because a wrapping ``<label>``'s question text is the text *before* the
    control it wraps: Lever writes
    ``<label>Current location ✱ <input> No location found. Try again</label>``
    and gluing the trailing dropdown chrome onto the label wrecks it. The
    "after" half is still returned because a checkbox's *option* text sits there
    instead (``<label><input type=checkbox><span>English (ENG)</span></label>``).
    """
    before: list[str] = []
    after: list[str] = []
    seen = False
    stack: list[str | _Node] = list(reversed(container.content))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            (after if seen else before).append(item)
            continue
        if item is target:
            seen = True
            continue
        if item.tag in _NON_TEXT or item.tag in stop_tags:
            continue
        stack.extend(reversed(item.content))
    return _collapse("".join(before)), _collapse("".join(after))


def _text_of(node: _Node, *, stop_tags: frozenset[str] = frozenset()) -> str:
    """All text under `node`, whitespace-collapsed, skipping `stop_tags`
    subtrees (and always script/style-ish ones)."""
    return _walk_text(node, stop_tags)[0]


def _split_text_around(
    container: _Node, target: _Node, *, stop_tags: frozenset[str] = frozenset()
) -> tuple[str, str]:
    """Text inside `container` before / after `target`, in source order."""
    return _walk_text(container, stop_tags, target)


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------

# Required markers a board appends to a visible label. Lever uses U+2731 HEAVY
# ASTERISK (✱) — NOT an ASCII "*" — which is exactly the kind of detail that
# breaks a naive `rstrip("*")`. Greenhouse and Ashby render the marker via CSS
# or a separate node; "(required)" is included because plenty of other forms
# spell it out.
_REQUIRED_MARK_CHARS = "*✱＊∗٭·"
_REQUIRED_SUFFIX_RE = re.compile(
    r"(?:\s*\(\s*required\s*\)|\s*\brequired\b|[" + re.escape(_REQUIRED_MARK_CHARS) + r"\s])+$",
    re.IGNORECASE,
)

# Intra-word joiners: removed rather than turned into a space, so "E-mail"
# normalizes to "email" and matches a query of "Email".
_JOINERS_RE = re.compile(r"[-‐-―_'‘’ʼ.]")
# Everything that is not a word character becomes a space (":", "?", "(", ")",
# "/", "|", "[", "]", ...), keeping token boundaries where a human sees them.
# `\w` is UNICODE-aware on purpose. An earlier `[^0-9a-z]` folded every
# non-ASCII letter to a space, which was wrong in two directions: "姓名" and
# "Прізвище" normalized to "" and became unfindable by any query at all, and
# "Prénom" became the two tokens "pr nom" so that a query of "Pr"
# token-prefix-matched a word FRAGMENT — the exact `uk`-in-`Milwaukee` failure
# the boundary rules exist to prevent.
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def _strip_accents(text: str) -> str:
    """NFKD + drop combining marks, so "Prénom" and "Prenom" compare equal.
    Scripts without combining marks (CJK, Cyrillic) pass through untouched."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_label(text: str) -> str:
    """Canonical form of a label for comparison.

    Unicode-normalized (NFKC), required-marker stripped, casefolded,
    accent-folded, punctuation folded to token boundaries, whitespace collapsed.
    Deliberately lossy — it exists only to make "E-mail *", "Email", and
    "email address" comparable.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ").strip()
    text = _REQUIRED_SUFFIX_RE.sub("", text)
    # casefold, not lower: "ß" -> "ss" and dotted-I forms fold correctly.
    text = _strip_accents(text.casefold())
    joined = _NON_WORD_RE.sub(" ", _JOINERS_RE.sub("", text))
    return re.sub(r"\s+", " ", joined).strip()


def _variants(text: str) -> frozenset[str]:
    """Both readings of an ambiguous joiner, so neither spelling loses.

    "E-mail" should equal "Email" (joiner removed) *and* "Full-Name" should
    equal "Full Name" (joiner as a space). Since we cannot know which the author
    meant, we produce both and match if any pair lines up.
    """
    if not text:
        return frozenset()
    spaced = _JOINERS_RE.sub(" ", unicodedata.normalize("NFKC", text))
    return frozenset(v for v in (normalize_label(text), normalize_label(spaced)) if v)


def _matches(query: str, label: str) -> bool:
    """True if `query` addresses `label`: equal, or a whole-token **prefix**.

    Token-prefix, not substring and not suffix — see decision 4 in the module
    docstring. `"name"` matching `"first name"` is the bug this rules out.
    """
    q_variants = _variants(query)
    l_variants = _variants(label)
    if not q_variants or not l_variants:
        return False
    for q in q_variants:
        for lab in l_variants:
            if q == lab or lab.startswith(q + " "):
                return True
    return False


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

# Ordered names of the fallback chain steps, for reporting/telemetry and so a
# caller can say "this label came from a placeholder, treat it with suspicion".
LABEL_SOURCES = (
    "label",           # step 1a / 1c: <label for=id>, or a wrapping <label>
    "question-block",  # step 1b: Lever's li.application-question containment
    "label-for-name",  # step 1d: a dangling <label for=X> where X == unique name
    "aria-label",      # step 2
    "aria-labelledby",  # step 2 (dereferenced)
    "placeholder",     # step 3 — weak: a hint, not a label
    "name",            # step 4 — last resort, machine-ish
)

# The two tiers that are not really labels: a placeholder is a *hint* ("Type your
# response") and a `name` is an identifier (`cards[<uuid>][field0]`). Both are
# shared verbatim by unrelated fields on real forms, so a duplicate at these
# tiers means two DIFFERENT questions wearing one label — the ambiguity that
# `find_control` exists to refuse, arriving one layer earlier. Controls in that
# situation are demoted to unlabelled (hence `unreadable_questions`) rather than
# emitted as indistinguishable twins.
_WEAK_LABEL_SOURCES = frozenset({"placeholder", "name"})


@dataclass(frozen=True)
class Control:
    """One fillable form control found in a document.

    `selector` is a CSS selector that addresses *this* control and no other, or
    `None` when the control carries nothing unique to address it by (Ashby's
    location combobox has neither `id` nor `name`). `None` is not a bug: it
    means "this one is the human's to fill", which is the safe outcome.

    `group_label` is set only for a radio/checkbox group member, and holds the
    group's shared question text (from the enclosing `<fieldset>`), while
    `label` holds the individual option's text.
    """

    tag: str
    input_type: str
    label: str
    label_source: str
    kind: str
    required: bool
    required_source: str
    options: list[str] = field(default_factory=list)
    name: str = ""
    element_id: str = ""
    selector: str | None = None
    group_label: str = ""


# Inline styles that mean "not on the page". Deliberately NOT a general CSS
# engine, and deliberately not matching the visually-hidden clip/1px-square
# pattern: Ashby and Greenhouse both hide their REAL file inputs that way (a
# styled button triggers them), so treating clipped controls as absent would
# make résumé upload undiscoverable on two of three boards.
_DISPLAY_NONE_RE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:;|$)", re.I)


def _is_hidden(node: _Node) -> bool:
    """True if `node` or any ancestor is hidden from the accessibility tree.

    Checking only the control itself is not enough, and the gap is a
    wrong-element bug rather than a cosmetic one: a collapsed accordion, a
    mobile/desktop duplicate pair, or `react-modal`'s `aria-hidden` on the app
    root all produce a *hidden* control carrying the *same* label as the visible
    one. The hidden copy then wins the exact-match tier, and ambiguity detection
    never fires because there is only one candidate at that tier.

    Deliberate limitation: a container hidden purely by a **CSS class** is
    invisible to a pure HTML parser. Greenhouse's phone-country dropdown is
    exactly that (`class="iti__dropdown-content iti__hide"`), so its "Search" box
    is still discovered from the saved fixture. Harmless — nothing a resolver
    asks for matches "Search" — but a live caller wanting to prune those should
    do it in `PageLocator` via Playwright's `is_visible()`, NOT here, and must
    exempt file inputs: Ashby and Greenhouse both keep their real
    `<input type=file>` visually hidden and drive it from a styled button.
    """
    for candidate in (node, *node.ancestors()):
        if candidate.attr("aria-hidden").strip().lower() == "true":
            return True
        if "hidden" in candidate.attrs and candidate.tag != "input":
            return True
        if "inert" in candidate.attrs:
            return True
        if _DISPLAY_NONE_RE.search(candidate.attr("style")):
            return True
        # `<template>` content is an inert clone source, not the live form. Its
        # controls would fabricate questions and, worse, duplicate the real
        # field's label and make the real field ambiguous.
        if candidate.tag == "template":
            return True
    return False


def _is_fillable(node: _Node) -> bool:
    if node.tag not in _CONTROL_TAGS:
        return False
    if node.attr("name").strip() in _MACHINE_NAMES:
        return False
    if node.tag == "input":
        itype = node.attr("type").strip().lower() or "text"
        if itype in _NON_FILLABLE_INPUT_TYPES:
            return False
        # `<input hidden>` is the boolean attribute, equivalent to type=hidden.
        if "hidden" in node.attrs:
            return False
    if _is_hidden(node):
        return False
    # Voluntary EEO self-identification, excluded STRUCTURALLY and ahead of
    # everything else. Lever's `.eeo-survey` block reuses the same
    # `application-label` classes as the rest of the form, so the containment
    # rule would read "What is your gender?" out of it as happily as any other
    # question — and a text-only screen is the wrong instrument when the
    # structure already says "this is the demographic survey".
    if _in_eeo_block(node):
        return False
    return True


def _label_index(root: _Node) -> tuple[dict[str, list[_Node]], dict[str, list[_Node]]]:
    """`for`-attribute -> labels, and `id` -> elements. Both keep *lists* so
    duplicates are visible rather than silently last-write-wins: a duplicated
    `id` makes a `for` reference ambiguous, and ambiguous means `None`."""
    by_for: dict[str, list[_Node]] = {}
    by_id: dict[str, list[_Node]] = {}
    for node in root.descendants():
        if node.tag == "label":
            target = node.attr("for").strip()
            if target:
                by_for.setdefault(target, []).append(node)
        node_id = node.attr("id").strip()
        if node_id:
            by_id.setdefault(node_id, []).append(node)
    return by_for, by_id


_LABEL_STOP = frozenset({"label", "select", "button"})

# --- Lever's question block -------------------------------------------------
#
# Lever is the one board that puts a question's text and its control in two
# SIBLING elements, with no `<label>`, no `for`, and no ARIA relationship
# between them:
#
#   <li class="application-question custom-question">
#     <div>
#       <div class="application-label full-width multiple-choice">
#         <div class="text">Are you legally authorized to work…<span class="required">✱</span></div>
#       </div>
#       <div class="application-field full-width required-field">
#         <ul data-qa="multiple-choice">
#           <li><label><input type="radio" name="cards[<uuid>][field0]" value="Yes" required/>…
#
# Reading it requires naming two classes, which needs justifying against the
# "match on the label, never on CSS classes" rule. Three reasons this is inside
# that rule rather than an exception to it:
#
#   1. `application-question` / `application-label` are **semantic, stable**
#      names Lever uses in its own stylesheet selectors — not build-hashed like
#      Ashby's `_input_80epu_28` or Greenhouse's `remix-css-1a0ro4n-*`. The
#      hazard the rule guards against is a name that changes every deploy.
#   2. It is a **containment** relationship, scoped to one
#      `li.application-question`: the same class of evidence as a wrapping
#      `<label>`, which this module already trusts. It is NOT proximity — no
#      "nearest preceding sibling", no distance, no ordering.
#   3. Refusing to read it was not failing safe. On the captured Palantir form
#      the two `BLOCKING_KINDS` questions ("legally authorized to work…",
#      "…require sponsorship…") came back with `label=""`, which means Task 7
#      would report a form as ready-to-review with work authorization and
#      sponsorship silently unlabelled — and two *different* questions both
#      surfaced as the placeholder text "Type your response". A confidently
#      wrong label is worse than a class-name dependency.
#
# Scope is deliberately narrow: the block must be an `li` carrying
# `application-question`. It is not generalized to arbitrary sibling divs.
_QUESTION_BLOCK_CLASS = "application-question"
_QUESTION_LABEL_CLASS = "application-label"
_QUESTION_TEXT_CLASS = "text"

# Lever's voluntary EEO self-identification block reuses `application-label`
# for its questions, so the containment rule would happily read "What is your
# gender?" straight out of it. Excluded structurally, ahead of everything else:
# a control anywhere under `.eeo-survey` is not a question this module reports.
_EEO_BLOCK_CLASS = "eeo-survey"

# Required markers to strip from a stored label. `span.required` renders U+2731
# HEAVY ASTERISK on Lever and a plain "*" on Greenhouse, and that character ends
# up inside the label text because it is a child of the label element. Leaving it
# there is not cosmetic: the stored `label` is what Task 3's resolver phrase-
# matches on and what Task 7 shows the human.
_LABEL_MARKER_STRIP_RE = re.compile(
    r"^[\s" + re.escape(_REQUIRED_MARK_CHARS) + r"]+|[\s" + re.escape(_REQUIRED_MARK_CHARS) + r"]+$"
)


def _clean_label(text: str) -> str:
    """The label as a human reads it: required marker removed, whitespace
    collapsed, original casing kept.

    Applied to EVERY label source, not just the containment one — Greenhouse
    stores "First Name*" and Lever "Full name✱", and both must come back as the
    words alone. Note this runs AFTER `_required_of` has seen the raw text, so
    stripping the marker does not destroy the `label-marker` required signal.
    """
    return _collapse(_LABEL_MARKER_STRIP_RE.sub("", _collapse(text)))


def _in_eeo_block(node: _Node) -> bool:
    """True if `node` sits inside a voluntary EEO self-identification block."""
    return any(a.has_class(_EEO_BLOCK_CLASS) for a in node.ancestors())


def _question_block_label(node: _Node) -> str:
    """The question text paired with `node` inside one `li.application-question`.

    Returns "" unless the control has such an ancestor AND that block contains
    an `application-label` element — so this is inert on every board except
    Lever, and inert on a Lever block that does not follow the shape.

    Prefers the label element's inner `div.text` when present (Lever's custom
    questions nest one level deeper than its built-in fields do), and keeps the
    `span.required` marker in the returned text so the required inference can
    still see it; `_clean_label` removes it afterwards.
    """
    block = None
    for ancestor in node.ancestors():
        if ancestor.tag == "li" and ancestor.has_class(_QUESTION_BLOCK_CLASS):
            block = ancestor
            break
    if block is None:
        return ""

    for desc in block.descendants():
        if not desc.has_class(_QUESTION_LABEL_CLASS):
            continue
        inner = next(
            (d for d in desc.descendants() if d.has_class(_QUESTION_TEXT_CLASS)), None
        )
        text = _text_of(inner if inner is not None else desc, stop_tags=_LABEL_STOP)
        if text:
            return text
    return ""


def _first_labelable(node: _Node) -> _Node | None:
    """The first labelable descendant of `node`, per HTML's definition of what a
    wrapping `<label>` labels. `<select>` counts even though `_LABEL_STOP`
    excludes its *text*."""
    for desc in node.descendants():
        if _is_fillable(desc):
            return desc
    return None


def _associated_label_text(
    node: _Node,
    by_for: dict[str, list[_Node]],
    by_id: dict[str, list[_Node]],
    name_counts: dict[str, int],
) -> tuple[str, str]:
    """Chain step 1: the control's own `<label>`. Returns (text, sub-source).

    (a) explicit `for=`→`id` wins over (b) a wrapping `<label>` when both exist,
    because a board that writes both means the explicit one. (c) a *dangling*
    `for` that matches this control's unique `name` is the last resort within
    this step — it repairs malformed board HTML rather than trusting it.

    Two labels claiming the same `id`, or a duplicated `id`, yields "" for that
    sub-step: ambiguous, so we fall through rather than pick one.
    """
    def for_id() -> tuple[str, str]:
        node_id = node.attr("id").strip()
        if node_id and len(by_id.get(node_id, [])) == 1:
            labels = by_for.get(node_id, [])
            if len(labels) == 1:
                return _text_of(labels[0], stop_tags=_LABEL_STOP), "label"
        return "", ""

    # Steps 1b and 1c: the wrapping `<label>` and Lever's question block. Which
    # of the two goes first depends on what a wrapping label CONVENTIONALLY
    # holds for this kind of control, and both conventions show up on the one
    # Lever form:
    #
    #   * around a checkbox/radio it holds the OPTION text
    #     (`<label><input type=radio value=Yes><span>Yes</span></label>`), and
    #     the question ("Are you legally authorized to work…") is the block's.
    #     So for a choice control the wrapping label wins, and the block becomes
    #     the control's `group_label`.
    #   * around anything else it holds the field label — but Lever's résumé
    #     label also encloses the upload button, so it reads
    #     "Resume/CV ✱ATTACH RESUME/CV" where `div.application-label` holds
    #     exactly "Resume/CV ✱". So for a non-choice control the block wins.
    is_choice = node.tag == "input" and _kind_of(node) in ("checkbox", "select")

    def wrapping() -> tuple[str, str]:
        for ancestor in node.ancestors():
            if ancestor.tag != "label":
                continue
            # Per HTML, a `<label>`'s labeled control is its FIRST labelable
            # descendant — not all of them. Without this,
            # `<label>Address<input name=street><input name=city><input name=zip>`
            # labels all three "Address", and a caller asking for "Address" gets
            # three candidates (or, worse, one wrong one once the others are
            # filtered out).
            if _first_labelable(ancestor) is not node:
                break
            before, after = _split_text_around(ancestor, node, stop_tags=_LABEL_STOP)
            text = before or after
            if text:
                return text, "label"
            break
        return "", ""

    def block() -> tuple[str, str]:
        text = _question_block_label(node)
        return (text, "question-block") if text else ("", "")

    def for_name() -> tuple[str, str]:
        name = node.attr("name").strip()
        if name and name_counts.get(name, 0) == 1 and not by_id.get(name):
            labels = by_for.get(name, [])
            if len(labels) == 1:
                return _text_of(labels[0], stop_tags=_LABEL_STOP), "label-for-name"
        return "", ""

    ordered = (
        (for_id, wrapping, block, for_name)
        if is_choice
        else (for_id, block, wrapping, for_name)
    )
    for attempt in ordered:
        text, source = attempt()
        # A sub-step whose whole content is the required marker reads as blank to
        # a human, so keep going rather than accept it.
        if text and _clean_label(text):
            return text, source
    return "", ""


def _aria_labelledby_text(node: _Node, by_id: dict[str, list[_Node]]) -> str:
    parts: list[str] = []
    for ref in node.attr("aria-labelledby").split():
        targets = by_id.get(ref, [])
        if len(targets) == 1:
            parts.append(_text_of(targets[0], stop_tags=frozenset({"select", "button"})))
    return " ".join(p for p in parts if p).strip()


def _derive_label(
    node: _Node,
    by_for: dict[str, list[_Node]],
    by_id: dict[str, list[_Node]],
    name_counts: dict[str, int],
) -> tuple[str, str]:
    """Run the documented fallback chain. Returns (raw_label_text, source_name).

    The text is RAW — required marker still attached — because `_required_of`
    needs to see it; `_clean_label` strips it for storage.

    A step only wins if its text survives `_clean_label`. A `<label>` whose whole
    content is the required marker (`<label for="x">*</label>`) contributes
    nothing a human could read, so the chain must continue to the next step
    rather than accept it and then discard the control.

    ("", "") means no step produced anything — the control is not discovered and
    not locatable. That is the designed outcome, not a failure to handle.
    """
    candidates: list[tuple[str, str]] = []
    text, sub_source = _associated_label_text(node, by_for, by_id, name_counts)
    if text:
        candidates.append((text, sub_source))
    candidates.append((node.attr("aria-label").strip(), "aria-label"))
    candidates.append((_aria_labelledby_text(node, by_id), "aria-labelledby"))
    candidates.append((node.attr("placeholder").strip(), "placeholder"))
    candidates.append((node.attr("name").strip(), "name"))
    for raw, source in candidates:
        if raw and _clean_label(raw):
            return raw, source
    return "", ""


# A grouping container, per HTML/ARIA — never a class name. `<fieldset>` is the
# HTML form-grouping element (Ashby wraps each radio question in one);
# `role="group"`/`role="radiogroup"` is its ARIA equivalent (Greenhouse wraps
# its Resume/CV uploader in `<div role="group" aria-labelledby=…>`).
_GROUP_ROLES = frozenset({"group", "radiogroup"})


def _group_container(node: _Node) -> _Node | None:
    for ancestor in node.ancestors():
        if ancestor.tag == "fieldset" or ancestor.attr("role").strip().lower() in _GROUP_ROLES:
            return ancestor
    return None


def _group_label_text(group: _Node, by_id: dict[str, list[_Node]]) -> str:
    """The shared question text for a grouping container.

    Sources, in order: `aria-labelledby` (dereferenced — Greenhouse's Resume/CV
    group points at a `<div>` reading "Resume/CV*"), `aria-label`, `<legend>`,
    and finally the group's first `<label>` that does *not* label a real control
    — a "heading" label, which is what Ashby emits for each radio question.
    All four are standard HTML/ARIA, not generated class names.

    Returns "" when none applies — for instance Lever, which groups its
    checkboxes in a bare `<ul>` and puts the question text in a sibling
    `<div class="application-label">`, reachable only via a generated class name
    that decision 1 forbids. "" propagates into a `Question` with no label,
    which the resolver leaves blank for the human. That is the correct trade:
    no label beats a guessed one.
    """
    labelledby = _aria_labelledby_text(group, by_id)
    if labelledby:
        return labelledby
    aria = group.attr("aria-label").strip()
    if aria:
        return aria
    for desc in group.descendants():
        if desc.tag == "legend":
            text = _text_of(desc)
            if text:
                return text
    for desc in group.descendants():
        if desc.tag != "label":
            continue
        target = desc.attr("for").strip()
        # A label whose `for` resolves to a real control is that control's
        # label, not the group's heading.
        if target and any(_is_fillable(t) for t in by_id.get(target, [])):
            continue
        text = _text_of(desc, stop_tags=_LABEL_STOP)
        if text:
            return text
    return ""


def _kind_of(node: _Node) -> str:
    """Element -> normalized `Question.kind`, using the same five-value
    vocabulary as `schema_greenhouse.Question` so downstream code never has to
    branch on which board a question came from.

    `input[type=radio]` maps to **select**, not a sixth kind: a radio group is a
    single choice from a fixed option list, which is exactly what `select`
    means to the resolver (Greenhouse's own `multi_value_single_select` maps
    there too). Checkboxes stay `checkbox` because they are multi-select.
    """
    if node.tag == "textarea":
        return "textarea"
    if node.tag == "select":
        return "select"
    itype = node.attr("type").strip().lower() or "text"
    if itype == "file":
        return "file"
    if itype == "checkbox":
        return "checkbox"
    if itype == "radio":
        return "select"
    return "text"


def _select_options(node: _Node) -> list[str]:
    """A `<select>`'s human-visible choices.

    Only a LEADING placeholder is dropped, and only when it carries an
    explicitly empty `value=""` (Lever's `<option value="">Select...</option>`).
    An `<option>` with no `value` attribute at all is not a placeholder — HTML
    says its value *is* its text — so `<option>Yes</option><option>No</option>`
    must yield `["Yes", "No"]`. An earlier version keyed on "`value` is empty
    and we have collected nothing yet", which latched: with no `value=`
    attributes anywhere the condition stayed true for every option and the
    question came back with an empty option list.
    """
    option_nodes = [d for d in node.descendants() if d.tag == "option"]
    options: list[str] = []
    for index, desc in enumerate(option_nodes):
        text = _text_of(desc)
        if not text:
            continue
        if index == 0 and "value" in desc.attrs and not desc.attr("value").strip():
            continue
        options.append(text)
    return options


def _required_of(node: _Node, label: str, group: _Node | None) -> tuple[bool, str]:
    """Required inference, in priority order, returning (required, signal).

    1. the `required` attribute (present at all, even as `required=""` — HTML
       boolean attributes are true by presence; Ashby writes `required=""`).
    2. `aria-required="true"` — Greenhouse's React form uses this and never
       writes a bare `required` on its visible inputs.
    3. `aria-required="true"` on the enclosing group — where Greenhouse marks
       its Resume/CV uploader, since the `<input type=file>` inside carries
       neither attribute.
    4. a trailing required marker in the label text (Lever renders "✱", U+2731,
       *not* an ASCII asterisk; Greenhouse renders "*").

    A board that signals required-ness only through CSS (Ashby's
    `_required_f7cvd_91` class on the label) is not detectable here and comes
    back `False`. That direction of error is the safe one: an unflagged required
    field just isn't highlighted for the human, whereas a wrongly-required field
    would push the resolver to invent a value.
    """
    if "required" in node.attrs:
        return True, "required-attr"
    if node.attr("aria-required").strip().lower() == "true":
        return True, "aria-required"
    if group is not None and group.attr("aria-required").strip().lower() == "true":
        return True, "group-aria-required"
    if label and _REQUIRED_SUFFIX_RE.search(unicodedata.normalize("NFKC", label).strip()):
        if normalize_label(label):  # a label that is *only* a marker proves nothing
            return True, "label-marker"
    return False, ""


# A value we refuse to build a selector from at all. Control characters are a
# CSS string parse error, and `>>` is Playwright's own selector-chaining
# operator — either could turn one selector into something that addresses a
# different element. Refusing yields `selector=None` ("the human fills this
# one"), which is the safe failure.
_UNSAFE_SELECTOR_VALUE_RE = re.compile(r"[\x00-\x1f\x7f]|>>")


def _css_escape(value: str) -> str | None:
    """Escape `value` for use inside a CSS attribute selector's double quotes,
    or return None if it cannot be safely embedded at all.

    Backslash and `"` are escaped; brackets and quotes-in-attributes are fine
    inside a quoted value, which matters because Lever names fields
    `urls[LinkedIn]` and `cards[<uuid>][field0]`.
    """
    if _UNSAFE_SELECTOR_VALUE_RE.search(value):
        return None
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_for(
    node: _Node,
    by_id: dict[str, list[_Node]],
    name_counts: dict[str, int],
    name_value_counts: dict[tuple[str, str], int],
) -> str | None:
    """A CSS selector addressing exactly this control, or `None`.

    Only `id`, `name`, and `name`+`value` are used — never `:nth-of-type` or any
    other positional form (module docstring, decision 2). `None` when none of
    them is unique, which makes the control un-fillable by design rather than
    fillable by luck.

    The `name`+`value` pair exists for radio/checkbox groups, where every member
    deliberately shares one `name` and Lever gives them no `id` at all. Without
    it, all 33 of Lever's language checkboxes and both of its Yes/No radio groups
    were reported as questions that nothing could ever act on. `value` is an
    exact attribute match that HTML requires to differ between members of a
    group, so this is identity, not position.
    """
    node_id = node.attr("id").strip()
    if node_id and len(by_id.get(node_id, [])) == 1:
        escaped = _css_escape(node_id)
        if escaped is not None:
            return f'[id="{escaped}"]'
    name = node.attr("name").strip()
    if name and name_counts.get(name, 0) == 1:
        escaped = _css_escape(name)
        if escaped is not None:
            return f'{node.tag}[name="{escaped}"]'
    value = node.attr("value")
    if name and value and name_value_counts.get((name, value), 0) == 1:
        escaped_name, escaped_value = _css_escape(name), _css_escape(value)
        if escaped_name is not None and escaped_value is not None:
            return f'{node.tag}[name="{escaped_name}"][value="{escaped_value}"]'
    return None


def parse_controls(html: str) -> list[Control]:
    """Every fillable control in `html`, in document order, each carrying its
    derived label / kind / required-ness / selector.

    Pure: a string in, dataclasses out. No browser, no network.
    """
    root = _parse(html)
    by_for, by_id = _label_index(root)

    fillable = [n for n in root.descendants() if _is_fillable(n)]
    name_counts: dict[str, int] = {}
    name_value_counts: dict[tuple[str, str], int] = {}
    for node in fillable:
        name = node.attr("name").strip()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
            value = node.attr("value")
            if value:
                pair = (name, value)
                name_value_counts[pair] = name_value_counts.get(pair, 0) + 1

    # Which grouping container (if any) each control sits in, and how many
    # fillable controls that container holds. A group heading only describes a
    # control when the group is *about* that control: one control, or a set of
    # controls sharing one `name` (a radio/checkbox group). A `role="group"`
    # wrapping five unrelated text inputs must not stamp its heading on all
    # five.
    groups: dict[int, _Node] = {}
    members: dict[int, list[_Node]] = {}
    for node in fillable:
        group = _group_container(node)
        if group is None:
            continue
        groups[id(node)] = group
        members.setdefault(id(group), []).append(node)

    # Labels first, so a weak tier's duplicates can be spotted before any
    # Control is built (see `_WEAK_LABEL_SOURCES`).
    derived: list[tuple[_Node, str, str]] = []
    for node in fillable:
        raw_label, source = _derive_label(node, by_for, by_id, name_counts)
        if raw_label:
            derived.append((node, raw_label, source))
    weak_counts: dict[str, int] = {}
    for _, raw_label, source in derived:
        if source in _WEAK_LABEL_SOURCES:
            key = normalize_label(raw_label)
            weak_counts[key] = weak_counts.get(key, 0) + 1

    controls: list[Control] = []
    for node, raw_label, source in derived:
        if source in _WEAK_LABEL_SOURCES and weak_counts.get(normalize_label(raw_label), 0) > 1:
            # Two different questions wearing one placeholder/name. Refuse both.
            continue
        # `_required_of` sees the RAW text so the trailing-marker signal survives;
        # the stored label is the cleaned one a human would read.
        label = _clean_label(raw_label)
        if not label:
            continue
        kind = _kind_of(node)
        group = groups.get(id(node))
        raw_group_label = ""
        if group is not None:
            siblings = members.get(id(group), [])
            names = {n.attr("name").strip() for n in siblings}
            # `names == {""}` is NOT a radio/checkbox group — it is several
            # nameless widget inputs that happen to share a container.
            # Greenhouse's phone field wraps a country combobox, a search box
            # and the tel input in one `role="group"`, all without a `name`;
            # without this guard all three inherited the heading "Phone".
            shared_name = len(names) == 1 and names != {""}
            if len(siblings) == 1 or shared_name:
                raw_group_label = _group_label_text(group, by_id)
        if not raw_group_label and node.tag == "input" and kind in ("checkbox", "select"):
            # Lever has neither `<fieldset>` nor `role="group"`, so a choice
            # control's heading comes from its `li.application-question` block.
            # Restricted to choice controls because that is the only case where
            # the heading differs from the control's own label, and the block is
            # per-question by construction, so it cannot over-reach the way a
            # `role="group"` wrapping several unrelated fields can.
            raw_group_label = _question_block_label(node)
        group_label = _clean_label(raw_group_label)
        required, required_source = _required_of(node, raw_label, group)
        if not required and raw_group_label:
            # A group's required-ness is marked on the heading, not on each
            # option — and on the RAW heading, since that is where the marker is.
            group_required, group_source = _required_of(node, raw_group_label, group)
            if group_required:
                required, required_source = True, group_source
        controls.append(
            Control(
                tag=node.tag,
                input_type=(node.attr("type").strip().lower() or "text") if node.tag == "input" else "",
                label=label,
                label_source=source,
                kind=kind,
                required=required,
                required_source=required_source,
                options=_select_options(node) if node.tag == "select" else [],
                name=node.attr("name").strip(),
                element_id=node.attr("id").strip(),
                selector=_selector_for(node, by_id, name_counts, name_value_counts),
                group_label=group_label,
            )
        )
    return controls


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def find_control(controls: list[Control], label_query: str) -> Control | None:
    """The one control whose label `label_query` addresses, or `None`.

    Two tiers, each independently ambiguity-checked:
      1. normalized-equal labels;
      2. labels whose tokens *start with* the query's tokens.

    Both the control's own label and its group heading are candidates, because
    the group heading is what `discover_questions` reports as the question (a
    Greenhouse file input's own label is the button word "Attach"; its group
    heading is "Resume/CV*", which is what a caller will ask for).

    `None` is returned for no match **and** for more than one match at the
    winning tier. Never a best guess: a wrong element means a wrong value typed
    into a real application (decision 3).
    """
    if not label_query or not label_query.strip():
        return None
    q_variants = _variants(label_query)
    if not q_variants:
        return None

    def texts(control: Control) -> tuple[str, ...]:
        return tuple(t for t in (control.label, control.group_label) if t)

    exact = [c for c in controls if any(_variants(t) & q_variants for t in texts(c))]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    prefix = [c for c in controls if any(_matches(label_query, t) for t in texts(c))]
    if len(prefix) == 1:
        return prefix[0]
    return None


def find_group_options(controls: list[Control], label_query: str) -> list[Control]:
    """Every member control of the radio/checkbox group headed by `label_query`.

    A multi-option group is deliberately NOT resolvable through `find_control`:
    all N members carry the same group heading, so the query is ambiguous and
    correctly returns `None` — there is no single element that *is* a radio
    group. But `discover_questions` reports the group as one question, so
    something has to bridge the two. This does: it returns the members, each
    with its own option `label` and its own `selector`, and the caller picks the
    option matching the resolver's answer.

    Returns `[]` when nothing matches, or when the query matches more than one
    distinct group — ambiguity across groups is still a refusal.
    """
    if not label_query or not label_query.strip():
        return []
    q_variants = _variants(label_query)
    if not q_variants:
        return []

    members = [
        c
        for c in controls
        if c.group_label and (_variants(c.group_label) & q_variants or _matches(label_query, c.group_label))
    ]
    if len({c.name for c in members}) > 1:
        return []
    return members


def find_by_key(controls: list[Control], key: str) -> Control | None:
    """The control whose `name` or `id` is exactly `key`, or `None`.

    This is the **Greenhouse fallback path**, and it is not label matching at
    all: for Greenhouse the questions come from the JSON schema
    (`schema_greenhouse.parse_questions`), whose `Question.key` is Greenhouse's
    own field `name` — and Greenhouse renders that same string as the input's
    `id` (`first_name`, `resume`, `question_68177703`). So an exact identifier
    match is available and is strictly better than matching prose. `name` is
    checked before `id` because `name` is what the schema's key came from.

    Exact only, never normalized, and `None` on any tie — an identifier either
    matches or it does not.

    `name` and `id` are searched **together**, in one pass, and an `id` match is
    accepted only when that control does not declare a *different* `name`.

    Both halves of that matter. Searching `name` first and falling through to
    `id` looked equivalent and was not: a control filtered out of `controls`
    (hidden, aria-hidden, reCAPTCHA) frees its `name` to be answered by an
    unrelated element's `id`. Concretely, `<input type="hidden" name="email">`
    next to `<input id="email" name="applicant_name">` resolved key "email" to
    the *name* box — on the Greenhouse path, so the email address would have been
    typed into the full-name field. Requiring the id-matched control to have no
    conflicting `name` closes it: a control that declares `name="applicant_name"`
    has told us what it is, and it is not "email".

    Greenhouse's visible inputs carry an `id` and no `name` at all, which is why
    the `id` route has to exist for the Greenhouse fallback to work.
    """
    if not key or not key.strip():
        return None
    key = key.strip()
    hits = [
        c
        for c in controls
        if c.name == key or (c.element_id == key and not c.name)
    ]
    return hits[0] if len(hits) == 1 else None


def find_selector(controls: list[Control], label_query: str) -> str | None:
    """`find_control` narrowed to the CSS selector, or `None` if the control was
    not found *or* was found but has nothing unique to address it by."""
    control = find_control(controls, label_query)
    return control.selector if control else None


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _slugify(label: str) -> str:
    slug = normalize_label(label).replace(" ", "_")
    return slug or "question"


def _dedupe_keys(questions: list[Question]) -> list[Question]:
    """Same contract as `schema_greenhouse._dedupe_keys`: the first user of a
    key keeps it verbatim, later collisions get a stable `__N` suffix. Kept
    local rather than imported because it is that module's private helper."""
    seen: dict[str, int] = {}
    out: list[Question] = []
    for q in questions:
        seen[q.key] = seen.get(q.key, 0) + 1
        out.append(q if seen[q.key] == 1 else replace(q, key=f"{q.key}__{seen[q.key]}"))
    return out


def _questions_from(html: str) -> list[Question]:
    """Every question on the form, EEO screening not yet applied. Private —
    `discover_questions` / `excluded_eeo_questions` are the two ways out."""
    controls = parse_controls(html)

    # Bucket radio/checkbox controls by shared `name`, keeping first-appearance
    # order for everything.
    units: list[list[Control]] = []
    slot: dict[str, int] = {}
    for control in controls:
        choice = control.tag == "input" and control.kind in ("checkbox", "select") and control.name
        if choice and control.name in slot:
            units[slot[control.name]].append(control)
            continue
        if choice:
            slot[control.name] = len(units)
        units.append([control])

    questions: list[Question] = []
    for unit in units:
        first = unit[0]
        if len(unit) > 1:
            # A real choice list: the question is the group heading (possibly
            # "" — see `_group_label_text`) and the members are its options.
            label, options = first.group_label, [c.label for c in unit]
        elif first.group_label:
            # A single-member group. If the member is a choice control (a lone
            # checkbox or radio) its own label is the one option; a `<select>`
            # or text field keeps whatever options it has of its own. Reading
            # `first.options` for a radio gave `[]`, because a radio has no
            # `<option>` children — the option text is its label.
            is_choice = first.tag == "input" and first.kind in ("checkbox", "select")
            label = first.group_label
            options = [first.label] if is_choice else list(first.options)
        else:
            # A lone checkbox with no group heading is a consent box: its own
            # label *is* the question, and it has no option list.
            label, options = first.label, list(first.options)
        questions.append(
            Question(
                key=first.name or first.element_id or _slugify(label),
                label=label,
                required=any(c.required for c in unit),
                kind=first.kind,
                options=options,
            )
        )

    return _dedupe_keys(questions)


def discover_questions(html: str) -> list[Question]:
    """The form's answerable questions, as `schema_greenhouse.Question` objects.

    Reuses Task 2's `Question` deliberately: a Lever question and a Greenhouse
    question must be the same type, or the resolver (Task 3) and the fill
    executor (Task 6) would each need two code paths — and one of them would rot.

    Radio/checkbox controls sharing a `name` are collapsed into a **single**
    question whose `options` are the member labels, because that is what they
    are: one question with N choices. Emitting one question per checkbox would
    hand the resolver 33 questions labelled "English (ENG)", "Spanish (SPA)", …
    for Lever's one "Language Skill(s)" question.

    Two classes of question are **withheld** from this list. Neither is silently
    dropped; each has its own accessor, so a caller can report the counts.

    1. **Voluntary EEO self-identification** (race / gender / veteran /
       disability / pronouns), using the same content backstop as
       `schema_greenhouse.parse_questions` (`is_eeo_label`). Greenhouse publishes
       those in a separate `demographic_questions` array that Task 2 simply never
       reads; Lever and Ashby put everything in one DOM with no such separation,
       so this label screen is the only thing standing between a
       protected-characteristic question and the resolver. See
       `excluded_eeo_questions`.

    2. **Questions with no readable label at all.** This is a safety property,
       not tidiness. The EEO screen matches on the label — so a question whose
       label is empty *cannot be screened*, and Lever produces exactly that
       shape: a `<ul>` of checkboxes whose title lives in a sibling
       `<div class="application-label">` that decision 1 forbids reading. A
       label-less "Gender: Male / Female / Decline to self-identify" would sail
       through an EEO screen that only ever looks at `label`. Since nothing
       downstream can legitimately answer a question with no prompt anyway
       (Task 3's resolver classifies by label and returns blank), keeping such a
       question in the *answerable* list only ever asserted something false.
       So the invariant is: **every question returned here has a non-empty
       human-readable label.** See `unreadable_questions`.
    """
    return [
        q
        for q in _questions_from(html)
        if normalize_label(q.label) and not is_eeo_label(q.label)
    ]


def excluded_eeo_questions(html: str) -> list[Question]:
    """Questions withheld from `discover_questions` by the EEO content screen.

    Mirrors `schema_greenhouse.excluded_eeo_questions` exactly, so both discovery
    paths have the same shape: never hand this list to a resolver. It exists only
    so a caller can report "N questions were withheld as protected-characteristic
    content" instead of the questions vanishing.
    """
    return [q for q in _questions_from(html) if is_eeo_label(q.label)]


def unreadable_questions(html: str) -> list[Question]:
    """Questions withheld from `discover_questions` for having no label.

    These are real form fields the human must fill by hand: the module could see
    the control (and its options), but could not read what it is asking without
    resorting to a generated class name or a proximity guess. Surfacing them is
    the point — a handoff that says "4 questions on this form could not be read"
    is honest, whereas silently returning 24 of 28 questions is not.
    """
    return [q for q in _questions_from(html) if not normalize_label(q.label)]


# ---------------------------------------------------------------------------
# Playwright adapter (the only browser-aware code in this module)
# ---------------------------------------------------------------------------


class PageLocator:
    """Thin adapter running the pure logic above against a live Playwright page.

    Everything interesting happens in `parse_controls` / `find_control`; this
    class only turns a `Page` into HTML and a selector back into a `Locator`.
    That split is what lets the whole matching story be tested against saved
    fixtures with no browser.

    Read-only, like the rest of this module: it hands back `Locator` objects and
    never acts on them. Filling is Task 6's job, and submitting is nobody's.
    """

    def __init__(self, page: Any) -> None:
        self._page = page
        self._controls: list[Control] | None = None

    def refresh(self) -> list[Control]:
        """Re-read the DOM. Needed because these forms are client-rendered and
        mount fields progressively, so a snapshot taken too early is stale."""
        self._controls = parse_controls(self._page.content())
        return self._controls

    @property
    def controls(self) -> list[Control]:
        if self._controls is None:
            return self.refresh()
        return self._controls

    def questions(self) -> list[Question]:
        return discover_questions(self._page.content())

    def locator_for_label(self, label_query: str) -> Any | None:
        """A Playwright `Locator` for the control labelled `label_query`, or
        `None` if it was not found unambiguously / has no unique selector.

        The returned locator is also verified to resolve to exactly one element
        on the live page — a selector that matches two nodes is treated as a
        miss, not as "take the first".
        """
        return self._single(find_selector(self.controls, label_query))

    def locator_for_key(self, key: str) -> Any | None:
        """Same, addressed by the field's own `name`/`id` — the Greenhouse
        fallback, where the schema already knows the identifier
        (see `find_by_key`)."""
        control = find_by_key(self.controls, key)
        return self._single(control.selector if control else None)

    def _single(self, selector: str | None) -> Any | None:
        if not selector:
            return None
        locator = self._page.locator(selector)
        try:
            if locator.count() != 1:
                return None
        except Exception:
            return None
        return locator
