"""Decide what value — if any — goes into each application-form field.

This module is the safety boundary of Phase B. Everything downstream just
types what it says, so this is the one place where "never invent a value" is
enforced. Three properties make that enforceable:

1. It is PURE. A question list plus a profile dict in, a list of `Answer`
   objects out. No browser, no network, no local model, no database — not even
   a read of the profile table (the caller passes the row in). A test greps
   this module's own source for I/O libraries to keep it that way, because the
   tempting "just look it up" shortcut is exactly how a boundary like this
   rots.

2. It is DEFAULT-DENY. A profile value is emitted only when the label
   POSITIVELY matches a narrow pattern for that field AND the label is asking
   for a value rather than asking a yes/no question about one. Every other
   label — including every phrasing nobody thought of — comes out
   `source="blank"` with a note. The first version of this module was
   default-allow with a denylist of bad shapes, and a review found eight
   phrasings that leaked real values through it ("Do you currently live in
   Milwaukee" answered with the profile's city; "Will you be graduating before
   June 2027?" answered with a date; "Have you ever applied under a different
   last name?" answered with the surname). A denylist cannot be finished; an
   allowlist can.

3. Work-eligibility topics are quarantined FIRST, in any spelling, casing or
   widget. See `_ELIGIBILITY_SUBKINDS`.

A blank field is always a safe answer — the human reviews the form and presses
Submit themselves — whereas a guess is submitted to a real employer under
their name.

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
# Work eligibility: quarantined before anything else
# ---------------------------------------------------------------------------
# Everything a form might ask about the right to hold the job — authorization,
# sponsorship, citizenship, visas, permits, residency. These are the questions
# that actually gate an application, and the instruction on them was explicit:
# never guessed.
#
# Three properties are deliberate:
#
#   * Matching is CASE-INSENSITIVE and covers BOTH orthographies
#     (authoriz|authoris). A review found "ARE YOU LEGALLY AUTHORIZED TO WORK
#     WITH US?" and "Are you legally authorised to work in the US?" slipping
#     past casing- and spelling-specific rules — the first answered "No", the
#     second classified as free text and would have been handed to a model.
#
#   * An eligibility match is checked BEFORE every other rule and regardless
#     of `Question.kind`, so the textarea form of one of these can never
#     become `free_text`. That is what makes the drafting node's "refuse to
#     draft a BLOCKING_KINDS question" rule load-bearing rather than
#     decorative: these kinds are all blocking.
#
#   * The vocabulary is intentionally broad and over-matches. "Are you a
#     Citizens Bank customer?" lands here and shows up in the handoff as a
#     blocking item the user must answer themselves. That is the trade taken
#     knowingly: a spurious blocking item costs one glance, while a missed
#     citizenship question could be answered by a model on a real application.

# Mappable: asks whether the user MAY work somewhere. A work-auth status can
# answer this (subject to the country and yes/no gates below).
_ELIG_WORK_AUTH = (
    r"work\s+authoris(?:ation|ed)|work\s+authoriz(?:ation|ed)"
    r"|authoris(?:ation|ed)\s+to\s+work|authoriz(?:ation|ed)\s+to\s+work"
    r"|authoris(?:ation|ed)\s+for\s+employment|authoriz(?:ation|ed)\s+for\s+employment"
    r"|legally\s+(?:able|permitted|entitled|allowed)\s+to\s+work"
    r"|eligible\s+to\s+work|eligibility\s+to\s+work|employment\s+eligibility"
    r"|right\s+to\s+work|authoris\w*|authoriz\w*"
)

# Mappable: asks whether the user NEEDS an employer petition.
_ELIG_SPONSORSHIP = (
    r"sponsorship|sponsored|sponsor\s+(?:your|a|an)\s+\w+|visa\s+support"
)

# NOT mappable: asks about citizenship, nationality or residency. The profile
# records a work-authorization status, not a nationality — see the
# `citizenship` branch in `_resolve_one` for why inferring one from the other
# is refused outright.
_ELIG_CITIZENSHIP = (
    r"citizens?|citizenship|nationality|national\s+of"
    r"|permanent\s+resident(?:cy|ce)?|residency|resident\s+status|green\s+card"
)

# NOT mappable: asks whether the user HOLDS a particular document or status.
# "Do you hold a valid US work permit?" is not answerable from
# us_work_auth="citizen" — a citizen holds no work permit — so these are split
# out from the mappable "are you authorized" phrasings rather than sharing an
# alternation with them.
_ELIG_DOCUMENT = (
    r"visa|immigration|work\s+permit|employment\s+permit|permit\s+to\s+work"
    r"|status\s+to\s+work|work\s+status|employment\s+authoris\w*\s+document"
    r"|employment\s+authoriz\w*\s+document|h-?1b|f-?1|stem\s*-?\s*opt"
    r"|opt\s+(?:status|employment|work)|tn\s+status"
)

# ORDERED. `sponsorship` precedes `document` so "Will you require visa
# sponsorship?" stays answerable instead of being blanked for containing the
# word "visa"; `citizenship` precedes both because a label mixing citizenship
# with either is not answerable at all.
_ELIGIBILITY_SUBKINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("citizenship", re.compile(rf"\b(?:{_ELIG_CITIZENSHIP})\b", re.IGNORECASE)),
    ("sponsorship", re.compile(rf"\b(?:{_ELIG_SPONSORSHIP})\b", re.IGNORECASE)),
    ("work_auth", re.compile(rf"\b(?:{_ELIG_WORK_AUTH})\b", re.IGNORECASE)),
    ("work_document", re.compile(rf"\b(?:{_ELIG_DOCUMENT})\b", re.IGNORECASE)),
)

# The kinds that can be answered from a work-auth status at all. The other two
# eligibility kinds are always blank.
_MAPPABLE_ELIGIBILITY = frozenset({"work_auth", "sponsorship"})
_ELIGIBILITY_KINDS = frozenset(k for k, _ in _ELIGIBILITY_SUBKINDS)


def _eligibility_kind(label: str) -> str | None:
    """The work-eligibility sub-kind of `label`, or None if it isn't one."""
    for kind, pattern in _ELIGIBILITY_SUBKINDS:
        if pattern.search(label):
            return kind
    return None


