"""Fitting a long document into a bounded model prompt.

One helper, `head_tail`, because the same mistake was made independently in two
different agents: both the job scraper's rank node and the résumé generator's
keywords node bounded a job description with `description[:n]`, and both were
therefore reading company boilerplate instead of the requirements.

This lives in `shell/` rather than in either agent because it is cross-cutting
prompt infrastructure, the same as `model_router`: it encodes "how do I spend a
token budget on a document", not anything about jobs or résumés. Both agents
already depend on `shell/`, so sharing it here couples nothing new. The BUDGETS
stay with each caller — they are measured per prompt and per model — while the
mechanism is defined once.
"""

from __future__ import annotations

ELIDE = "\n[...]\n"


def head_tail(text: str | None, *, head: int, tail: int, elide: str = ELIDE) -> str:
    """Return the OPENING plus the ENDING of `text`, bounded by `head` + `tail`.

    Do NOT replace a call to this with `text[:head + tail]`. Head-only slicing is
    the bug this exists to prevent, and it is a bug specifically because of how
    the documents in this repo are laid out: a job description opens with company
    marketing ("At <Company>, we are passionate about...") and puts the part that
    actually carries signal — Qualifications, Requirements, Minimum/Preferred,
    the concrete tech stack — at the BOTTOM. Measured over 50 live job
    descriptions from greenhouse / lever / ashby on 2026-07-25 (p50 5,045 chars,
    max 13,982), the last mention of a concrete technology sat a median of 1,305
    characters from the END of the document.

    The head is kept, rather than taking the tail alone, because the last stretch
    of a posting is frequently EEO / background-check / "how we work" boilerplate
    that never names the role — so a tail-only excerpt can lose what the document
    is even about.

    Text that already fits within `head + tail` is returned unchanged: no elide
    marker, and no duplicated overlap in the middle.

    `head` and `tail` must both be positive. A zero `tail` is rejected rather
    than quietly meaning head-only, because `text[-0:]` is the WHOLE string in
    Python — the one arithmetic slip here returns more text than either bound
    asks for, which is exactly the silent overflow the budgets exist to prevent.
    """
    if head <= 0 or tail <= 0:
        raise ValueError(f"head and tail must both be positive, got {head=} {tail=}")
    text = text or ""
    if len(text) <= head + tail:
        return text
    return text[:head] + elide + text[-tail:]
