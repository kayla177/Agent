"""Decide what value — if any — goes into each application-form field.

This module is the safety boundary of Phase B. Everything downstream just
types what it says, so this is the one place where "never invent a value" is
enforced. Two properties make that enforceable:

1. It is PURE. A question list plus a profile dict in, a list of `Answer`
   objects out. No browser, no network, no local model, no database — not even
   a read of the profile table (the caller passes the row in). A test greps
   this module's own source for I/O libraries to keep it that way, because the
   tempting "just look it up" shortcut is exactly how a boundary like this
   rots.

2. It only ever copies or mechanically derives a value from a field the user
   typed. Anything else becomes `source="blank"` with a `note` saying why, and
   the handoff (Task 7) shows those notes to the user. A blank field is always
   a safe answer — the human is going to review the form and press Submit
   themselves — whereas a guess is submitted to a real employer under their
   name.

Work authorization is the sharpest edge here, and the user's instruction on it
was explicit: it is never to be guessed. Those questions are what actually
gate an application, so:

  - they are classified BEFORE the free-text rule, so a work-auth question
    phrased as a textarea can never be handed to the drafting node;
  - an unset profile field means blank, marked blocking, never "No";
  - `select`/`checkbox` answers must match one of the question's OWN options
    exactly; if nothing matches, the answer is blank. Picking the
    closest-looking option is precisely the failure mode to avoid;
  - the two conditional statuses (`f1_opt`, `tn_eligible`) never get a bare
    Yes/No — see `_CONDITIONAL_STATUS_TEXT`.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module writes no browser code at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents.job_applier.schema_greenhouse import Question

# Where an answer came from. The resolver itself only ever emits "profile" or
# "blank"; "drafted" belongs to the drafting node (Task 5) and is listed here
# so both nodes share one vocabulary and the handoff can render all three.
SOURCES: tuple[str, str, str] = ("profile", "drafted", "blank")


@dataclass(frozen=True)
class Answer:
    """One resolved form field.

    `kind` is the resolver's own classification of the question (see
    `classify`), carried on the answer so the handoff can group and flag
    without re-deriving it — notably to find the `BLOCKING_KINDS`.
    """

    question: Question
    value: str
    source: str  # one of SOURCES
    note: str = ""
    kind: str = "other"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
# An ORDERED list of (kind, pattern). First match wins, so the list is sorted
# specific -> generic, and every pattern is word-boundary anchored. Both of
# those are load-bearing:
#
#   * Word boundaries, not substrings. agents/job_scraper/locations.py learned
#     this the hard way: a substring check for "uk" matches inside
#     "Milwaukee". Here the equivalent traps are "school" inside "Schoology"
#     and "name" inside "Current Company Name".
#
#   * Order. "Legal Name (if different than above)" must be caught by the
#     specific `name_alt` rule before any generic name rule retypes the
#     user's name into it; "Expected graduation date from your university"
#     is a graduation date, not a school; a question mentioning both LinkedIn
#     and a personal website is a LinkedIn question. The work-auth family sits
#     near the top so nothing can ever steal one of those questions.


def _rx(*alternatives: str) -> re.Pattern[str]:
    """Case-insensitive, word-boundary-anchored alternation of label phrases."""
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b", re.IGNORECASE)


# A whole label that is nothing but "Name" / "Your name" / "Full name*".
# Deliberately anchored: a bare `\bname\b` rule would also swallow "Current
# Company Name" and "Name of your university" and type the applicant's own
# name into them.
_BARE_NAME_RE = re.compile(r"^[\s*]*(?:your\s+|full\s+|legal\s+)?name[\s*:?]*$", re.IGNORECASE)

_LABEL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "Legal Name (if different than above)", "Preferred name (if different)".
    # Conditional on something this system cannot know (whether the user's
    # legal name differs from the name they typed), so it is never filled.
    ("name_alt", re.compile(
        r"\b(?:legal|preferred|other|maiden)\s+name\b.*\bdiffer", re.IGNORECASE)),

    # The work-authorization family, first among the substantive rules.
    #
    # `citizenship` is separate from `work_auth` because the two questions are
    # NOT interchangeable: mapping a `permanent_resident` status onto "Are you
    # a US citizen?" as a Yes would be a false legal statement on a real
    # application. It exists as its own kind, rather than falling through to
    # "other", for two reasons: it is always blocking, and — crucially — a
    # citizenship question phrased as a textarea would otherwise reach the
    # `free_text` rule and be handed to a model to write. No status question
    # ever goes to a model.
    ("citizenship", _rx(
        r"citizens?(?:hip)?", r"immigration\s+status", r"visa\s+status",
        r"green\s+card", r"permanent\s+resident(?:cy|ce)?", r"nationality",
    )),
    ("work_auth", _rx(
        r"work\s+authoriz(?:ation|ed)",
        r"authoriz(?:ation|ed)\s+to\s+work",
        r"authoriz(?:ation|ed)\s+for\s+employment",
        r"legally\s+(?:able|permitted|entitled|allowed)\s+to\s+work",
        r"eligible\s+to\s+work",
        r"employment\s+eligibility",
        r"right\s+to\s+work",
        r"work\s+permit",
        r"work\s+visa\s+status",
    )),
    ("sponsorship", _rx(
        r"sponsorship",
        r"sponsor\s+(?:your|a|an)\s+(?:visa|work\s+visa|h-?1b)",
        r"visa\s+support",
    )),

    # Legal consent (privacy notice, terms, GDPR). Only the human can give it.
    ("consent", _rx(
        r"acknowledge", r"acknowledgement", r"consent", r"i\s+agree",
        r"privacy\s+(?:policy|notice|statement)", r"terms\s+(?:and|&)\s+conditions",
        r"gdpr",
    )),

    # "How did you hear about this job?" — a fact about the user's own history
    # that no profile field records. Blank, and explicitly NOT drafted: a model
    # would happily invent a referrer's name.
    ("referral_source", _rx(
        r"how\s+did\s+you\s+hear", r"how\s+do\s+you\s+hear",
        r"referr?(?:al|ed\s+by)", r"source\s+of\s+referral",
    )),

    ("first_name", _rx(r"first\s+name", r"given\s+name", r"forename")),
    ("last_name", _rx(r"last\s+name", r"family\s+name", r"surname")),
    ("email", _rx(r"e-?mail(?:\s+address)?")),
    ("phone", _rx(r"phone", r"telephone", r"mobile\s*(?:number)?", r"cell\s*(?:phone)?")),
    ("linkedin", _rx(r"linked\s?in")),
    ("github", _rx(r"git\s?hub")),
    ("portfolio", _rx(
        r"portfolio", r"personal\s+(?:web)?site", r"personal\s+web\s+page",
        r"website", r"blog",
    )),

    # Before `school`/`degree`: "Expected graduation date from your university"
    # names a university but asks for a date.
    ("grad_date", _rx(r"graduat\w*", r"grad\s+date", r"expected\s+completion")),
    ("school", _rx(
        r"school", r"university", r"college", r"institution", r"alma\s+mater",
        r"education",
    )),
    ("degree", _rx(
        r"degree", r"major", r"field\s+of\s+study", r"discipline", r"concentration",
    )),

    # Before `location`: "willing to relocate to the job's location" is a
    # preference, not the user's current city, and mentions "location".
    ("relocation", _rx(r"relocat\w*", r"willing\s+to\s+move")),
    ("location", _rx(
        r"location", r"city", r"address", r"postal\s+code", r"zip\s+code",
        r"country\s+of\s+residence", r"where\s+are\s+you\s+(?:based|located)",
        r"current(?:ly)?\s+(?:reside|live|located|based)",
    )),

    # Free-text prompts recognisable from the label even on a single-line
    # input. The drafting node (Task 5) owns these.
    ("free_text", _rx(
        r"why\s+do\s+you", r"why\s+are\s+you", r"tell\s+us\s+about",
        r"cover\s+letter", r"describe", r"what\s+(?:interests|excites|motivates)",
        r"in\s+your\s+own\s+words",
    )),

    ("full_name", _rx(r"full\s+(?:legal\s+)?name", r"legal\s+name", r"your\s+name",
                      r"candidate\s+name")),
)

# Kinds a pure resolver can never answer AND that gate a real submission, so
# the handoff must surface every one of them for explicit human action before
# the user presses Submit. The two work-auth kinds are here even when they DID
# resolve from the profile: those are the answers that decide whether an
# application is even considered, so they get confirmed, not assumed.
BLOCKING_KINDS: frozenset[str] = frozenset(
    {"work_auth", "sponsorship", "citizenship", "consent", "file_upload"}
)

_AUTH_KINDS = frozenset({"work_auth", "sponsorship"})

# Profile field backing each straightforwardly-copied kind.
_PROFILE_FIELD_BY_KIND = {
    "full_name": "full_name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "portfolio": "portfolio_url",
    "school": "school",
    "degree": "degree",
    "grad_date": "grad_date",
    "location": "location",
}


def classify(question: Question) -> str:
    """The resolver's category for one question — the ordered mapping above.

    `kind == "file"` short-circuits (nothing in a text profile is a file), and
    `kind == "textarea"` falls through to `free_text` only AFTER every label
    rule has had its chance, so a work-authorization or referral question
    rendered as a textarea is still classified as itself and never handed to a
    model.
    """
    if question.kind == "file":
        return "file_upload"
    label = question.label or ""
    for kind, pattern in _LABEL_RULES:
        if pattern.search(label):
            return kind
    if _BARE_NAME_RE.match(label):
        return "full_name"
    if question.kind == "textarea":
        return "free_text"
    return "other"


# ---------------------------------------------------------------------------
# Country of a work-authorization question
# ---------------------------------------------------------------------------
# The bare abbreviation "US" is matched CASE-SENSITIVELY — the same trick
# locations.py uses for two-letter state codes. Lowercase "us" is the English
# pronoun, and "Do you have authorization to work with us?" must not be read
# as a question about the United States. "CA" is deliberately absent: in a job
# posting it means California at least as often as Canada.
_US_RE = re.compile(r"\b(?:united\s+states(?:\s+of\s+america)?|usa|america|american)\b",
                    re.IGNORECASE)
_US_ABBR_RE = re.compile(r"\bU\.?S\.?\b")
_CA_RE = re.compile(r"\b(?:canada|canadian)\b", re.IGNORECASE)


def _country(label: str) -> str | None:
    """"us", "ca", or None when the label names neither or BOTH.

    Both is None on purpose: "authorized to work in the US or Canada?" cannot
    be answered from one profile field, and answering it from the wrong one is
    the mistake this module exists to prevent.
    """
    is_us = bool(_US_RE.search(label) or _US_ABBR_RE.search(label))
    is_ca = bool(_CA_RE.search(label))
    if is_us and not is_ca:
        return "us"
    if is_ca and not is_us:
        return "ca"
    return None


# ---------------------------------------------------------------------------
# Work-authorization enum -> form answer
# ---------------------------------------------------------------------------
# profile_store.WORK_AUTH values are INTERNAL enum strings, never text to type
# into a form. A form either asks a yes/no question or offers its own option
# strings, so each status maps to an intended answer here and the option gate
# below decides whether that answer can actually be used.
#
# citizen / permanent_resident are unambiguously authorized and unambiguously
# need no sponsorship. needs_sponsorship is the unambiguous opposite.
_WORK_AUTH_YES_NO = {
    "citizen": "Yes",
    "permanent_resident": "Yes",
    "needs_sponsorship": "No",
}
_SPONSORSHIP_YES_NO = {
    "citizen": "No",
    "permanent_resident": "No",
    "needs_sponsorship": "Yes",
}

# f1_opt and tn_eligible are CONDITIONAL: whether either counts as "authorized"
# or as "needing sponsorship" depends on visa specifics this system does not
# model (OPT/STEM-OPT expiry and whether the employer will later petition; for
# TN, whether the job title is on the USMCA schedule and that status is granted
# at entry rather than held in advance). Asserting "Yes" could put a false
# claim on a real application; asserting "No" could disqualify a candidate who
# is in fact employable. So neither is asserted: on a free-text field the
# resolver states the status the user actually typed — a faithful rendering of
# a profile field, not a guess — and flags it for review; on a select the
# status matches no option, so the answer goes blank (see the option gate).
_CONDITIONAL_STATUS_TEXT = {
    "f1_opt": "F-1 student status with OPT work authorization",
    "tn_eligible": "Canadian citizen, eligible for TN status under USMCA",
}


def _blank(question: Question, kind: str, note: str) -> Answer:
    return Answer(question=question, value="", source="blank", note=note, kind=kind)


def _normalize_option(text: str) -> str:
    """Compare option text ignoring case, surrounding space and trailing
    punctuation ("Yes." == "yes"), and nothing else. Deliberately NOT fuzzy:
    "Yes, I am authorized and do not need sponsorship" is a different claim
    from "Yes" and must not be selected on the strength of a shared prefix.
    """
    return re.sub(r"[\s.!*:]+$", "", text.strip().casefold())


def _emit(question: Question, kind: str, value: str, note: str = "") -> Answer:
    """Return a profile-sourced answer, subject to the option gate.

    For `select`/`checkbox` questions the value must equal one of the
    question's own options (after `_normalize_option`), and the option's exact
    text is what gets returned so the executor can match it in the form. No
    match means blank with a note — never the closest-looking option.
    """
    if question.kind in ("select", "checkbox"):
        match = next(
            (opt for opt in question.options
             if _normalize_option(opt) == _normalize_option(value)),
            None,
        )
        if match is None:
            prefix = "work authorization — " if kind in _AUTH_KINDS else ""
            return _blank(
                question,
                kind,
                f"{prefix}your profile implies “{value}”, but none of this "
                f"question's options matches it exactly — choose an option yourself.",
            )
        value = match
    return Answer(question=question, value=value, source="profile", note=note, kind=kind)


def _resolve_auth(question: Question, profile: dict, kind: str) -> Answer:
    """Resolve a work-authorization or sponsorship question.

    Never reached by the drafting node and never allowed to guess: every path
    that isn't a direct read of a typed status ends in a blank with a note the
    handoff can list as blocking.
    """
    country = _country(question.label or "")
    if country is None:
        return _blank(
            question, kind,
            "work authorization — this question does not name a single country "
            "(US or Canada), so I cannot tell which profile field applies; "
            "answer it yourself.",
        )
    auth_field = f"{country}_work_auth"
    status = str(profile.get(auth_field) or "").strip()

    # needs_sponsorship is an int 0/1 and DEFAULTS to 0, so a bare 0 on an
    # otherwise-empty profile means "the user never told us", not "the user
    # does not need sponsorship". An explicit 1, by contrast, can only have
    # been ticked deliberately, and "Yes" is the safe direction to trust: the
    # harmful error on a real application is claiming sponsorship ISN'T needed.
    wants_sponsorship = str(profile.get("needs_sponsorship") or "0") not in ("", "0", "False", "false")

    if wants_sponsorship and status in ("citizen", "permanent_resident"):
        return _blank(
            question, kind,
            f"work authorization — your profile conflicts: it says you need "
            f"sponsorship but {auth_field} is “{status}”. Fix the profile or "
            f"answer this question yourself.",
        )

    if not status:
        if wants_sponsorship:
            # The only thing typed is "I need sponsorship" — enough to answer a
            # sponsorship question, never enough to claim authorization.
            if kind == "sponsorship":
                return _emit(question, kind, "Yes")
            return _blank(
                question, kind,
                f"work authorization — your profile says you need sponsorship but "
                f"{auth_field} is not set, so your current status is unknown; "
                f"answer this yourself.",
            )
        return _blank(
            question, kind,
            f"work authorization is not set in your profile ({auth_field}) — this "
            f"answer must be yours; it is never guessed.",
        )

    if status in _CONDITIONAL_STATUS_TEXT:
        return _emit(
            question, kind, _CONDITIONAL_STATUS_TEXT[status],
            note=(
                f"conditional status ({status}): a plain yes/no depends on visa "
                f"specifics this system does not model, so your profile's status is "
                f"stated instead — review and edit before submitting."
            ),
        )

    table = _SPONSORSHIP_YES_NO if kind == "sponsorship" else _WORK_AUTH_YES_NO
    answer = table.get(status)
    if answer is None:
        # An enum value added to profile_store.WORK_AUTH but not mapped here.
        # Blank rather than a stab at what it might mean.
        return _blank(
            question, kind,
            f"work authorization — your profile's {auth_field} value "
            f"“{status}” has no defined form answer; answer this yourself.",
        )
    return _emit(question, kind, answer)


# A label that opens with a boolean auxiliary and asks a question ("Do you
# currently live in Milwaukee?", "Have you worked at a startup before?") wants
# a yes/no, NOT a copy of a profile field — typing "Toronto, ON" into it is a
# wrong answer even though every character of it came from the profile. Only
# the unambiguously-boolean auxiliaries are listed: "Would/Will you like to
# include your LinkedIn profile…" is an invitation to paste a value, not a
# yes/no question, and Greenhouse really does phrase the LinkedIn field that
# way, so "would"/"will" must stay out of this set.
_YES_NO_LABEL_RE = re.compile(
    r"^\s*(?:do|does|did|are|is|was|were|have|has|can|could)\b.*\?", re.IGNORECASE
)


def _missing_note(field_name: str) -> str:
    return (
        f"“{field_name}” is empty in your profile — add it there, or type "
        f"this field in yourself."
    )


def _name_parts(full_name: str) -> tuple[str, str]:
    """Split on the LAST space so multi-word given names survive: "Mary Jane
    Watson" -> ("Mary Jane", "Watson"). A single token yields (token, "") — no
    surname is ever fabricated.
    """
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _resolve_one(question: Question, profile: dict) -> Answer:
    kind = classify(question)

    if kind in _AUTH_KINDS:
        return _resolve_auth(question, profile, kind)

    if kind == "citizenship":
        # Always blank, even when a status IS typed. The profile records work
        # authorization, not citizenship or nationality, and inferring one from
        # the other ("us_work_auth is citizen, so tick US citizen") is exactly
        # the kind of chained guess that puts a false legal claim on a real
        # application. One field for the user to fill beats any chance of that.
        return _blank(
            question, kind,
            "citizenship, nationality and visa status are not stored in your profile "
            "(it records work authorization only) — this answer must be yours.",
        )

    if kind == "file_upload":
        return _blank(
            question, kind,
            "file upload — attach the file yourself in the open browser window; "
            "nothing is uploaded automatically.",
        )

    if kind == "consent":
        return _blank(
            question, kind,
            "a consent or acknowledgement only you can give — read it and tick it "
            "yourself.",
        )

    if kind == "free_text":
        return _blank(
            question, kind,
            "free-text answer left for the AI drafting step, which marks its "
            "output as AI-drafted for you to review.",
        )

    if kind == "referral_source":
        return _blank(
            question, kind,
            "how you heard about this job is a fact only you know — fill it in "
            "yourself.",
        )

    if kind == "name_alt":
        return _blank(
            question, kind,
            "this asks for a legal name only if it differs from the name above, and "
            "your profile stores one name — fill it in only if yours differs.",
        )

    if kind == "relocation":
        return _blank(
            question, kind,
            "a relocation preference is not stored in your profile — answer it "
            "yourself.",
        )

    if kind in ("first_name", "last_name"):
        first, last = _name_parts(str(profile.get("full_name") or ""))
        if not first:
            return _blank(question, kind, _missing_note("full_name"))
        if kind == "first_name":
            return _emit(question, kind, first)
        if not last:
            return _blank(
                question, kind,
                "your profile's full_name is a single word, so there is no surname to "
                "fill in — type it yourself if the form needs one.",
            )
        return _emit(question, kind, last)

    field_name = _PROFILE_FIELD_BY_KIND.get(kind)
    if field_name:
        value = str(profile.get(field_name) or "").strip()
        if not value:
            return _blank(question, kind, _missing_note(field_name))
        if question.kind in ("text", "textarea") and _YES_NO_LABEL_RE.match(question.label or ""):
            return _blank(
                question, kind,
                f"this reads as a yes/no question, so your profile's “{field_name}” "
                f"is not the answer to it — answer it yourself.",
            )
        return _emit(question, kind, value)

    return _blank(
        question, kind,
        "not derivable from a typed profile field — fill this one in yourself.",
    )


def resolve(questions: list[Question], profile: dict) -> list[Answer]:
    """One `Answer` per question, in the questions' own order.

    Reads `profile` only via `.get`, so a partial or empty dict is normal
    input, not an error — the real profile currently has most fields unset, and
    "everything blank, each with a reason" is the expected output for it.
    """
    profile = profile or {}
    return [_resolve_one(q, profile) for q in questions]


def blocking(answers: list[Answer]) -> list[Answer]:
    """The answers the handoff must make the user confirm before submitting:
    every `BLOCKING_KINDS` answer, plus any required field left blank.
    """
    return [
        a for a in answers
        if a.kind in BLOCKING_KINDS or (a.question.required and not a.value)
    ]