# ---------------------------------------------------------------------------
# Is this label asking for a value, or asking a yes/no question?
# ---------------------------------------------------------------------------
# "Do you currently live in Milwaukee" and "Will you be graduating before June
# 2027?" both mention a field the profile holds, and pasting that field in
# would be wrong even though every character of it came from the profile. The
# check is an ALLOWLIST of value-prompt shapes, not a denylist of question
# openers: `will`/`would` cannot simply be denied, because Greenhouse's real
# LinkedIn field is phrased "Would you like to include your LinkedIn profile,
# personal website or blog?" — which IS asking for the value. So that exact
# shape is allowlisted and every other auxiliary opener falls through to
# "blank", including openers nobody enumerated.
_VALUE_INVITATION_RE = re.compile(
    r"^\s*(?:would|will|do|does|can|could|may|please)\s+(?:you\s+)?"
    r"(?:like\s+to\s+|care\s+to\s+|please\s+|want\s+to\s+|wish\s+to\s+)?"
    r"(?:include|provide|share|add|enter|list|link|attach|give|supply|upload|tell\s+us)\b",
    re.IGNORECASE,
)

# Any label opening with an auxiliary verb is treated as a yes/no question.
# Deliberately generous — over-matching here only produces a blank.
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:are|is|was|were|am|do|does|did|have|has|had|can|could|will|would"
    r"|shall|should|may|might|must)\b",
    re.IGNORECASE,
)


def _is_value_prompt(label: str) -> bool:
    """True when the label asks for a value, so a profile field may be typed.

    Applied to EVERY copy-a-field branch, the name branch included: "Have you
    ever applied under a different last name?" previously reached the name
    branch, which had no gate, and got the surname typed into it.
    """
    if _VALUE_INVITATION_RE.match(label):
        return True
    return not _QUESTION_OPENER_RE.match(label)


def _is_yes_no_question(label: str) -> bool:
    """True when the label reads as a yes/no question.

    The inverse gate, used for the eligibility kinds: a status may be mapped to
    "Yes"/"No" only for a label that actually asks a yes/no question. A bare
    "Work Authorization Status" field wants a status, not a "Yes", so it goes
    blank rather than receiving one.
    """
    return bool(_QUESTION_OPENER_RE.match(label))


# ---------------------------------------------------------------------------
# Everything else: an ordered, word-boundary-anchored label mapping
# ---------------------------------------------------------------------------
# First match wins, so the list runs specific -> generic. Word boundaries, not
# substrings: agents/job_scraper/locations.py learned that the hard way when a
# substring check for "uk" matched inside "Milwaukee". The equivalents here are
# "school" inside "Schoology" and "name" inside "Current Company Name".


def _rx(*alternatives: str) -> re.Pattern[str]:
    """Case-insensitive, word-boundary-anchored alternation of label phrases."""
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b", re.IGNORECASE)


# A whole label that is nothing but "Name" / "Your name" / "Full name*".
# Deliberately anchored: a bare `\bname\b` rule would also swallow "Current
# Company Name" and "Name of your university" and type the applicant's own
# name into them.
_BARE_NAME_RE = re.compile(r"^[\s*]*(?:your\s+|full\s+|legal\s+)?name[\s*:?]*$", re.IGNORECASE)

