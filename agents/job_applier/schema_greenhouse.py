"""Greenhouse application-form schema parser.

Greenhouse — unlike Lever and Ashby — publishes its application form as
structured JSON:
    GET boards-api.greenhouse.io/v1/boards/<token>/jobs/<id>?questions=true
so, for Greenhouse only, the field mapping can be schema-driven: deterministic
and unit-testable with no browser at all. This module turns that payload's
`questions` array into a normalized `Question` list. It parses a dict — no
browser, network, or model code lives here (see agents/job_applier/browser.py
for THE ONE RULE governing all of Phase B: no code path may ever click a
submit button. This module doesn't even open a browser).

The payload also carries `compliance`/`data_compliance`, `demographic_questions`,
and `location_questions` keys. NONE of those are folded into `parse_questions`:

  - `demographic_questions` is voluntary EEO self-identification (race,
    gender, veteran/disability status, ...). The resolver (Task 3) must never
    be handed one of these to auto-answer, so this array is never read by
    `parse_questions` at all. `demographic_questions_raw` exposes it
    separately and unparsed, purely so a future caller could surface
    "N voluntary EEO questions were skipped on this form" without this module
    ever normalizing them into `Question` objects a resolver could act on.
  - `location_questions` (latitude/longitude/free-text location) is
    geolocation plumbing that Greenhouse's own widget fills automatically;
    its fields are `input_hidden` with no human-meaningful label to derive an
    answer from.
  - `compliance` / `data_compliance` is consent metadata (e.g. GDPR retention
    flags), not a question with fields to fill.

Which ARRAY a question arrives in is not a safety property we can rely on,
though: an employer can write a custom EEO-flavored question straight into
the core `questions` array (a real ATS reviewer reproduced this with
adversarial payloads: "Race", "Gender", "Veteran Status" questions placed in
`questions` sailed straight through a version of this parser that only
excluded by ARRAY membership). So `parse_questions` also runs a **content**
backstop — `_is_eeo_question` — over every core question's `label`, in
addition to demographic_questions never being read. A question excluded by
that backstop is never silently dropped: it is retrievable via
`excluded_eeo_questions`, so a caller (or a future UI) can still show "N
questions were withheld as protected-characteristic content" — it simply
never reaches `parse_questions`'s returned list, which is the only thing a
resolver should ever be pointed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Greenhouse's raw `fields[].type` values, mapped to this module's normalized
# `kind`. A type this dict doesn't recognize (e.g. `input_hidden`, seen on
# `location_questions`, which this module never parses) falls back to "text"
# via the `.get(..., "text")` at the call site below — the most conservative
# guess for a field kind we've never seen.
_KIND_BY_FIELD_TYPE = {
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "checkbox",
    "textarea": "textarea",
    "input_text": "text",
}

# `kind` precedence when one question offers several `fields` — e.g.
# Greenhouse models Resume/CV as `[input_file, textarea]` because it offers
# both an upload AND a paste-in box. Earlier in this list wins. A file upload
# is the real, authoritative answer; the textarea is only Greenhouse's
# fallback for candidates who can't upload, so it must never be picked over a
# select/checkbox or a file input. Do NOT "simplify" this to `fields[0]` —
# a question's field ORDER in the payload is not meaningful, only field TYPE
# is, and `fields[0]` happens to be the upload here only by coincidence.
_KIND_PRECEDENCE = ["file", "select", "checkbox", "textarea", "text"]

# Voluntary EEO self-identification terms. Matched with `\b...\b` word
# boundaries ONLY — a plain substring search would repeat the exact bug this
# repo already shipped once in `agents/job_scraper/locations.py`, where a
# naive substring check for "uk" matched inside "Milwaukee". Word boundaries
# make `\bsex\b` correctly ignore "Essex" and `\brace\b` correctly ignore
# "embrace" (both preceded/followed by a word character, so no boundary
# exists there) while still catching "sex"/"race" as standalone words.
#
# This list is deliberately OVER-inclusive rather than precise: a false
# positive here just means one extra question gets punted to a human via
# `excluded_eeo_questions` instead of being auto-filled — mildly annoying,
# fully recoverable. A false negative means the resolver gets handed a real
# race/gender/disability question to answer — the exact harm this module
# exists to prevent. So e.g. a hypothetical benefits question literally
# titled "Disability Insurance Enrollment" would also be excluded by this
# list; that is an accepted, deliberate trade-off, not a bug.
#
# Only `label` text is matched — never `options`/`values` — so a benign
# question like "What was your undergraduate major?" whose SELECT options
# happen to include "Gender Studies" alongside "Biology"/"Computer Science"
# is untouched: the protected term lives in one dropdown choice among many,
# not in what the question is actually asking.
_EEO_TERMS = (
    "race",
    "ethnicity",
    "gender",
    "sex",
    "sexual orientation",
    "veteran",
    "disability",
    "pronoun",
)
_EEO_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _EEO_TERMS) + r")s?\b",
    re.IGNORECASE,
)


def _is_eeo_question(label: str) -> bool:
    """True if `label` (the question prompt only, never its options) matches
    a voluntary EEO self-identification term. See `_EEO_TERMS` above for the
    term list and the reasoning behind erring toward over-inclusion."""
    return bool(_EEO_PATTERN.search(label or ""))


def is_eeo_label(label: str) -> bool:
    """Public alias for `_is_eeo_question`, so the DOM discovery path
    (`locate_dom.discover_questions`, for Lever/Ashby, which have no schema to
    screen) applies the SAME content backstop as this module rather than
    duplicating `_EEO_TERMS` and letting the two copies drift apart."""
    return _is_eeo_question(label)


@dataclass(frozen=True)
class Question:
    """One normalized application-form question.

    `key` is the stable identifier the resolver (Task 3) and fill executor
    (Task 6) use to look up/write an answer. It is Greenhouse's own field
    `name` (e.g. "first_name", "question_68177703") whenever one exists,
    because employers reword `label` text freely but never touch `name` —
    preferring `name` is what keeps `key` stable across a job reposting.
    Two fields sharing a `name` (or both lacking one and slugifying to the
    same string) is not something Greenhouse should ever produce, but
    `_dedupe_keys` guarantees uniqueness anyway — see its docstring.
    `options` is populated only for `select`/`checkbox` kinds, since those
    are the only kinds the resolver needs an allowed-values list for; it is
    `[]` for every other kind.
    """

    key: str
    label: str
    required: bool
    kind: str  # "text" | "textarea" | "file" | "select" | "checkbox"
    options: list[str] = field(default_factory=list)


def _slugify(label: str) -> str:
    """Fallback key for the rare field with no `name` — lowercase, `_`-joined.

    Only reached when Greenhouse omits `name` entirely; every field observed
    in a real captured payload (see tests/fixtures/ats/greenhouse-questions.json)
    has one, so this path is defensive rather than the common case.
    """
    slug = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "question"


def _truthy(value: object) -> bool:
    """Coerce Greenhouse's `required` value to bool defensively.

    Real Greenhouse payloads always emit a genuine JSON boolean, so this is
    theoretical — but `bool("false")` is `True` in Python, and silently
    inverting a hypothetical string "false" into `required=True` is cheap to
    guard against, so the string case is handled explicitly rather than left
    to a bare `bool(...)`.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no")
    return bool(value)


