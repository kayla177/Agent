"""Rank node — undergrad-eligibility screen + baseline/refined fit score.

Uses the platform's local model (via ``shell/model_router.llm``) to, for every
posting, judge:
  - ``eligible``: can an UNDERGRAD (bachelor's, ~0-1 yrs exp) realistically apply?
    False only when the role clearly requires a Master's/PhD or senior experience
    (read from the title + a HEAD+TAIL excerpt of the JD — see ``_desc_slice``;
    the qualifications live at the bottom, so a head-only slice showed the model
    nothing but company boilerplate). Roles judged ineligible are dropped.
  - ``fit_score`` (0–100) + ``fit_reason`` against the candidate profile.

Design choices that keep this safe and cheap:
  - Batched: several postings per model call so one prompt handles many roles.
  - Runs even with no profile — eligibility is independent of the profile.
  - A deterministic keyword baseline (scoring.py) is applied to EVERY posting
    FIRST, so a score always exists; the LLM then overrides it only when it
    returns a usable integer. Measured 2026-07-25: llama3.1:8b returns a null
    score for ~60% of postings even with a profile set, so treating the model
    as the sole source left most rows unscored — and null sinks to the bottom
    of sort-by-fit, hiding good roles. The LLM still has sole authority over
    the ``eligible`` drop decision.
  - Optional ``config.JOB_MIN_FIT`` drops roles scoring below it (0 = keep
    all). Since every posting now always has a score, there is no "unscored
    roles are always kept" carve-out anymore.
"""

from __future__ import annotations

import json
import re

import config
import profile_store
from agents.job_scraper.scoring import extract_keywords, is_baseline_reason, score_baseline
from agents.job_scraper.state import JobScraperState
from shell.model_router import llm

_BATCH = 3
# Per-posting JD budget for the prompt, split HEAD + TAIL. See `_desc_slice`.
_DESC_HEAD = 400
_DESC_TAIL = 1600
_DESC_ELIDE = "\n[...]\n"
_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)

_SYSTEM = (
    "You screen roles for an UNDERGRADUATE student (bachelor's, ~0-1 years "
    "experience) seeking intern / co-op / new-grad software, ML, and data roles. "
    "For each role set \"eligible\": true, UNLESS it clearly requires a Master's/PhD "
    "or senior (2+ years) experience — then false. If a candidate profile is given, "
    "also give a 0-100 fit \"score\" (else null). Reply with ONLY a JSON array of "
    "{\"i\": <index>, \"eligible\": <true|false>, \"score\": <0-100 or null>, "
    "\"reason\": \"<=12 words\"}. No prose, no code fences."
)


def _desc_slice(
    description: str | None, *, head: int = _DESC_HEAD, tail: int = _DESC_TAIL
) -> str:
    """A bounded JD excerpt for the prompt: the OPENING plus the ENDING.

    DO NOT "simplify" this back to `description[:n]`. Head-only is the bug this
    replaced, and it is the reason both jobs below scored the same.

    A job description is laid out company-first: mission blurb, team pitch, "what
    you'll do" — and only then the part that decides whether an undergrad can
    apply at all ("Qualifications", "Requirements", "Minimum/Preferred", "you
    have...", the actual tech stack). Measured over 50 live JDs pulled from
    greenhouse / lever / ashby on 2026-07-25 (p50 length 5,045 chars, max
    13,982): the last mention of a concrete technology sits a median of 1,305
    chars from the END of the text, while a head-only 500-char slice — the old
    `_DESC_SLICE` — captured a qualifications heading in 10% of them and 1.8% of
    the distinct tech terms. head=400 + tail=1600 captures 88% and 40%
    respectively at the same order of cost. The head is still needed because the
    ending alone often lands in EEO/legal boilerplate that never names the role.

    The budget is deliberately small: `_BATCH` postings share ONE prompt against
    `ollama/llama3.1:8b`, and Ollama commonly defaults `num_ctx` to 2048 TOKENS.
    A full 3-posting prompt built from the three longest JDs in that sample
    measures 1,506 tokens including the system message (litellm token_counter),
    leaving ~540 for the reply. Raising these numbers without re-measuring
    reintroduces the original bug the expensive way: the runtime silently drops
    the overflow and the model is left reading the intro paragraph again.

    A description that fits whole is returned whole — no elision marker, no
    duplicated middle.
    """
    text = description or ""
    if len(text) <= head + tail:
        return text
    return text[:head] + _DESC_ELIDE + text[-tail:]


def _prompt(profile: str, batch: list[dict]) -> str:
    head = (
        f"CANDIDATE PROFILE:\n{profile}\n"
        if profile
        else "No candidate profile provided — set every score to null.\n"
    )
    lines = [head, "ROLES:"]
    for i, p in enumerate(batch):
        desc = _desc_slice(p.get("description")).replace("\n", " ")
        lines.append(
            f"[{i}] {p.get('title', '?')} @ {p.get('company', '?')} "
            f"({p.get('location', '—')})\n{desc}"
        )
    return "\n".join(lines)