_LABEL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # A label that asks something ABOUT a name is not asking FOR one. FIRST in
    # this list, because almost every rule below it can read a name request out
    # of such a label: "Name Pronunciation | How do you pronounce your name?"
    # matched `full_name`'s `your\s+name` and was filled with the applicant's
    # actual name, with no note and nothing flagging it — a wrong value in a
    # real application, which is worse than leaving the field blank. It also has
    # to beat `free_text`, or a phrasing like "Please describe how you pronounce
    # your name" would be handed to the drafting model, which would invent a
    # fact about the user.
    #
    # Kept deliberately NARROW: a meta token is not enough on its own, a NAME
    # token has to co-occur with it. The first version matched the meta
    # vocabulary alone, which made "How do you say hello in Spanish?" and "Please
    # provide the correct spelling of your address" both `name_meta` — blank,
    # which is safe, but with a note reading "this asks about how your name is
    # said or spelled", which is simply untrue of those questions. A wrong
    # explanation costs the user trust in every other note the handoff shows, so
    # it is not a cosmetic problem.
    #
    # Equally, a broad `\bname\b` denylist would swallow "Full name", "Preferred
    # Name" and "Legal name" and blank out the fields this resolver exists to
    # fill — hence: name token AND meta token, both word-anchored, in either
    # order.
    #
    # `pronounc\w*` and `pronunciation` are both spelled out because neither
    # contains the other ("pronunc" vs "pronounc"), and `pronounc` specifically
    # — not `pronoun` — because "pronouns" is a protected-characteristic
    # question, a different concern with its own handling, and must not be
    # quietly relabelled as a name question.
    #
    # `\bnames?\b` is anchored so "username" and "surname" do not satisfy the
    # name half: "What is the correct spelling of your username?" is a username
    # field, and the note would be wrong about it.
    # The LEADING word boundary is dropped for the two "pronounce" stems and kept
    # for the others, and that asymmetry is measured, not aesthetic. Checked
    # against /usr/share/dict/words (236k entries) for which words the anchored
    # and unanchored forms disagree on:
    #
    #   pronounc / pronunciation -> mispronounce, unpronounceable, unpronounced,
    #       repronounce, overpronounced, nonpronunciation. EVERY one is about
    #       pronouncing something, so the boundary only ever produced a false
    #       negative — and a false negative here is not benign: "How often do
    #       people mispronounce your name?" fell through to `full_name` and was
    #       auto-filled with the applicant's name. Exactly the defect this class
    #       exists to prevent, reintroduced by a boundary doing its job too well.
    #   spell -> gospellike, dispeller, bespell, indispellable. NOT about
    #       spelling, so here the boundary is protective and stays; `mis` is
    #       allowed explicitly for the one real prefix ("misspelled").
    #   phonetic -> euphonetic, symphonetic (about sound harmony, not names).
    #       Boundary stays; no evidence of a false negative that matters.
    #
    # A word boundary is not automatically the safe choice. It depends entirely on
    # what the neighbouring words in the language actually are.
    ("name_meta", re.compile(
        r"(?=.*\bnames?\b)"
        r".*(?:"
        r"\w*pronunciation\b"
        r"|\w*pronounc\w*\b"
        r"|\bphonetic\w*\b"
        r"|\bhow\s+(?:do|would|should)\s+(?:you|we|i|one)\s+say\b"
        r"|\b(?:mis)?spell(?:ing|ed|s)?\b"
        r")",
        re.IGNORECASE | re.DOTALL)),

    # A name field that is conditional on something this system cannot know:
    # whether the user's legal/previous/preferred name differs from the one
    # they typed. Must beat every generic name rule. All the near-misses of
    # the original rule — "Legal first name (if different than above)",
    # "Legal name (if applicable)", "Other Legal Name" — resolved to real
    # name values until this pattern covered them.
    ("name_alt", re.compile(
        r"\b(?:maiden|previous|former|other|preferred|alias)\s+"
        r"(?:legal\s+)?(?:first\s+|last\s+|middle\s+|full\s+)?names?\b"
        r"|\b(?:legal|full)\s+(?:first\s+|last\s+|middle\s+|full\s+)?names?\b"
        r"[^?]*?\b(?:differ\w*|applicable|if\s+any|other\s+than|alias|above)\b",
        re.IGNORECASE)),

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

    # A label naming a SPECIFIC INSTANCE of an attribute is not asking for the
    # instance the profile happens to store. This is a different principle from
    # `name_meta` above (which is "a label asking ABOUT an attribute is not
    # asking FOR it"), and it needs its own rule: the profile records ONE
    # `school`, with one `degree` and one `grad_date` all belonging to that same
    # school, and "High School Name" asks for a different school entirely.
    #
    # MEASURED before the fix, on the captured Lever form with a real profile:
    #   "High School Name"               -> school    -> "University of Waterloo"
    #   "Year of High School Graduation" -> grad_date -> "2027"   (the options on
    #        that question are 2020-2030 plus "Other", so the profile's UNIVERSITY
    #        graduation year really was selectable and really would have been
    #        selected — the earlier "this one is already fine" reading only held
    #        because a `grad_date` of "May 2027" matched no option verbatim.)
    # Both were `source="profile"`, which is the worst possible placement: the
    # handoff files them under "filled and verified", the band the user is least
    # likely to open, so a false statement about her education goes onto a real
    # application with nothing drawing her eye to it.
    #
    # ORDERED AHEAD OF `grad_date`, not merely ahead of `school`. Ordering it
    # between the two would fix the name and leave the year, and the year is the
    # one that silently selects a plausible-looking wrong value out of a
    # dropdown. The `grad_date`-before-`school` precedent directly below is the
    # same manoeuvre one rule earlier.
    #
    # VOCABULARY: pre-tertiary levels ONLY, and that boundary is provable rather
    # than cautious. A profile that stores `school` next to `degree` and
    # `grad_date` is describing a tertiary institution by construction — nobody
    # holds a degree from a middle school — so a label naming a school BELOW
    # that level cannot be asking for it. Deliberately EXCLUDED for the opposite
    # reason: "undergraduate"/"graduate" name levels the profile plausibly DOES
    # hold ("Undergraduate School" is the right question to answer for an intern
    # applicant), so they keep resolving to `school` and this rule does not
    # touch them. Checked against every label in all three captured fixtures
    # plus the Greenhouse JSON payload: it fires on exactly the two broken Lever
    # labels and on none of the three correct ones — notably not on "Which
    # university are you currently attending … Please select "Other (School Not
    # Listed)"", which contains the word "School" and must keep working.
    # `high[\s-]*school` with a zero-or-more quantifier so "highschool" and
    # "high-school" land too; "Highest level of education" does NOT match,
    # because the "high" there is not followed by "school".
    #
    # WORD BOUNDARIES, measured rather than assumed — the same exercise the
    # `name_meta` rule above documents, and it came out differently at each end:
    #
    #   * LEADING `\b`: LOAD-BEARING — and it was not, one revision ago. When
    #     this rule held only pre-tertiary vocabulary the boundary was measured
    #     to be equivalent: zero of /usr/share/dict/words' 235,976 entries
    #     embedded any of the phrases, and the mutant that removed it was
    #     reported as genuinely unkillable rather than papered over. Adding
    #     `graduate school` changed that in one line: **"undergraduate school"
    #     contains "graduate school"**, so without the boundary "Undergraduate
    #     School" — the case that must keep resolving from the profile — starts
    #     matching here and goes blank. `postgraduate` is the same shape and is
    #     wanted, which is why it is spelled out as its own alternative rather
    #     than left to a loosened boundary.
    #     The general lesson, recorded because it is the branch's third
    #     word-boundary surprise: whether a boundary is load-bearing is a
    #     property of the VOCABULARY, not of the regex, so it has to be
    #     re-measured every time the vocabulary grows. An "equivalent mutant"
    #     is only equivalent for the alternation it was measured against.
    #     (`test_the_leading_boundary_is_what_keeps_undergraduate_working`.)
    #
    #   * TRAILING `\b`: REMOVED from every alternative ending in
    #     "school"/"education", and replaced with `\w*`. A hard boundary there
    #     was not protective, it was WRONG: it rejected "high schooling",
    #     "high schooler", "middle schooling", "primary schooling", "elementary
    #     schooling" and "secondary educational" — six real phrasings, every one
    #     of which is asking about exactly the school level this rule exists to
    #     refuse, and every one of which would therefore have been answered with
    #     the profile's university. This is the `mispronounce` finding again:
    #     a word boundary is not automatically the safe choice.
    #
    #   * The inline `\b` after "junior high" and "sixth form" IS load-bearing
    #     and stays, because those two alternatives do NOT end in "school" —
    #     `\w*` there would match "junior highlights" and "sixth formation".
    #     (`test_the_school_level_tokens_are_anchored_on_word_boundaries`.)
    # (`test_a_school_level_the_profile_does_not_store_is_never_answered`,
    # `test_an_inflected_school_level_phrasing_is_still_caught`,
    # `test_no_lever_question_is_auto_filled_with_the_profile_school_by_accident`.)
    # A GPA is not a date, not a school and not a degree, and the profile does
    # not record one at all. FIRST of the education rules, deliberately: it is
    # the only one of them that can be settled without knowing anything else
    # about the label, and putting it first is what makes "Graduate school GPA"
    # — measured returning the profile's graduation date, i.e. a date into a
    # numeric field — impossible to reach by any other rule. "High School GPA"
    # lands here too rather than in `school_level`, and that is the better of
    # the two: telling her "your profile has no GPA" names the thing the field
    # actually wants, where the school-level note would explain the wrong half
    # of the question. NOTE `grade\s+point\s+average` cannot collide with
    # `school_level`'s `grade\s+school\w*` — different second word.
    # (`test_no_gpa_field_is_ever_answered_from_a_date`.)
    ("gpa", _rx(r"gpa", r"g\.p\.a\.?", r"grade\s+point\s+average")),

    ("school_level", re.compile(
        r"\b(?:"
        r"high[\s-]*school\w*"
        r"|secondary\s+(?:school|education)\w*"
        r"|middle\s+school\w*"
        r"|grade\s+school\w*"
        r"|grammar\s+school\w*"
        r"|elementary\s+school\w*"
        r"|primary\s+school\w*"
        r"|junior\s+high\b(?:\s+school\w*)?"
        r"|senior\s+high\b(?:\s+school\w*)?"
        r"|prep(?:aratory)?\s+school\w*"
        r"|sixth\s+form\b(?:\s+college)?"
        # ABOVE tertiary as well as below it, and for the same reason. See the
        # `graduate`/`graduation` note on `grad_date` below: "graduate" here is
        # the ADJECTIVE naming a level of study, and the profile's single
        # `school` cannot vouch for a second, postgraduate institution. This is
        # the asymmetry with `undergraduate`, which is deliberately NOT in this
        # rule — for the intern and new-grad applications this agent is pointed
        # at, "Undergraduate School" is the one school the profile is
        # describing, while "Graduate School" is an ADDITIONAL one it says
        # nothing about. A form asking both (common) would otherwise get the
        # same answer twice, which is wrong on at least one of them.
        # The residual risk is stated in the report: a user whose profile school
        # IS a graduate school still gets it offered for "Undergraduate School".
        r"|(?:post[\s-]*)?graduate\s+(?:school\w*|programs?|programmes?|studies"
        r"|study|coursework|degrees?|education\w*|institutions?|level|students?"
        r"|admissions?|transcripts?)"
        r"|grad\s+school\w*"
        r"|post[\s-]*grad(?:uate)?\b"
        r")",
        re.IGNORECASE)),

    # Before `school`/`degree`: "Expected graduation date from your university"
    # names a university but asks for a date.
    #
    # THE SAME STEM IS TWO DIFFERENT WORDS, and only one of them is a date:
    #
    #   * "graduatION" is the EVENT noun. An event happens at a time, so every
    #     use of it is a date question: "Expected graduation date", "Year of
    #     Graduation", "Anticipated graduation", "Graduation year".
    #   * "graduatE" is a LEVEL OF STUDY when it modifies a noun ("Graduate
    #     School", "Graduate program", "Graduate degree", "graduate student"),
    #     and a VERB only in a clause ("When do you expect to graduate?").
    #
    # `graduat\w*` could not tell them apart, and MEASURED with a real profile
    # it answered ELEVEN adjectival phrasings with the graduation date —
    # "Graduate School" and "Graduate school GPA" among them, i.e. a date typed
    # into a school-name field and into a numeric field. This is the same family
    # as `school_level` above and as `name_meta`/`name_alt` before it, and the
    # fourth time on this branch that a token which looked like one concept was
    # two.
    #
    # The verbal uses of "graduate" are an ALLOWLIST of the clause openers that
    # can precede the verb, not a denylist of the nouns that can follow it. A
    # denylist of following nouns cannot be finished — every noun in the
    # academic vocabulary is a candidate — whereas the set of words that can
    # stand immediately before an English verb here is small and closed. Same
    # reasoning as `_VALUE_INVITATION_RE` above.
    #
    # Anything with "graduate" that matches NO verbal opener and modifies NO
    # noun ("Are you a recent graduate?") falls through to `other` and goes
    # blank with a reason, which is the correct outcome: the profile records
    # when she graduates, not whether she counts as a graduate.
    # (`test_the_date_phrasings_that_must_not_regress` — FIFTEEN phrasings, not
    # the five an earlier version of this citation named, and the test it named
    # did not exist,
    # `test_graduate_the_adjective_is_never_a_date`,
    # `test_no_profile_value_lands_in_a_field_of_a_different_kind`.)
    ("grad_date", _rx(
        r"graduation\w*",
        r"graduating", r"graduated",
        r"(?:to|will|would|shall|should|can|may|might|you|expect|expects"
        r"|expected|anticipate|anticipates|anticipated|do|does|did)\s+graduate",
        r"grad\s+date", r"grad\s+year",
        r"expected\s+completion",
    )),
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

