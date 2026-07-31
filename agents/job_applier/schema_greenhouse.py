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
    be handed one of these to auto-answer, so they are kept out of the
    returned `Question` list entirely. `demographic_questions_raw` exposes
    them separately and unparsed, purely so a future caller could surface
    "N voluntary EEO questions were skipped on this form" without this module
    ever normalizing them into `Question` objects a resolver could act on.
  - `location_questions` (latitude/longitude/free-text location) is
    geolocation plumbing that Greenhouse's own widget fills automatically;
    its fields are `input_hidden` with no human-meaningful label to derive an
    answer from.
  - `compliance` / `data_compliance` is consent metadata (e.g. GDPR retention
    flags), not a question with fields to fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class Question:
    """One normalized application-form question.

    `key` is the stable identifier the resolver (Task 3) and fill executor
    (Task 6) use to look up/write an answer. It is Greenhouse's own field
    `name` (e.g. "first_name", "question_68177703") whenever one exists,
    because employers reword `label` text freely but never touch `name` —
    preferring `name` is what keeps `key` stable across a job reposting.
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


def parse_questions(payload: dict) -> list[Question]:
    """Parse a Greenhouse job-detail payload's `questions` array.

    A missing or empty `questions` array is not an error — it just means the
    posting has no custom questions, and `[]` is returned. Only the core
    `questions` array is parsed; `demographic_questions`, `location_questions`,
    and `compliance`/`data_compliance` are deliberately excluded (see the
    module docstring for why each one is unsafe or meaningless to include).
    """
    out: list[Question] = []
    for q in payload.get("questions") or []:
        chosen = _pick_field(q.get("fields") or [])
        if chosen is None:
            # No field means nothing for a resolver to fill; skip rather than
            # invent a key for a question with no answerable target.
            continue
        kind = _KIND_BY_FIELD_TYPE.get(chosen.get("type", ""), "text")
        key = chosen.get("name") or _slugify(q.get("label") or "")
        options: list[str] = []
        if kind in ("select", "checkbox"):
            options = [v.get("label", "") for v in chosen.get("values") or []]
        out.append(
            Question(
                key=key,
                label=q.get("label") or "",
                required=bool(q.get("required", False)),
                kind=kind,
                options=options,
            )
        )
    return out


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