def _parse(reply: str, size: int) -> dict[int, dict]:
    """Extract {index: {eligible, score, reason}} from the reply; {} on failure."""
    match = _JSON_RE.search(reply or "")
    if not match:
        return {}
    try:
        items = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    out: dict[int, dict] = {}
    for it in items if isinstance(items, list) else []:
        try:
            idx = int(it["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < size):
            continue
        # Default eligible=True unless the model explicitly says false.
        eligible = it.get("eligible", True) is not False
        raw = it.get("score", None)
        # `bool` subclasses `int`, so `int(True) == 1` — without this guard a
        # reply of {"score": true} is silently accepted as a fit score of 1
        # AND overwrites the baseline reason, which makes is_baseline_reason()
        # False. The row is then never re-selected by backfill and never
        # re-baselined, so ONE malformed reply pins a good job at the bottom of
        # sort-by-fit permanently. A bool is not a score: treat it as absent
        # and keep the deterministic baseline.
        if isinstance(raw, bool):
            raw = None
        try:
            score = None if raw is None else max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            score = None
        out[idx] = {"eligible": eligible, "score": score, "reason": str(it.get("reason", "")).strip()}
    return out


def _score_batch(profile: str, keywords: list[str], batch: list[dict]) -> None:
    """Attach eligible / fit_score / fit_reason to a batch in place.

    The deterministic baseline is applied FIRST so every posting always has a
    score, then the LLM overrides it only when it returns a usable integer.
    Measured 2026-07-25: llama3.1:8b returns a null score for ~60% of postings
    even with a profile set, so treating the model as the sole source would
    leave most rows unscored — and null sinks to the bottom of sort-by-fit,
    hiding good roles.
    """
    for p in batch:
        base_score, base_reason = score_baseline(keywords, p)
        p["fit_score"] = base_score
        p["fit_reason"] = base_reason
        p["eligible"] = True

    try:
        reply = llm("local", _prompt(profile, batch), system=_SYSTEM, temperature=0.2)
    except Exception as exc:  # model unavailable / transport error -> keep baselines
        # Print the detail (can be a very long litellm/httpx error) rather than
        # storing it in fit_reason, which is rendered directly in the jobs board
        # UI. The fixed " (unrefined)" suffix must keep is_baseline_reason() True
        # so the backfill node still retries these rows.
        print(f"rank_node: LLM unavailable, keeping baseline scores: {exc}")
        for p in batch:
            p["fit_reason"] = f"{p['fit_reason']} (unrefined)"
        return

    parsed = _parse(reply, len(batch))
    for i, p in enumerate(batch):
        hit = parsed.get(i)
        if not hit:
            continue
        p["eligible"] = hit["eligible"]
        if hit["score"] is not None:
            p["fit_score"] = hit["score"]
            # Always overwrite the reason when the score was refined. Leaving the
            # baseline reason in place would make is_baseline_reason() True, so the
            # row would be re-selected (and re-clobbered) on every future run.
            p["fit_reason"] = hit["reason"] or "refined (no reason given)"


def rank_node(state: JobScraperState) -> JobScraperState:
    new = [dict(p) for p in state.get("new", [])]
    if not new:
        return {"new": new}

    # Profile: explicit pref wins, else the applicant profile's summary.
    profile = (config.JOB_PROFILE or "").strip() or profile_store.fit_profile_text()
    keywords = extract_keywords(profile)

    refinable = [p for p in new if not p.get("_skip_llm")]
    baseline_only = [p for p in new if p.get("_skip_llm")]

    for p in baseline_only:
        # Never clobber an existing LLM-refined score. A row can land here with
        # `_skip_llm` set for a reason that has nothing to do with its score —
        # e.g. backfill selected it only because `country` was blank, and it
        # already carries a real, LLM-refined fit_score/fit_reason. Recomputing
        # unconditionally would overwrite that (measured: 92/"strong python +
        # react match" -> 35/"matched: none"), and since a baseline reason
        # reads as "not yet refined", the row would then be re-selected by
        # backfill forever. Only (re)compute when there is no score yet, or
        # the current score already IS the baseline (never refined).
        if p.get("fit_score") is None or is_baseline_reason(p.get("fit_reason", "")):
            p["fit_score"], p["fit_reason"] = score_baseline(keywords, p)
        p.setdefault("eligible", True)

    for start in range(0, len(refinable), _BATCH):
        _score_batch(profile, keywords, refinable[start : start + _BATCH])

    # Drop roles the model judged not undergrad-eligible (grad-only / senior).
    # `_rescored` rows are backlog rows already in the store, not newly
    # discovered postings — this drop exists to decide which NEW postings are
    # worth keeping, so it must not discard a backlog row's freshly computed
    # country/fit_score before notify can persist it. `_rescored` already
    # suppresses announcement, so letting one through here only updates a row
    # that exists in the DB either way.
    new = [p for p in new if p.get("_rescored") or p.get("eligible", True)]

    # Optional fit threshold. Every posting now HAS a score, so there is no
    # "unscored" carve-out to make any more. Same `_rescored` exemption as above.
    if config.JOB_MIN_FIT > 0:
        new = [p for p in new if p.get("_rescored") or p["fit_score"] >= config.JOB_MIN_FIT]

    new.sort(key=lambda p: p["fit_score"], reverse=True)
    return {"new": new}