# Kinds a pure resolver can never safely settle AND that gate a real
# submission, so the handoff must surface every one of them for explicit human
# action before the user presses Submit. The eligibility kinds are here even
# when they DID resolve from the profile: those are the answers that decide
# whether an application is considered at all, so they get confirmed, not
# assumed.
BLOCKING_KINDS: frozenset[str] = frozenset(
    _ELIGIBILITY_KINDS | {"consent", "file_upload"}
)

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
    """The resolver's category for one question.

    Order of decision, and why each step is where it is:

      1. `kind == "file"` — nothing in a text profile is a file.
      2. Work eligibility, in any spelling/casing and for any widget, so a
         textarea version can never reach the `free_text` rule and be handed
         to a model.
      3. The ordered label rules.
      4. The anchored bare-"Name" rule.
      5. `kind == "textarea"` => `free_text` — LAST, so it only catches labels
         no rule above recognised.
    """
    if question.kind == "file":
        return "file_upload"
    label = question.label or ""
    eligibility = _eligibility_kind(label)
    if eligibility:
        return eligibility
    for kind, pattern in _LABEL_RULES:
        if pattern.search(label):
            return kind
    if _BARE_NAME_RE.match(label):
        return "full_name"
    if question.kind == "textarea":
        return "free_text"
    return "other"