def _pick_field(fields: list[dict]) -> dict | None:
    """Choose the one field that decides a question's `kind`, per the
    file > select > checkbox > textarea > text precedence documented above.

    Returns None when `fields` is empty — a question with nothing to fill.
    """
    if not fields:
        return None
    return min(
        fields,
        key=lambda f: _KIND_PRECEDENCE.index(
            _KIND_BY_FIELD_TYPE.get(f.get("type", ""), "text")
        ),
    )


def _build_question(q: dict) -> Question | None:
    """Build a `Question` from one raw entry of a `questions`-shaped array.

    Shared by `parse_questions` and `excluded_eeo_questions` so the
    key/kind/options logic exists in exactly one place. Returns None when the
    entry has no usable field (nothing for a resolver to fill).
    """
    chosen = _pick_field(q.get("fields") or [])
    if chosen is None:
        return None
    kind = _KIND_BY_FIELD_TYPE.get(chosen.get("type", ""), "text")
    label = q.get("label") or ""
    key = chosen.get("name") or _slugify(label)
    options: list[str] = []
    if kind in ("select", "checkbox"):
        options = [v.get("label", "") for v in chosen.get("values") or []]
    return Question(
        key=key,
        label=label,
        required=_truthy(q.get("required", False)),
        kind=kind,
        options=options,
    )


