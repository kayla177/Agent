"""Prompt budgets: the shared head+tail slicer, and the pinned local context window.

Two follow-ups to the description-signal fix, both grounded in a measurement that
contradicted an assumption:

* The context window was believed to be 2048 tokens. It is not — `llama3.1:8b`
  reports `llama.context_length = 131072`, and ollama 0.30.11 was observed
  serving it at 32768. The real defect was never the size, it was that the size
  was UNSTATED: Ollama's default has changed across releases and is auto-sized
  against free VRAM at load time, so an overflow is silent and machine-dependent.
  `llm()` now pins it (`config.OLLAMA_NUM_CTX`).

* The same head-only slicing bug existed independently in TWO agents — the job
  scraper's rank node and the résumé generator's keywords node — over the same
  `description` field. The mechanism now lives once in `shell.prompt_text`.
"""

from __future__ import annotations

import pytest

import config
from shell import model_router
from shell.prompt_text import ELIDE, head_tail


# --------------------------------------------------------------------------
# The shared slicer.
# --------------------------------------------------------------------------

def test_head_tail_keeps_both_ends():
    head, tail = "OPENING: SWE Intern at Acme.", "Qualifications: Bachelor's, Python."
    text = head + ("boilerplate " * 500) + tail

    out = head_tail(text, head=60, tail=80)

    assert out.startswith("OPENING: SWE Intern at Acme.")
    assert out.endswith(tail), "the ENDING carries the qualifications"
    assert ELIDE in out, "an elided middle must be marked"
    assert len(out) == 60 + len(ELIDE) + 80


def test_head_tail_returns_short_text_whole_without_duplication():
    text = "Short JD. Qualifications: Python."
    assert head_tail(text, head=100, tail=100) == text
    assert head_tail(text, head=100, tail=100).count("Qualifications") == 1
    assert head_tail("", head=10, tail=10) == ""
    assert head_tail(None, head=10, tail=10) == ""


def test_head_tail_boundary_is_exactly_head_plus_tail():
    exact = "a" * 50
    assert head_tail(exact, head=25, tail=25) == exact
    over = "H" + "m" * 49 + "T"
    out = head_tail(over, head=25, tail=25)
    assert out.startswith("H") and out.endswith("T") and out != over


@pytest.mark.parametrize("head,tail", [(0, 100), (100, 0), (-1, 10), (10, -1)])
def test_head_tail_rejects_a_non_positive_bound(head, tail):
    """`text[-0:]` is the WHOLE string in Python.

    A zero tail would therefore return MORE text than either bound asks for —
    a silent overflow, which is the exact failure mode these budgets exist to
    prevent. Fail loudly instead.
    """
    with pytest.raises(ValueError):
        head_tail("x" * 500, head=head, tail=tail)


def test_both_agents_share_one_slicer_not_two_copies():
    """The bug was duplicated once already; a second copy would re-duplicate it."""
    from agents.job_scraper.nodes import rank
    from agents.resume_generator.nodes import keywords

    assert rank.head_tail is head_tail
    assert keywords.head_tail is head_tail


# --------------------------------------------------------------------------
# The pinned context window.
# --------------------------------------------------------------------------

def test_local_requests_pin_num_ctx(monkeypatch):
    seen: dict = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model_router.litellm, "completion", fake_completion)
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 4242)

    model_router.llm("local", "hi")

    assert seen["num_ctx"] == 4242, \
        "an unstated context window makes every prompt budget meaningless"
    assert seen["api_base"] == config.OLLAMA_API_BASE


def test_hosted_requests_do_not_get_num_ctx(monkeypatch):
    """`num_ctx` is an Ollama option; sending it to a hosted API is not valid."""
    seen: dict = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model_router.litellm, "completion", fake_completion)
    monkeypatch.setattr(config, "MODEL_ROLES", {"smart": "anthropic/claude-sonnet-5"})

    model_router.llm("smart", "hi")

    assert "num_ctx" not in seen
    assert "api_base" not in seen


def test_pinned_window_clears_the_largest_local_prompt():
    """The pin is only useful if it actually exceeds what callers build.

    Measured 2026-07-30 with litellm's token counter: the résumé `draft` node
    needs ~8,124 tokens of prompt+output and `latexify` ~7,558 with the user's
    real 9,108-char master résumé — both far above the 2048 the window was
    assumed to be, which is why pinning had to come with a measured value.
    """
    assert config.OLLAMA_NUM_CTX >= 16384, (
        "must stay clear of the ~8.2k tokens the résumé nodes need, with headroom"
    )


# --------------------------------------------------------------------------
# The résumé generator reads the same field and had the same bug.
# --------------------------------------------------------------------------

def test_resume_keyword_prompt_sees_the_qualifications():
    """The qualifications section IS the ATS keyword list.

    With full JDs now stored, the old head-only `[:3000]` handed the extractor
    3,000 chars of mission statement, so tailored résumés were keyword-matched
    against a company blurb.
    """
    from agents.resume_generator.nodes.keywords import _prompt

    tail = "Requirements: Python, React, PostgreSQL, Docker. Bachelor's in CS."
    job = {
        "title": "Software Engineering Intern", "company": "Acme",
        "description": "At Acme, we are passionate about data teams."
                       + ("company boilerplate. " * 400) + tail,
    }

    out = _prompt(job, research="")

    assert "Software Engineering Intern" in out
    assert tail in out, "the requirements must reach the keyword extractor"
    for term in ("Python", "React", "PostgreSQL", "Docker"):
        assert term in out, f"{term} is an ATS keyword and must be visible"


def test_resume_keyword_prompt_stays_within_its_budget():
    from agents.resume_generator.nodes import keywords as kw

    out = kw._prompt({"title": "T", "company": "C", "description": "x" * 60_000},
                     research="r" * 20_000)
    # JD budget + research budget + the small fixed scaffolding.
    assert len(out) < kw._DESC_HEAD + kw._DESC_TAIL + kw._RESEARCH_SLICE + 500