# ---------------------------------------------------------------------------
# Which country is an eligibility question about?
# ---------------------------------------------------------------------------
# The answer decides WHICH profile field may be read, so getting it wrong means
# answering a Canadian question from a US field. Rules:
#
#   * "United States", "USA", "U.S." and "America" are unambiguous anywhere in
#     the label.
#   * The bare token "us" is the English pronoun far more often than the
#     country, so it counts ONLY directly after a locative preposition ("in
#     the US", "for US employment"). This is a positive allowlist rather than
#     the earlier case-sensitive `\bUS\b` trick, which an ALL-CAPS label
#     ("...TO WORK WITH US?") defeated outright.
#   * "CA" is never matched: in a job posting it means California at least as
#     often as Canada.
#   * A label naming BOTH countries, or "North America", resolves to None:
#     one profile field cannot answer it.
_US_UNAMBIGUOUS_RE = re.compile(
    r"\b(?:united\s+states(?:\s+of\s+america)?|u\.s\.a?\.?|usa|america|american)\b",
    re.IGNORECASE)
_US_PRONOUN_SAFE_RE = re.compile(
    r"\b(?:in|for|within|to|from|inside|outside|throughout)\s+(?:the\s+)?us\b",
    re.IGNORECASE)
# "US" used adjectivally before a noun it can only be modifying as the country
# ("US work authorization", "US payroll"). Also an allowlist: the pronoun is
# never followed by any of these, whereas "…to work with us?" and "…tell us
# about…" both leave "us" at the end of a clause.
_US_ADJECTIVE_RE = re.compile(
    r"\bus\s+(?:work|employment|citizen\w*|resident\w*|visa|immigration|payroll"
    r"|entity|office|based|jobs?|roles?|positions?|law|labor|labour)\b",
    re.IGNORECASE)
