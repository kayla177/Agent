"""Draft node — the free-text answers, and the merge that decides what is typed.

Two jobs, and the second one is the load-bearing one.

**1. Draft.** `drafting.draft` is safe to hand the whole question list: anything
it does not own comes back blank with a reason, so this node does not have to
pre-filter correctly for the safety properties to hold. A model that is down,
slow or returns junk yields blanks, never a guess.

**2. Merge.** `merge_answers` combines the resolver's list with drafting's into
the single list the executor types. The policy is not obvious and it is not
cosmetic, so it is stated here once and pinned by tests rather than reinvented
per caller:

    Drafting wins for every question drafting owns — refusals included.

The resolver blanks every `free_text` question with the note "free-text answer
left for the AI drafting step, which marks its output as AI-drafted for you to
review". Drafting then DECLINES some of those: on the captured Lever form it
declines four, including both video prompts, where the box wants a YouTube URL
rather than prose and drafting's own note ("this box reads as a request for a
video or audio recording") is the true and useful one. If the merge kept the
resolver's answer whenever drafting came back blank, the handoff would promise
the user a draft that is never coming, on four fields of one real form.

THE ONE RULE for all of Phase B: no code path may ever click a submit button.
This module writes no browser code at all.
"""

from __future__ import annotations

from agents.job_applier import drafting

#: The resolver kind whose answer drafting owns. Kept as a named constant
#: because the merge below is a one-liner whose correctness lives entirely in
#: this string matching what `resolver.classify` emits for an open question.
DRAFTED_KIND = "free_text"


def merge_answers(resolved: list, drafted: list) -> list:
    """The answer list the executor types: drafting wins for what it owns.

    "Owns" is `resolved.source == "blank"` AND `drafted.kind == DRAFTED_KIND` —
    i.e. the resolver deliberately stepped aside for the drafter on this
    question. A question the resolver ANSWERED from the profile is never
    overwritten by a draft, and a question drafting does not classify as
    free-text keeps the resolver's note.

    Both lists are one-per-question in the questions' own order (`resolve` and
    `draft` both guarantee that), so the zip is positional and total.
    """
    return [
        d if (r.source == "blank" and d.kind == DRAFTED_KIND) else r
        for r, d in zip(resolved, drafted)
    ]


def draft_node(state: dict) -> dict:
    questions = state.get("questions") or []
    drafted = drafting.draft(
        questions,
        job=state.get("job") or {},
        profile=state.get("profile") or {},
        experience=state.get("experience") or "",
    )
    return {
        "drafted": drafted,
        "answers": merge_answers(state.get("resolved") or [], drafted),
    }
