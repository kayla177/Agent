"""Draft the free-text answers on an application form — always marked, never invented.

This is the only module in Phase B that talks to a model, and the only one that
produces text a human did not write. Two properties make that safe enough to put
in front of a real employer:

1. **Every drafted answer carries the marker in its own text**, not merely in
   metadata. `source="drafted"` is set too, but `DRAFT_MARKER` is prepended to
   the returned string, so a caller that reads only `.value` — the fill executor,
   a log line, a copy-paste into the browser — still cannot lose the fact that a
   machine wrote it. Metadata is exactly the kind of signal a later layer drops;
   a first line reading "[AI-DRAFTED …]" is not. It is deliberately typed INTO
   the form: if a human somehow submits without reading, the employer sees the
   marker rather than being deceived, and the human has to delete it to submit a
   clean answer, which means they had to read it.

2. **Default-deny on the question, not just on the model's output.** Grounding is
   limited to four sources, all of them supplied by the caller — the user's
   stored profile (a narrow allowlist of fields), the job description,
   `company_research`, and the user's own experience corpus (the master résumé
   plus experience-pool docs, i.e. exactly what
   `agents/resume_generator/nodes/gather.py` assembles; it is the user's own
   prose about their own history, which is why it counts as grounding and not as
   a fourth liberty) — and a question is
   drafted only when a POSITIVE rule recognises it as one those sources can
   answer (`topic_of`). Everything else, including every phrasing nobody thought
   of, comes back `source="blank"` with a reason. The alternative shape (draft
   everything, refuse a denylist of bad questions) was rejected for the same
   reason `resolver.py` rejected it: a denylist cannot be finished. The concrete
   case that decided it is on the real captured Lever form —

       "Give us three numbers that describe you - and tell us why. They can be
        anything: a stat from your life, a score, a streak, a count, a ranking,
        a time."

   — where a model with no facts to draw on will cheerfully make three up, and
   those three numbers are then a false claim about the applicant. There is no
   phrasing rule that catches that question and its infinite siblings; there is
   a rule that catches the handful of questions we CAN ground.

   The same is true one level down: `"experience"` questions ("your proudest
   project") are draftable only to the extent the caller passes a non-empty
   experience corpus. With an empty pool the answer is blank with a reason — the
   same call `agents/resume_generator/nodes/gather.py` makes, for the same
   reason.

3. **Not every free-text box wants prose.** The same Lever form contains

       "Prompt 1: Show us something you've built that is important to you.
        What, why, and how did you create it? (90 seconds max)"

   under a section heading explaining that the answer is a URL to an unlisted
   YouTube video, and a drafted paragraph in that box is worse than a blank.

   The evidence comes from two places, in this order of authority:

     * `Question.section` — the section heading. This is the AUTHORITATIVE
       signal, and closing the gap it fills is why `Question` gained the field:
       "please submit a URL to an unlisted YouTube video" states outright what
       the box wants. `_SECTION_NOT_PROSE_RULES` is the deliberately narrower
       subset applied at this scope, because one heading match refuses every
       question in the section.
     * the label itself — a heuristic, and the only thing available on
       Greenhouse and Ashby, which publish no section headings at all. For these
       two prompts the label's whole contribution is a parenthesised
       "(90 seconds max)": a duration budget, which belongs to a recording and
       never to a paragraph.

   Each half is pinned by a test that strips the other one out. And note that
   the label also matches the `experience` rule ("something you've built"), so
   `_NOT_PROSE_RULES` is checked BEFORE the draftable topics. That ordering is
   load-bearing, and pinned by a test against the real fixture.

The anti-fabrication precedent is `agents/resume_generator/nodes/draft.py`, and
the system prompt here mirrors its discipline: real facts may be reordered,
reframed and surfaced, but employers, titles, dates, degrees, metrics and
personal anecdotes are never invented, and a model that cannot ground an answer
is instructed to say so rather than fill the space.

**Contact details in the prompt.** Stated precisely, because the absolute version
("the prompt never carries the user's phone or email") is not what the code
guarantees, and an overclaim here is the kind of thing a later reader relies on:

  * **Guaranteed.** The profile's OWN `email` and `phone` never reach a prompt.
    Two independent mechanisms: `_PROMPT_PROFILE_FIELDS` is an allowlist that
    excludes both structurally, and `_scrub_contact` removes the profile's exact
    values — the email literally, the phone by the digit GROUPS it is written in —
    from the assembled prompt whatever source they came in from. The second pass
    is not paranoia: the experience corpus IS the master résumé, whose header is a
    name, an email and a phone number, so the allowlist alone leaks both.
  * **Best-effort.** Any email in any text is removed (an email shape is
    unambiguous). ANY OTHER phone number — a referee's, a previous employer's — is
    caught only when it is written in one of two recognised shapes: `+`-prefixed
    international, or 3-3-4 NANP. Known survivors, measured: a bare
    `4165559999` (no separators, not the user's own number) and non-NANP grouping
    such as `0044 20 7183 8750`. Unicode dashes and non-breaking spaces used to be
    survivors too and are now folded to ASCII before matching, which is what
    closed `416‑555‑9999` (U+2011) and `416–555–9999` (en dash).
  * **Deliberately not attempted.** Redacting every digit run that could be a
    phone number. The grounding text is the user's résumé, and over-redaction
    destroys the metrics the draft exists to surface — see the comment above
    `_own_number_res` for the three real figures an earlier, laxer version ate.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module writes no DOM code at all — it never sees a page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from agents.job_applier.resolver import BLOCKING_KINDS, Answer, classify
from agents.job_applier.schema_greenhouse import Question
from shell.model_router import llm

# ---------------------------------------------------------------------------
# The marker
# ---------------------------------------------------------------------------
# Kept as ONE constant used by both the writer and `is_marked`, so the string a
# caller searches for and the string this module writes cannot drift apart. The
# em-dash and the ALL-CAPS are intentional: this has to survive being pasted
# into a plain textarea and still read as an alarm to a human skimming the form.
DRAFT_MARKER = "[AI-DRAFTED — REVIEW AND EDIT THIS BEFORE YOU SUBMIT]"

# Separator between the marker and the draft. A blank line, so the marker is
# unmistakably its own line rather than the first words of the answer.
_MARKER_SEP = "\n\n"


def mark(text: str) -> str:
    """Prefix `text` with `DRAFT_MARKER`. The only place a draft becomes a value."""
    return f"{DRAFT_MARKER}{_MARKER_SEP}{text}"


def is_marked(value: str) -> bool:
    """True when `value` carries the AI-drafted marker.

    Exists so downstream code (the handoff report, the fill executor) tests for
    the marker through this module rather than hardcoding the literal a second
    time.
    """
    return DRAFT_MARKER in (value or "")


# ---------------------------------------------------------------------------
# What kind of free-text question is this?
# ---------------------------------------------------------------------------
# Topics, and what each one means for drafting:
#
#   "motivation"    — why this company / role / team. Groundable in the job
#                     description plus `company_research`. DRAFTED.
#   "experience"    — the applicant's own projects, accomplishments, a time when.
#                     Groundable ONLY in the experience corpus the caller passes.
#                     DRAFTED when that corpus is non-empty, else blank.
#   "not_prose"     — the box wants a URL, a video, a file, a number or a date.
#                     REFUSED.
#   "blocked"       — the resolver already refused it (`BLOCKING_KINDS`).
#                     REFUSED.
#   "not_free_text" — the resolver owns this question; drafting was called on it
#                     by mistake. REFUSED.
#   "unknown"       — recognised by nothing above. REFUSED. This is the
#                     default-deny bucket and it is where most real free-text
#                     prompts land, on purpose.
TOPICS: tuple[str, ...] = (
    "motivation", "experience", "not_prose", "blocked", "not_free_text", "unknown",
)

# The only two topics a model is ever asked to write.
DRAFTABLE_TOPICS: frozenset[str] = frozenset({"motivation", "experience"})


def _normalize(label: str) -> str:
    """Fold typographic punctuation so the rules below can be written in ASCII.

    Real forms use curly apostrophes: the captured Lever label is "something
    you’ve built" with U+2019, and an ASCII-only `you've` rule silently misses
    it. Same for the various dashes.
    """
    text = label or ""
    for fancy, plain in (
        ("’", "'"), ("‘", "'"), ("ʼ", "'"), ("´", "'"),
        ("“", '"'), ("”", '"'),
        ("—", "-"), ("–", "-"), ("−", "-"),
    ):
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Boxes that do not want prose
# ---------------------------------------------------------------------------
# ORDERED, and each entry carries the human-readable thing it thinks the box
# wants. The note quotes the text that MATCHED, so the user can check the claim
# instead of taking it on faith — `resolver.py`'s `name_meta` comment records
# what a confidently wrong note costs, and every rule here is a guess about
# intent made from a label alone.
#
# Vocabulary choices that are narrower than they look, and why:
#
#   * "record" is matched as a NOUN only in its `-ing`/`-ings` forms, and as a
#     VERB only when an object follows it (`record a|an|your|yourself`). Bare
#     `\brecord\b` is never matched, because "track record", "academic record",
#     "criminal record" and "record of employment" are all real phrasings and
#     none is a video prompt — and a note telling the user this question wants a
#     recording would simply be false. The verb rule requires WHITESPACE after
#     "record", which is what keeps "For the record, are you over 18?" out: the
#     comma blocks it. The `pre-?` prefix is spelled out because
#     /usr/share/dict/words has no "prerecorded"/"prerecording" entry, so the
#     stem check that justified the anchor could not have found them — the same
#     lesson as `resolver.py`'s `(?:mis)?spell`.
#   * The duration rule is DIGITS + A TIME UNIT ("90 seconds", "2 minutes"),
#     never a bare time word: "Tell us about a time when…" is the most common
#     prose prompt there is, and "…a ranking, a time." appears verbatim in the
#     three-numbers question. A word budget ("in 200 words") is deliberately not
#     matched — that is a prose question with a length limit.
#   * `\bfiles?\b` and `\blinks?\b` are word-anchored, so "profile" and
#     "filing" do not satisfy them.
#
# The third element is SECTION-SAFE: may this rule be applied to a section
# heading, where one match refuses every question in the section? THREE of the
# five rules are, and between them they cover the four things a heading can ask
# for that settle the matter — a URL, a link, a video/recording (one rule: the
# same vocabulary catches both), a file. The other two are NOT, purely because of
# blast radius: "you have 30 minutes to complete this application" and "answer
# how many of the following apply" are the kinds of thing a heading says in
# passing, and at heading scope an incidental match silently blanks a whole
# section. At label scope the same rules stay on, as a second line of defence
# for boards that publish no section heading at all (Greenhouse, Ashby).
_NOT_PROSE_RULES: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("a video or audio recording", re.compile(
        r"\bvideos?\b|\b(?:pre-?)?record(?:ing|ings)\b|\bpre-?recorded\b"
        r"|\brecord\s+(?:a|an|your|yourself)\b|\bre-?record\b"
        r"|\bself-?tape\b|\baudio\b"
        r"|\bvoice\s*(?:note|memo|recording)\b|\bwebcam\b|\bscreencast\b"
        r"|\byoutube\b|\bvimeo\b|\bloom\b|\bclips?\b",
        re.IGNORECASE), True),
    ("a recording or another timed answer, not a paragraph", re.compile(
        r"\b\d+\s*(?:seconds?|secs?|minutes?|mins?|hours?)\b", re.IGNORECASE), False),
    ("a URL or a link", re.compile(
        r"\burls?\b|\blinks?\b|\bhttps?\b|\bwww\.", re.IGNORECASE), True),
    ("a file or an attachment", re.compile(
        r"\bupload\w*\b|\battach\w*\b|\bfiles?\b|\bpdfs?\b|\bdocx?\b",
        re.IGNORECASE), True),
    ("a number, a date or a single figure", re.compile(
        r"\bhow\s+many\b|\bhow\s+much\b|\bwhat\s+year\b|\bwhich\s+year\b"
        r"|\bdate\s+of\b|\bmm\s*/\s*(?:dd|yy)|\byyyy\b|\bnumeric\b|\bgpa\b"
        r"|\b(?:salary|compensation)\s+expectations?\b"
        r"|\b(?:expected|desired|current)\s+(?:salary|compensation|base)\b",
        re.IGNORECASE), False),
)

# Derived, never hand-copied, so the two lists cannot drift apart.
_SECTION_NOT_PROSE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (wants, pattern) for wants, pattern, section_safe in _NOT_PROSE_RULES if section_safe
)

# The applicant's own history. Draftable ONLY against a non-empty experience
# corpus — with nothing to draw on, the model's only option is invention.
_EXPERIENCE_RE = re.compile(
    r"\b(?:favou?rite|proudest|greatest|biggest|hardest"
    r"|most\s+(?:significant|impactful|challenging|interesting|difficult|meaningful))"
    r"\s+(?:\w+\s+){0,2}?(?:projects?|accomplishments?|achievements?|work|contributions?)\b"
    r"|\bproudest\b"
    r"|\btell\s+us\s+about\s+(?:a|an|one|your|yourself)\b"
    r"|\bdescribe\s+(?:a|an|one|your)\b"
    r"|\bsomething\s+you(?:'ve|\s+have)?\s+(?:built|made|shipped|created|worked\s+on)\b"
    r"|\bprojects?\s+you(?:'ve|\s+have)?\s+(?:built|worked\s+on|shipped|are\s+proud\s+of)\b"
    r"|\bwalk\s+us\s+through\b"
    r"|\ba\s+time\s+(?:when|you|that)\b",
    re.IGNORECASE,
)

# Why this employer. Groundable in the posting and the company research, which
# is what makes this the one topic that needs no facts about the applicant.
_MOTIVATION_RE = re.compile(
    r"\bwhy\s+(?:do|would|are)\s+you\s+(?:want|like|interested|excited|applying|apply|keen)\b"
    r"|\bwhy\s+do\s+you\s+want\s+to\s+(?:work|join|be)\b"
    r"|\bwhy\s+(?:this|our|the)\s+"
    r"(?:company|role|team|position|job|firm|organi[sz]ation|internship|program)\b"
    r"|\bwhat\s+(?:interests|excites|attracts|draws|motivates|appeals\s+to)\s+you\b"
    r"|\bcover\s+letter\b"
    r"|\bwhy\s+(?:are\s+you\s+)?(?:a\s+)?(?:good|strong|great)\s+fit\b"
    r"|\bwhy\s+should\s+we\s+(?:hire|consider|interview)\s+you\b",
    re.IGNORECASE,
)


def _shorten(text: str, cap: int = 120) -> str:
    """`text` normalised and short enough to quote inside a note.

    Lever's video-prompt heading is 497 characters and quoting all of it buries
    the note it is supposed to explain. Measured against that heading: "please
    submit a URL to an unlisted YouTube video" — the phrase that makes the
    refusal self-evident — ends at character 91, so **92** is the shortest cap
    that reaches it. 120 is that minimum plus margin, so a slightly reworded
    heading does not silently truncate the evidence away again; 90 did, leaving
    the note arguing from "…unlisted YouTube vid…". (An earlier version of this
    docstring called 120 itself "the shortest cap that still reaches" the phrase.
    It is not — 92 is.)"""
    text = _normalize(text)
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def not_prose_reason(label: str, section: str = "") -> str:
    """The thing a non-prose box appears to want, or "" when it reads as prose.

    `section` — `Question.section`, the heading of the form section the question
    sits under — is checked FIRST and with the `_SECTION_NOT_PROSE_RULES` subset,
    because it is the AUTHORITATIVE evidence when it exists. Lever's video
    prompts are the case that forced it: their own labels say nothing except a
    parenthesised "(90 seconds max)", while the heading above them reads "Video
    Prompts: After recording your clips, please submit a URL to an unlisted
    YouTube video …". Inferring "probably a recording" from a duration is a
    heuristic; reading "submit a URL to an unlisted YouTube video" is evidence.

    The label pass stays as a second line of defence — Greenhouse and Ashby
    publish no section headings at all, so on those boards the heuristic is all
    there is.

    Public because these are the rules most likely to need checking against a
    newly captured form, and because `topic_of` collapses the result to a topic.
    """
    section_text = _normalize(section)
    if section_text:
        for wants, pattern in _SECTION_NOT_PROSE_RULES:
            found = pattern.search(section_text)
            if found:
                return (
                    f"{wants} — said by the section heading this question sits "
                    f"under, “{_shorten(section)}” (matched “{found.group(0)}”)"
                )
    text = _normalize(label)
    for wants, pattern, _ in _NOT_PROSE_RULES:
        found = pattern.search(text)
        if found:
            return f"{wants} (matched “{found.group(0)}”)"
    return ""


@dataclass(frozen=True)
class _Plan:
    """Everything decided about one question, decided ONCE.

    `kind` (the resolver's classification) was being recomputed up to four times
    per question and `not_prose_reason` twice — once to pick the topic and again
    to word the refusal. Both are pure and cheap, so this is not about speed: a
    note derived from a SECOND evaluation of a decision already made is two code
    paths that can disagree, and the note is what the user reads to check the
    refusal. Now the reason that words the note is the same string that caused it.
    """

    kind: str
    topic: str
    reason: str = ""  # the `not_prose_reason` that decided it, when it did


def _plan(question: Question) -> _Plan:
    """Classify one question — see `topic_of` for the ordering and its rationale."""
    kind = classify(question)
    if kind in BLOCKING_KINDS:
        return _Plan(kind, "blocked")
    if kind != "free_text":
        return _Plan(kind, "not_free_text")
    label = _normalize(question.label)
    reason = not_prose_reason(label, question.section)
    if reason:
        return _Plan(kind, "not_prose", reason)
    if _EXPERIENCE_RE.search(label):
        return _Plan(kind, "experience")
    if _MOTIVATION_RE.search(label):
        return _Plan(kind, "motivation")
    return _Plan(kind, "unknown")


def topic_of(question: Question) -> str:
    """This module's category for one question — one of `TOPICS`.

    Order of decision, and why each step is where it is:

      1. The resolver's own classification first, so drafting can never
         contradict it: a `BLOCKING_KINDS` question is refused outright, and a
         question the resolver claims (a name, an email, a referral source) is
         not drafting's business either. `classify` quarantines work eligibility
         before it looks at anything else, so no spelling of a work-auth
         question can arrive here as `free_text`.
      2. Non-prose shapes, BEFORE the draftable topics — the Lever video prompt
         "Show us something you've built … (90 seconds max)" matches
         `_EXPERIENCE_RE` too, and a paragraph in a box that wants a YouTube URL
         is worse than a blank. `question.section` is the authoritative evidence
         here and is checked first; see `not_prose_reason`.
      3. `experience` before `motivation`: a label mentioning both a real
         project and an interest in the company must be gated on the experience
         corpus, which is the stricter of the two.
      4. Anything unrecognised => "unknown" => blank. Default-deny.
    """
    return _plan(question).topic


# ---------------------------------------------------------------------------
# Refusal notes
# ---------------------------------------------------------------------------
# Readable phrasing for the blocking kinds, so a note never shows the user a
# machine token like "work_document".
_BLOCKED_PHRASE = {
    "work_auth": "work-authorization",
    "sponsorship": "visa-sponsorship",
    "citizenship": "citizenship, nationality or residency",
    "work_document": "visa, permit or immigration-document",
    "consent": "consent or acknowledgement",
    "file_upload": "file-upload",
}

_UNKNOWN_NOTE = (
    "left blank on purpose: the AI drafter only writes answers it can ground in "
    "your profile, the job description, or public information about the company, "
    "and it could not tell which of those — if any — answers this. Anything it "
    "wrote here would risk being invented, so write this one yourself."
)

_NO_EXPERIENCE_NOTE = (
    "this asks about your own projects or experience, and there is nothing in "
    "your experience pool for the AI to ground an answer in (add a master résumé "
    "or experience docs on the Resume tab). Inventing a project is the one thing "
    "it will not do, so write this one yourself."
)


def _blank(question: Question, note: str, kind: str) -> Answer:
    """A refusal. `kind` is the RESOLVER's kind, not the drafting topic, so a
    caller can still group by kind and find the blocking items. It is PASSED IN
    rather than re-derived: one classification per question, one answer."""
    return Answer(question=question, value="", source="blank", note=note, kind=kind)


def _refusal_note(plan: _Plan) -> str:
    """Why a question was not drafted, worded from the plan that decided it."""
    if plan.topic == "blocked":
        phrase = _BLOCKED_PHRASE.get(plan.kind, plan.kind.replace("_", " "))
        return (
            f"never drafted: this is a {phrase} question, which only you can "
            f"answer — a model must not guess it."
        )
    if plan.topic == "not_free_text":
        return (
            "not a free-text prompt — the resolver owns this field, so drafting "
            "left it alone."
        )
    if plan.topic == "not_prose":
        return (
            f"this box does not want a paragraph: it reads as a request for "
            f"{plan.reason}. A drafted paragraph would be the wrong kind of "
            f"answer here, so fill it in yourself."
        )
    return _UNKNOWN_NOTE


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------
# Profile fields the prompt may carry. An ALLOWLIST, so a field added to
# `profile_store.FIELDS` later is excluded until someone deliberately adds it —
# the same default-deny shape as the resolver.
#
# What is here and why:
#   summary   — the user's own free-text self-description; the single most useful
#               grounding text for "why do you want to work here".
#   school / degree / grad_date — real, checkable facts that let a draft say "as
#               a third-year computer-engineering student" without inventing one.
#
# What is deliberately absent:
#   email, phone            — the brief's hard requirement. See `_scrub_contact`.
#   full_name               — the form already knows it; a draft that greets the
#                             reader by the applicant's own name is noise.
#   linkedin/github/portfolio — URLs are answers to other fields, not grounding.
#   location                — not needed to answer any draftable topic, and it is
#                             quasi-identifying.
#   us_work_auth, ca_work_auth, needs_sponsorship — a model must never see, let
#                             alone restate, a work-eligibility status. Those
#                             questions are `BLOCKING_KINDS` for a reason.
_PROMPT_PROFILE_FIELDS: tuple[str, ...] = ("summary", "school", "degree", "grad_date")

# Character caps. The local model's context is pinned at `config.OLLAMA_NUM_CTX`
# (32768 tokens); these caps keep the worst-case prompt COMFORTABLY inside it —
# `test_the_worst_case_prompt_fits_the_pinned_context` pins the ratio at > 2.5x
# and measures 2.58x — so nothing is ever silently truncated by the server
# instead of visibly truncated here. Not "an order of magnitude", which this
# comment claimed, and not the "4x" a still earlier version claimed: the test
# does the arithmetic against the real config value and the prose now matches
# the number the test actually asserts.
_CAP_LABEL = 1200
_CAP_DESCRIPTION = 6000
_CAP_RESEARCH = 2000
_CAP_EXPERIENCE = 6000
_CAP_PROFILE_FIELD = 1500
_CAP_JOB_META = 200
# Every input above is capped, so this ceiling is arithmetic rather than a hope:
# 1200 + 6000 + 2000 + 6000 + (4 x 1500) + (3 x 200) + the fixed scaffolding.
_MAX_PROMPT_CHARS = 24000

_TRUNCATED = " …[truncated]"


def _clip(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[: cap - len(_TRUNCATED)] + _TRUNCATED


# ---------------------------------------------------------------------------
# Contact scrubbing
# ---------------------------------------------------------------------------
# The allowlist above keeps the PROFILE's email and phone out of the prompt, but
# the experience corpus is the master résumé and its header is a name, an email
# and a phone number — so an allowlist alone leaks both. Everything assembled
# into a prompt therefore goes through here.
#
# Over-redaction is a real cost, not a safe default: the grounding text is the
# user's own résumé, so a destroyed metric is a metric the draft cannot use — and
# the literal "[redacted]" is then sitting in the model's context where it can be
# copied into a real application. An earlier version of the own-number pass
# claimed in this very comment to be "deliberately conservative" while joining the
# phone's digits with `\D{0,2}`, i.e. allowing ANY two non-digits between every
# digit. With the profile phone `+1-555-0100` (last seven digits `5550100`) that
# redacted all three of these:
#
#     "$5,550,100 in ARR"        -> "$[redacted] in ARR"
#     "Processed 5 550 100 rows" -> "Processed [redacted] rows"
#     "Team of 5550100"          -> "Team of [redacted]"
#
# and the test that was supposed to cover it pinned one string that happened not
# to contain the digit run. So the rules are now:
#
#   * An email shape is unambiguous, so a general pattern is safe.
#   * A general phone shape is NOT — "$100,000 - 150,000", "2026-07-31" and "a
#     team of 5,000" are all digits-with-separators — so only two shapes are
#     matched generally: a `+`-prefixed international number and a 3-3-4 NANP
#     number.
#   * The user's OWN number is matched STRUCTURALLY (`_own_number_res`): the
#     digit GROUPS the profile value itself is written in, in that order, with a
#     separator allowed only BETWEEN groups and never inside one. A comma is
#     never a phone separator. That is what distinguishes a phone number from a
#     thousands-separated figure that happens to share its digits.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RES = (
    re.compile(r"\+\d[\d\s().\-]{6,}\d"),
    re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b"),
)
_REDACTED = "[redacted]"

# Characters that separate the groups of a written phone number. Deliberately
# excludes the comma (a thousands separator) and the slash. Applied AFTER
# `_fold_punctuation`, so only the ASCII hyphen needs listing.
_GROUP_SEP = r"[\s.()\-]{1,3}"

# Typographic characters a résumé really does contain, folded to ASCII before any
# matching. `_normalize` already folds these for label matching; `_scrub_contact`
# did not, and the gap was a leak: "416‑555‑9999" written with U+2011 NON-BREAKING
# HYPHEN survived the NANP rule, whose separator class is ASCII-only. Whitespace
# is NOT collapsed here — this runs over a whole prompt, and collapsing newlines
# would fuse the résumé's lines together.
_PUNCT_FOLD = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", "－": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
}
_PUNCT_FOLD_RE = re.compile("|".join(map(re.escape, _PUNCT_FOLD)))


def _fold_punctuation(text: str) -> str:
    """Unicode dashes and non-breaking spaces to their ASCII equivalents."""
    return _PUNCT_FOLD_RE.sub(lambda m: _PUNCT_FOLD[m.group(0)], text or "")


def _own_number_res(raw: str) -> list[re.Pattern[str]]:
    """Patterns matching the user's own phone number as it is actually written.

    Built from the digit GROUPS of the profile value ("+1-555-0100" -> 1 / 555 /
    0100), so a separator may appear only between groups and never inside one.
    That single constraint is what keeps "$5,550,100" and "Processed 5 550 100
    rows" intact while still catching "+1 555 0100", "+1.555.0100", "(555) 0100"
    and "+15550100".

    Two patterns are returned, and they differ in one deliberate way:

      * the FULL number, separators optional — "+15550100" is a real way to write
        a phone number;
      * the number with its leading (country/area) group dropped, separators
        REQUIRED — "555-0100" is a real way to write it, but a bare "5550100" is
        not, and "Team of 5550100" must survive. Requiring one real separator is
        exactly the difference between the two.

    Returns [] for a value with fewer than two groups or fewer than seven digits:
    there is nothing phone-shaped to match, and a short digit run would fire on
    ordinary prose.
    """
    groups = [g for g in re.split(r"\D+", _fold_punctuation(raw or "")) if g]
    if not groups or sum(map(len, groups)) < 7:
        return []
    if len(groups) == 1:
        # A profile value stored with no separators at all ("4165559999"). There
        # are no group boundaries to reason about, so match it EXACTLY, digit
        # guards included. Not expanded into hypothetical groupings: guessing
        # where a country or area code ends is how a run of digits that is not a
        # phone number gets redacted.
        return [re.compile(r"(?<!\d)" + re.escape(groups[0]) + r"(?!\d)")]
    def build(parts: list[str], sep: str) -> re.Pattern[str]:
        # The digit guards go ONCE, at each END of the whole pattern. Putting
        # `(?<!\d)` in the JOIN string instead — which an earlier draft did — makes
        # every pattern unmatchable, because the character before an inner group is
        # always a digit. It failed silently: the general `+`-prefixed rule still
        # caught "+1-555-0100", so the own-number pass looked like it worked while
        # doing nothing at all, and only "555-0100" (which only it can catch)
        # exposed it.
        return re.compile(r"(?<!\d)" + sep.join(map(re.escape, parts)) + r"(?!\d)")

    out = [build(groups, f"{_GROUP_SEP}?")]
    tail = groups[1:]
    if len(tail) >= 2 and sum(map(len, tail)) >= 7:
        out.append(build(tail, _GROUP_SEP))
    return out


def _scrub_contact(text: str, profile: dict) -> str:
    """Remove contact details from prompt text. Never returns them, redacted or not.

    Punctuation is folded FIRST so every rule below sees ASCII separators; the
    returned text carries that folding, which is a deliberate, visible change to
    dashes and non-breaking spaces rather than a hidden one.
    """
    out = _fold_punctuation(text)
    out = _EMAIL_RE.sub(_REDACTED, out)
    for pattern in _PHONE_RES:
        out = pattern.sub(_REDACTED, out)
    # The user's OWN values. This is the pass that actually catches a
    # master-résumé header, which is the leak a profile-field allowlist cannot
    # close on its own.
    profile = profile or {}
    for pattern in _own_number_res(str(profile.get("phone") or "")):
        out = pattern.sub(_REDACTED, out)
    email = _fold_punctuation(str(profile.get("email") or "").strip())
    if len(email) >= 5:
        out = out.replace(email, _REDACTED)
    return out


SYSTEM_PROMPT = (
    "You are helping a real job applicant draft ONE answer to ONE question on a "
    "real job application. A human will read, edit and submit it. Your draft must "
    "be something they can submit truthfully without correcting a single fact.\n\n"
    "ABSOLUTE RULES:\n"
    "- Use ONLY facts given to you below. Never invent or embellish employers, job "
    "titles, dates, schools, degrees, certifications, awards, projects, numbers, "
    "statistics, hobbies, anecdotes, or opinions the applicant never expressed.\n"
    "- If a number is not in the material below, do not state a number.\n"
    "- Never claim the applicant has met someone, used a product, read something, "
    "or attended an event unless the material below says so.\n"
    "- You MAY reorder, rephrase and emphasise the applicant's real experience, "
    "and mirror the posting's wording where it truthfully describes them.\n"
    "- Write about the company only from the posting and company context provided. "
    "Do not recall facts about the company from your own knowledge.\n"
    "- If the material below does not let you answer honestly, reply with exactly "
    "NOT_ENOUGH_INFORMATION and nothing else. That is a correct, expected answer, "
    "not a failure.\n\n"
    "STYLE: first person, plain specific prose, 100-200 words, no greeting, no "
    "sign-off, no headings, no bullet points, no preamble like \"Here is\". Output "
    "the answer text only."
)

# The model's own escape hatch (see SYSTEM_PROMPT). Treated as a blank, because
# an honest "I cannot ground this" is exactly the outcome this module wants and
# must not be typed into the form as if it were an answer.
_NOT_ENOUGH = "NOT_ENOUGH_INFORMATION"

# Anything shorter than this is not an answer to an open-ended question. It
# exists to stop "N/A", "Yes.", or a stray token from being typed into a real
# application with the marker glued to it — blank with a reason is better.
_MIN_DRAFT_CHARS = 20

_MAX_TOKENS = 700
_TEMPERATURE = 0.3


def build_prompt(
    question: Question,
    topic: str,
    *,
    job: dict | None = None,
    profile: dict | None = None,
    experience: str = "",
    company_research: str = "",
) -> str:
    """The user message for one question. Contains no contact details, ever.

    Sources, and nothing else: the question itself, the job record (title,
    company, location, description), `company_research`, the allowlisted profile
    fields, and — for an `experience` question — the experience corpus. The
    topic is included as a one-line instruction because "why this company" and
    "your proudest project" need different grounding emphasised, and the model
    otherwise reaches for whichever material is longest.
    """
    job = job or {}
    profile = profile or {}
    lines = [
        f"QUESTION ON THE APPLICATION FORM:\n{_clip(question.label, _CAP_LABEL)}",
        "",
        f"THIS IS AN APPLICATION FOR: "
        f"{_clip(str(job.get('title') or ''), _CAP_JOB_META) or '?'} "
        f"at {_clip(str(job.get('company') or ''), _CAP_JOB_META) or '?'}",
    ]
    if str(job.get("location") or "").strip():
        lines.append(f"LOCATION: {_clip(str(job['location']), _CAP_JOB_META)}")

    if topic == "motivation":
        lines += [
            "",
            "WHAT THIS QUESTION WANTS: why this applicant is interested in THIS "
            "company and THIS role. Ground it in the posting and the company "
            "context below. Do not claim experience that is not listed below.",
        ]
    elif topic == "experience":
        lines += [
            "",
            "WHAT THIS QUESTION WANTS: one concrete, REAL piece of the applicant's "
            "own experience, taken from the experience material below and chosen "
            "for its relevance to this role. If nothing there fits the question, "
            f"reply {_NOT_ENOUGH}.",
        ]

    description = _clip(str(job.get("description") or ""), _CAP_DESCRIPTION)
    if description:
        lines += ["", "JOB DESCRIPTION:", description]

    research = _clip(company_research, _CAP_RESEARCH)
    if research:
        lines += ["", "COMPANY CONTEXT (scraped; the only company facts you may use):", research]

    facts = [
        f"- {field}: {_clip(str(profile.get(field) or ''), _CAP_PROFILE_FIELD)}"
        for field in _PROMPT_PROFILE_FIELDS
        if str(profile.get(field) or "").strip()
    ]
    if facts:
        lines += ["", "ABOUT THE APPLICANT (from their own saved profile):", *facts]

    pool = _clip(experience, _CAP_EXPERIENCE)
    if pool and topic == "experience":
        lines += ["", "THE APPLICANT'S EXPERIENCE (the ONLY source of facts about them):", pool]

    lines += ["", "Write the answer now."]
    return _scrub_contact("\n".join(lines), profile)


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def draft_one(
    question: Question,
    *,
    job: dict | None = None,
    profile: dict | None = None,
    experience: str = "",
    company_research: str = "",
    llm_fn: Callable[..., str] | None = None,
) -> Answer:
    """One `Answer` for one question: `source="drafted"` (marked) or `"blank"`.

    Never raises. A model that is down, slow, or returns junk produces a blank
    with a reason — the same call `resume_generator`'s draft node makes, for the
    same reason: no output beats a broken one, and here a broken one would be
    typed into a real employer's form.

    `llm_fn` is the injected-model seam: pass a callable with `llm`'s signature
    and it is used instead of the module-level `llm`. It exists so a caller that
    already holds a model handle (the Task 8 graph) does not have to reach in and
    patch a module global, which is the only other way to substitute one. Tests
    use both shapes — see `test_llm_fn_is_used_instead_of_the_module_level_llm`.
    """
    plan = _plan(question)
    if plan.topic not in DRAFTABLE_TOPICS:
        return _blank(question, _refusal_note(plan), plan.kind)

    if plan.topic == "experience" and not (experience or "").strip():
        return _blank(question, _NO_EXPERIENCE_NOTE, plan.kind)

    prompt = build_prompt(
        question, plan.topic, job=job, profile=profile,
        experience=experience, company_research=company_research,
    )
    call = llm_fn or llm
    try:
        raw = call(
            "local", prompt, system=SYSTEM_PROMPT,
            temperature=_TEMPERATURE, max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:  # model down / transport error / anything at all
        return _blank(
            question,
            f"the AI drafter could not be reached, so this was left blank rather "
            f"than filled with a guess ({type(exc).__name__}: {exc}).",
            plan.kind,
        )

    text = (raw or "").strip()
    if not text:
        # Distinct from the too-short note below, which quotes what came back —
        # there is nothing to quote here, and "returned only “”" reads as a bug.
        # `_MIN_DRAFT_CHARS` would cover the SAFETY property on its own; this
        # branch exists only so the user is told which of the two happened.
        return _blank(
            question,
            "the AI drafter returned nothing at all, so this was left blank rather "
            "than filled with a guess.",
            plan.kind,
        )
    if _NOT_ENOUGH in text.upper():
        return _blank(
            question,
            "the AI drafter said it had no grounded facts to answer this with, "
            "which is the honest outcome — write this one yourself.",
            plan.kind,
        )
    if len(text) < _MIN_DRAFT_CHARS:
        return _blank(
            question,
            f"the AI drafter returned only “{text}”, which is not an answer to an "
            f"open question — left blank rather than typed into the form.",
            plan.kind,
        )

    return Answer(
        question=question,
        value=mark(text),
        source="drafted",
        note=(
            "written by the local AI model from the job posting and your own saved "
            "profile — read it, fix anything that is not true of you, and delete "
            "the marker line before submitting."
        ),
        kind=plan.kind,
    )


def draft(
    questions: list[Question],
    *,
    job: dict | None = None,
    profile: dict | None = None,
    experience: str = "",
    company_research: str = "",
    llm_fn: Callable[..., str] | None = None,
) -> list[Answer]:
    """One `Answer` per question, in the questions' own order.

    Safe to hand the WHOLE question list: anything drafting does not own comes
    back blank with a reason (see `topic_of`), so the caller does not have to
    pre-filter correctly for the safety properties to hold.
    """
    return [
        draft_one(
            q, job=job, profile=profile, experience=experience,
            company_research=company_research, llm_fn=llm_fn,
        )
        for q in questions or []
    ]