_CA_RE = re.compile(r"\b(?:canada|canadian)\b", re.IGNORECASE)
_MULTI_COUNTRY_RE = re.compile(r"\bnorth\s+america\w*\b", re.IGNORECASE)


def _country_signals(label: str) -> tuple[str | None, bool]:
    """`(country, names_more_than_one)`.

    `country` is "us", "ca", or None. The second element distinguishes the two
    reasons `country` can be None, which the posting-country default below
    depends on: a label naming NO country can be answered for the country the
    job is in, while one naming BOTH ("North America", or the US and Canada
    together) can never be — one profile field does not answer it, and the
    posting's country would silently pick a side.
    """
    if _MULTI_COUNTRY_RE.search(label):
        return None, True
    is_us = bool(
        _US_UNAMBIGUOUS_RE.search(label)
        or _US_PRONOUN_SAFE_RE.search(label)
        or _US_ADJECTIVE_RE.search(label)
    )
    is_ca = bool(_CA_RE.search(label))
    if is_us and not is_ca:
        return "us", False
    if is_ca and not is_us:
        return "ca", False
    return None, (is_us and is_ca)


def _country(label: str) -> str | None:
    """"us", "ca", or None when the label names neither or BOTH."""
    return _country_signals(label)[0]


#: Posting countries this module can turn into a profile field. `jobs.country`
#: also holds "UNKNOWN" and "OTHER", and neither of those names a field.
DEFAULT_COUNTRIES: frozenset[str] = frozenset({"us", "ca"})


def _default_country(value: str) -> str:
    """Normalise a caller-supplied posting country, or "" if it names no field.

    Deliberately strict: anything that is not recognisably the US or Canada
    (including `jobs.country`'s own "UNKNOWN" and "OTHER") yields "", which
    leaves every unnamed-country eligibility question exactly where it was —
    blank, with the note saying the question does not name a country.
    """
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in DEFAULT_COUNTRIES else ""


# ---------------------------------------------------------------------------
# Work-authorization enum -> form answer
# ---------------------------------------------------------------------------
# profile_store.WORK_AUTH values are INTERNAL enum strings, never text to type
# into a form. Each maps to an intended answer here; the option gate and the
# country/yes-no gates then decide whether that answer may actually be used.
#
# citizen / permanent_resident are unambiguously authorized and unambiguously
# need no sponsorship. needs_sponsorship is the unambiguous opposite.
_WORK_AUTH_YES_NO = {
    "citizen": "Yes",
    "permanent_resident": "Yes",
    # A held co-op/study permit authorizes work in the country that issued it.
    "coop_permit": "Yes",
    "needs_sponsorship": "No",
}
_SPONSORSHIP_YES_NO = {
    "citizen": "No",
    "permanent_resident": "No",
    # The permit is already held, so no sponsorship is needed to use it. Unlike
    # f1_opt this is NOT conditional: a co-op permit's work authorization does
    # not depend on an employer petitioning later.
    "coop_permit": "No",
    "needs_sponsorship": "Yes",
}

# f1_opt and tn_eligible are CONDITIONAL: whether either counts as "authorized"
# or as "needing sponsorship" depends on visa specifics this system does not
# model (OPT/STEM-OPT expiry and whether the employer will later petition; for
# TN, whether the job title is on the USMCA schedule, and that status is
# granted at entry rather than held in advance). Asserting "Yes" could put a
# false claim on a real application; asserting "No" could disqualify a
# candidate who is in fact employable. So neither is asserted: on a free-text
# field the resolver states the status the user typed and flags it for review,
# and on a select the status matches no option so the answer goes blank.
#
# These strings encode ONLY what the enum encodes. An earlier version rendered
# tn_eligible as "Canadian citizen, eligible for TN status under USMCA", which
# asserted a nationality the profile never stores AND was wrong for a Mexican
# citizen — TN covers both — i.e. exactly the citizenship inference the
# `citizenship` branch refuses to make.
_CONDITIONAL_STATUS_TEXT = {
    "f1_opt": "F-1 student; OPT work authorization applies",
    "tn_eligible": "Eligible for TN status under USMCA",
}

# `needs_sponsorship` is an int 0/1 column that DEFAULTS to 0, so truthiness is
# an ALLOWLIST of the values a deliberate tick can produce. A denylist
# ("anything except '', '0', 'false'") read a whitespace-only or unexpected
# value as "yes, I need sponsorship" and produced an answer with nothing typed
# anywhere in the profile.
_TRUE_VALUES = frozenset({"1", "true", "yes", "y"})


def _is_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 1
    return str(value).strip().casefold() in _TRUE_VALUES


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
            prefix = "work authorization — " if kind in _ELIGIBILITY_KINDS else ""
            return _blank(
                question,
                kind,
                f"{prefix}your profile implies “{value}”, but none of this "
                f"question's options matches it exactly — choose an option yourself.",
            )
        value = match
    return Answer(question=question, value=value, source="profile", note=note, kind=kind)