def _dedupe_keys(questions: list[Question]) -> list[Question]:
    """Disambiguate colliding `key`s deterministically and cheaply.

    Two distinct Greenhouse fields sharing a `name` (or two fields both
    missing `name` and slugifying to the same string, e.g. two questions
    both labelled "Additional Comments") should never legitimately happen,
    but it cannot be assumed — a wrong resolver answer landing in the wrong
    box on a REAL application is worse than a slightly uglier key. So: the
    first question to use a given key keeps it completely unchanged (the
    collision-free common case never churns downstream consumers), and every
    later question that collides on the same key gets a stable `__N` suffix
    by order of appearance.
    """
    seen: dict[str, int] = {}
    out: list[Question] = []
    for q in questions:
        seen[q.key] = seen.get(q.key, 0) + 1
        if seen[q.key] == 1:
            out.append(q)
        else:
            out.append(replace(q, key=f"{q.key}__{seen[q.key]}"))
    return out


def parse_questions(payload: dict) -> list[Question]:
    """Parse a Greenhouse job-detail payload's `questions` array.

    A missing or empty `questions` array is not an error — it just means the
    posting has no custom questions, and `[]` is returned. Only the core
    `questions` array is read (`demographic_questions`, `location_questions`,
    `compliance`/`data_compliance` are never even looked at — see the module
    docstring). On top of that array-membership exclusion, every core
    question's `label` is also screened by `_is_eeo_question` as a content
    backstop: a question that matches is withheld from this list (retrievable
    via `excluded_eeo_questions` instead) regardless of which array Greenhouse
    put it in.
    """
    out: list[Question] = []
    for q in payload.get("questions") or []:
        if _is_eeo_question(q.get("label") or ""):
            continue
        built = _build_question(q)
        if built is not None:
            out.append(built)
    return _dedupe_keys(out)


def excluded_eeo_questions(payload: dict) -> list[Question]:
    """Core-array questions withheld by the `_is_eeo_question` content
    backstop — parsed the same way `parse_questions` parses everything else,
    so nothing about them "silently vanishes", but returned as a SEPARATE
    list. Never hand this list to a resolver: every entry in it matched a
    protected-characteristic term (see `_EEO_TERMS`) and exists here only so
    a caller could report, e.g., "N questions were withheld as EEO content".
    """
    out: list[Question] = []
    for q in payload.get("questions") or []:
        if not _is_eeo_question(q.get("label") or ""):
            continue
        built = _build_question(q)
        if built is not None:
            out.append(built)
    return _dedupe_keys(out)


def demographic_questions_raw(payload: dict) -> list[dict]:
    """The posting's voluntary EEO self-identification questions, UNPARSED
    and kept separate from `parse_questions` on purpose (see module
    docstring). Never hand this list to a resolver — it exists only so a
    caller could, e.g., report "N voluntary questions were present and
    skipped" without this module ever turning one into an answerable
    `Question`.
    """
    return payload.get("demographic_questions") or []


def form_url(job: dict) -> str:
    """The application form's URL, from Greenhouse's own `absolute_url` —
    the same field `agents/job_scraper/ats.py`'s `fetch_greenhouse` already
    uses to populate a scraped posting's `url`, so this stays consistent with
    how the rest of the platform names "the job's URL".
    """
    return job.get("absolute_url") or ""