#: Appended — as its own complete sentence, at the END — to every note on an
#: eligibility answer whose country came from the POSTING rather than from the
#: question's own wording. The user is told which way the agent guessed, because
#: the guess is what picks the profile field.
#:
#: A SUFFIX rather than a prefix, and it names the field exactly once. As a
#: prefix it produced "work authorization — this question does not name a
#: country, …your profile conflicts…" (a lower-case clause spliced into the
#: middle of another sentence), and the field ended up named twice once
#: `fill.py` appended this note to its own refusal text.
_FROM_POSTING_NOTE = (
    "This question names no country, so the posting's own country ({country}) "
    "is what selected {field}."
)


def _with_country_note(note: str, from_posting: str) -> str:
    """Join a note and the posting-country disclosure, skipping empty parts."""
    return " ".join(part for part in (note.strip(), from_posting.strip()) if part)


def _resolve_eligibility(
    question: Question, profile: dict, kind: str, default_country: str = ""
) -> Answer:
    """Resolve a work-authorization or sponsorship question.

    Never reached by the drafting node and never allowed to guess: every path
    that isn't a direct read of a typed status ends in a blank with a note the
    handoff lists as blocking.

    `default_country` is the posting's country ("us"/"ca"), used ONLY when the
    label names no country at all — "Are you legally authorized to work in the
    country for which you are applying?" is a real Lever question, and blanking
    it for not naming a country was the resolver refusing to read a fact the
    posting row already knows. It changes nothing else: `work_auth` and
    `sponsorship` stay in `BLOCKING_KINDS`, so every answer here is still
    surfaced for the human to confirm, and a label naming BOTH countries still
    goes blank (`_country_signals`).
    """
    label = question.label or ""
    named, names_several = _country_signals(label)
    country = named or ("" if names_several else _default_country(default_country))
    if not country:
        return _blank(
            question, kind,
            "work authorization — this question does not name a single country "
            "(US or Canada), so I cannot tell which profile field applies; "
            "answer it yourself.",
        )
    auth_field = f"{country}_work_auth"

    # Said on every outcome below, not only the ones that fill something: a
    # blank that names `ca_work_auth` on a question that never said "Canada"
    # is just as much an inference as a filled one.
    from_posting = (
        _FROM_POSTING_NOTE.format(country=country.upper(), field=auth_field)
        if named is None else ""
    )

    # A status maps to "Yes"/"No" only for a label that actually asks a yes/no
    # question. On a select/checkbox the option gate plays that role instead,
    # so the shape of the label doesn't have to. No country disclosure here: this
    # path never looked at a profile field, so there is nothing to disclose.
    if question.kind in ("text", "textarea") and not _is_yes_no_question(label):
        return _blank(
            question, kind,
            "work authorization — this does not read as a yes/no question, so your "
            "profile's status is not an answer to it; answer it yourself.",
        )

    status = str(profile.get(auth_field) or "").strip()

    # `needs_sponsorship` is ONE country-agnostic checkbox, so it can never
    # answer a question that names a country — answering "will you require
    # sponsorship to work in Canada?" from a global flag with ca_work_auth
    # unset is exactly the kind of cross-wiring this module exists to prevent.
    # Its only job here is to catch a profile that contradicts itself.
    if _is_true(profile.get("needs_sponsorship")) and status in ("citizen", "permanent_resident"):
        return _blank(
            question, kind,
            _with_country_note(
                f"work authorization — your profile conflicts: it says you need "
                f"sponsorship but {auth_field} is “{status}”. Fix the profile or "
                f"answer this question yourself.",
                from_posting,
            ),
        )

    if not status:
        return _blank(
            question, kind,
            _with_country_note(
                f"work authorization is not set in your profile ({auth_field}) — "
                f"this answer must be yours; it is never guessed.",
                from_posting,
            ),
        )

    if status in _CONDITIONAL_STATUS_TEXT:
        return _emit(
            question, kind, _CONDITIONAL_STATUS_TEXT[status],
            note=_with_country_note(
                f"conditional status ({status}): a plain yes/no depends on visa "
                f"specifics this system does not model, so your profile's status "
                f"is stated instead — review and edit before submitting.",
                from_posting,
            ),
        )

    table = _SPONSORSHIP_YES_NO if kind == "sponsorship" else _WORK_AUTH_YES_NO
    answer = table.get(status)
    if answer is None:
        # An enum value added to profile_store.WORK_AUTH but not mapped here.
        # Blank rather than a stab at what it might mean.
        return _blank(
            question, kind,
            _with_country_note(
                f"work authorization — your profile's {auth_field} value "
                f"“{status}” has no defined form answer; answer this yourself.",
                from_posting,
            ),
        )
    return _emit(question, kind, answer, note=from_posting)


def _missing_note(field_name: str) -> str:
    return (
        f"“{field_name}” is empty in your profile — add it there, or type "
        f"this field in yourself."
    )


def _not_a_value_prompt_note(field_name: str) -> str:
    return (
        f"this reads as a yes/no question, not a prompt for a value, so your "
        f"profile's “{field_name}” is not the answer to it — answer it yourself."
    )


# Generational and academic suffixes that belong to the SURNAME, not to a
# separate name part: splitting "Testy McTestface Jr." on the last space
# yielded the surname "Jr.".
_NAME_SUFFIXES = frozenset({
    "jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "esq", "mba",
})


def _name_parts(full_name: str) -> tuple[str, str]:
    """Split on the LAST space so multi-word given names survive: "Mary Jane
    Watson" -> ("Mary Jane", "Watson"). A trailing generational suffix stays
    attached to the surname. A single token yields (token, "") — no surname is
    ever fabricated.
    """
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    tail = 1
    if len(parts) >= 3 and parts[-1].strip(".,").casefold() in _NAME_SUFFIXES:
        tail = 2
    return " ".join(parts[:-tail]), " ".join(parts[-tail:])


def _resolve_one(question: Question, profile: dict, default_country: str = "") -> Answer:
    kind = classify(question)
    label = question.label or ""

    if kind in _MAPPABLE_ELIGIBILITY:
        return _resolve_eligibility(question, profile, kind, default_country)

    if kind == "citizenship":
        # Always blank, even when a status IS typed. The profile records work
        # authorization, not citizenship, nationality or residency, and
        # inferring one from the other ("us_work_auth is citizen, so tick US
        # citizen") is exactly the kind of chained guess that puts a false
        # legal claim on a real application.
        return _blank(
            question, kind,
            "work authorization — citizenship, nationality and residency are not "
            "stored in your profile (it records a work-authorization status only); "
            "this answer must be yours.",
        )

    if kind == "work_document":
        # Holding a particular visa, permit or document does not follow from a
        # work-auth status: a citizen holds no work permit, and "authorized"
        # says nothing about which document authorizes it.
        return _blank(
            question, kind,
            "work authorization — which visa, permit or document you hold is not "
            "stored in your profile (it records a work-authorization status only); "
            "this answer must be yours.",
        )

    if kind == "file_upload":
        # This note used to read "attach the file yourself …; nothing is uploaded
        # automatically", which stopped being true on 2026-08-01: the fill
        # executor DOES attach the résumé, as its last action, to the one file
        # input whose label identifies it as a résumé/CV (see
        # `agents/job_applier/nodes/fill.py`, decision 8). What has NOT changed is
        # this module: `file_upload` is still blocking, still blank, still pure. A
        # resolver cannot answer a file question with text, and the attach is an
        # explicit executor path driven by the résumé the user picked — never an
        # answer emitted here. Pinned by
        # `test_the_resolvers_file_upload_note_does_not_contradict_the_attach`.
        return _blank(
            question, kind,
            "file upload — the agent attaches your résumé itself, last, once every "
            "other field is filled. Any other document (cover letter, transcript, "
            "portfolio) is yours to attach in the open browser window.",
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
            "this asks for a name only if it differs from the one above, and your "
            "profile stores one name — fill it in only if yours differs.",
        )

    if kind == "name_meta":
        return _blank(
            question, kind,
            "this asks about how your name is said or spelled, not for the name "
            "itself — your profile has no answer to it, so type it in yourself.",
        )

    if kind == "relocation":
        return _blank(
            question, kind,
            "a relocation preference is not stored in your profile — answer it "
            "yourself.",
        )

    if kind == "gpa":
        return _blank(
            question, kind,
            "your profile does not record a GPA — nothing it holds is a grade, "
            "so type this one in yourself.",
        )

    if kind == "school_level":
        # Says WHAT the profile holds and WHY it was not used, rather than only
        # that nothing was filled — the same shape as the option-gate note in
        # `_emit`. A bare "not derivable" here would read as a gap in the
        # resolver, when the truth is that the profile has a perfectly good
        # value that is the answer to a different question.
        stored = str(profile.get("school") or "").strip()
        held = f" — it records “{stored}”" if stored else ""
        return _blank(
            question, kind,
            f"this asks about a level of schooling your profile does not "
            f"store{held}, and one school, degree and graduation date all "
            f"describing that same school are everything it has. Answering "
            f"from them would put the wrong school on this application, so "
            f"fill this one in yourself.",
        )

    if kind in ("first_name", "last_name"):
        if not _is_value_prompt(label):
            return _blank(question, kind, _not_a_value_prompt_note("full_name"))
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
        if not _is_value_prompt(label):
            return _blank(question, kind, _not_a_value_prompt_note(field_name))
        value = str(profile.get(field_name) or "").strip()
        if not value:
            return _blank(question, kind, _missing_note(field_name))
        return _emit(question, kind, value)

    return _blank(
        question, kind,
        "not derivable from a typed profile field — fill this one in yourself.",
    )


def resolve(
    questions: list[Question], profile: dict, *, default_country: str = ""
) -> list[Answer]:
    """One `Answer` per question, in the questions' own order.

    Reads `profile` only via `.get`, so a partial or empty dict is normal
    input, not an error — the real profile currently has most fields unset, and
    "everything blank, each with a reason" is the expected output for it.

    `default_country` ("us" / "ca", case-insensitive; anything else ignored) is
    the country the POSTING is in, which the caller reads off the job row. It is
    consulted for one thing only: an eligibility question whose label names no
    country ("...authorized to work in the country for which you are applying?")
    picks the matching `{country}_work_auth` field instead of going blank for
    not knowing which one applies, and says so in its note. It never widens what
    may be answered — the eligibility kinds are all in `BLOCKING_KINDS`, so the
    handoff still makes the human confirm every one of them.
    """
    profile = profile or {}
    return [_resolve_one(q, profile, default_country) for q in questions]


def blocking(answers: list[Answer]) -> list[Answer]:
    """The answers the handoff must make the user confirm before submitting:
    every `BLOCKING_KINDS` answer, plus any required field left blank.
    """
    return [
        a for a in answers
        if a.kind in BLOCKING_KINDS or (a.question.required and not a.value)
    ]
