"""Tests for the latexify node — the résumé LaTeX tailoring step.

This node had NO tests, which is why it shipped a bug that made LaTeX tailoring
fail 100% of the time for weeks, silently, while reporting a misleading reason.

The bug, measured 2026-08-04 against `ollama/llama3.1:8b`: the model returns a
valid LaTeX document prefixed with a sentence —

    'Here is the tailored LaTeX document:\\n\\n\\n\\documentclass[letterpaper,11pt]...'

`_clean()` stripped markdown fences but not prose, so the gate's
`startswith("\\documentclass")` was False, the node raised
`CompileError("model did not return a full LaTeX document")`, and it fell back to
the untailored master **without ever invoking the compiler**. Slicing off those 39
characters, the same output compiled to a 33,575-byte PDF in 1.8 s.

Two things made it invisible:
  * the warning said "CompileError" for a case where no compile was attempted, so
    the message pointed at LaTeX validity rather than at output parsing;
  * the gate demanded `startswith` for the opening but only `in` for the closing —
    had it been symmetric, this would have worked by accident.

Every test here is hermetic: `llm` and `compile_tex` are both monkeypatched, so
nothing reaches Ollama or Tectonic.
"""

from __future__ import annotations

import pytest

from agents.resume_generator.latex import CompileError
from agents.resume_generator.nodes import latexify

# A minimal but structurally complete document, shaped like the real master.
DOC = (
    "\\documentclass[letterpaper,11pt]{article}\n"
    "\\usepackage{latexsym}\n"
    "\\begin{document}\n"
    "\\resumeItem{Built a thing.}\n"
    "\\end{document}"
)

MASTER = DOC.replace("Built a thing.", "Built the original thing.")

# The exact prefix observed from the local model on 2026-08-04.
REAL_PREAMBLE = "Here is the tailored LaTeX document:\n\n\n"


@pytest.fixture
def node(monkeypatch):
    """Drive `latexify_node` with a scripted model reply and compiler outcome."""

    def run(reply: str, *, compiles: bool = True, master: str = MASTER) -> dict:
        calls: list[str] = []

        def fake_llm(role, prompt, **kwargs):
            calls.append(prompt)
            return reply

        def fake_compile(tex: str) -> bytes:
            if not compiles:
                raise CompileError("! Undefined control sequence.\nl.4 \\bogus")
            return b"%PDF-1.7 fake"

        monkeypatch.setattr(latexify, "llm", fake_llm)
        monkeypatch.setattr(latexify, "compile_tex", fake_compile)
        out = latexify_state(master)
        result = latexify.latexify_node(out)
        result["_model_calls"] = calls
        return result

    return run


def latexify_state(master: str) -> dict:
    return {
        "master_latex": master,
        "job": {"title": "AI Research Scientist", "company": "Snowflake"},
        "keywords": ["Python", "PyTorch"],
        "company_research": "",
        "warnings": [],
    }


# --------------------------------------------------------------------------
# The regression. This is the bug that shipped.
# --------------------------------------------------------------------------


def test_a_conversational_preamble_is_stripped_and_the_document_is_accepted(node):
    """The exact failure observed in production, using the exact observed prefix.

    Before the fix this fell back to the master and warned about a "CompileError"
    that never happened.
    """
    out = node(REAL_PREAMBLE + DOC)
    assert out["latex"] == DOC, "the preamble must be removed and the document kept"
    assert not out.get("warnings"), f"should not warn, got {out.get('warnings')}"


def test_the_tailored_document_is_what_gets_returned_not_the_master(node):
    """Guards the actual user-visible symptom: every PDF was the untailored master.

    Asserting `!= master` is the point — a fallback that returns valid LaTeX still
    silently discards the tailoring work.
    """
    out = node(REAL_PREAMBLE + DOC)
    assert out["latex"] != MASTER


@pytest.mark.parametrize(
    "prefix",
    [
        "Here is the tailored LaTeX document:\n\n\n",
        "Sure! Here's the tailored resume:\n\n",
        "Here is the tailored LaTeX:\n",
        "```latex\n",
        "```\n",
        "Here you go:\n\n```latex\n",  # prose AND a fence
    ],
)
def test_every_wrapper_a_model_puts_before_the_source_is_removed(node, prefix):
    out = node(prefix + DOC)
    assert out["latex"].startswith("\\documentclass")
    assert not out.get("warnings")


def test_trailing_commentary_after_end_document_is_removed(node):
    """The mirror case. A model that explains itself afterwards is just as common."""
    out = node(DOC + "\n\nHope this helps! Let me know if you want changes.")
    assert out["latex"] == DOC
    assert not out.get("warnings")


def test_a_fenced_block_with_a_trailing_fence_is_unwrapped(node):
    """Pre-existing behaviour that must not regress."""
    out = node("```latex\n" + DOC + "\n```")
    assert out["latex"] == DOC
    assert not out.get("warnings")


# --------------------------------------------------------------------------
# The two failure modes must be DISTINGUISHABLE. Reporting a parse failure as a
# "CompileError" is what hid this bug: it aimed the reader at LaTeX validity.
# --------------------------------------------------------------------------


def test_output_that_is_not_a_document_falls_back_and_says_so(node):
    out = node("I'm sorry, I can't help with that.")
    assert out["latex"] == MASTER
    warning = " ".join(out["warnings"]).lower()
    assert "compileerror" not in warning, "no compile was attempted; do not blame the compiler"
    assert "document" in warning, "the warning must point at the output, not at LaTeX validity"


def test_a_document_that_will_not_compile_falls_back_and_says_something_different(node):
    out = node(DOC, compiles=False)
    assert out["latex"] == MASTER
    warning = " ".join(out["warnings"]).lower()
    assert "compile" in warning, "this one genuinely is a compile failure"


def test_the_two_failure_modes_do_not_share_a_message(node):
    """Pinned as a comparison, not as wording, so rephrasing stays free.

    Task 5 learned this the hard way: asserting an exact string makes the test
    brittle, while asserting nothing about the difference lets the two modes
    collapse back into one indistinguishable message.
    """
    not_a_doc = " ".join(node("nope").get("warnings", []))
    wont_build = " ".join(node(DOC, compiles=False).get("warnings", []))
    assert not_a_doc and wont_build
    assert not_a_doc != wont_build


def test_a_compile_failure_keeps_the_engine_log_for_diagnosis(node):
    """The reason was being discarded — only `type(exc).__name__` survived."""
    out = node(DOC, compiles=False)
    warning = " ".join(out["warnings"])
    assert "Undefined control sequence" in warning, (
        "the engine's actual complaint must reach the user, or the next silent "
        "fallback is invisible again"
    )


# --------------------------------------------------------------------------
# Invariants of the fallback path.
# --------------------------------------------------------------------------


def test_a_failure_never_sets_error_and_always_yields_usable_latex(node):
    for reply, compiles in [("nope", True), (DOC, False), ("", True)]:
        out = node(reply, compiles=compiles)
        assert not out.get("error"), "the Markdown draft is the primary output; never fail the run"
        assert out["latex"] == MASTER


def test_no_master_template_means_the_model_is_never_called(node, monkeypatch):
    """Skipping is cheap; a 60-second model call for a discardable result is not."""
    out = node(REAL_PREAMBLE + DOC, master="")
    assert out.get("latex") is None
    assert out["_model_calls"] == [], "no template to tailor -> do not call the model"


def test_an_error_already_in_state_short_circuits(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("llm must not be called when state carries an error")

    monkeypatch.setattr(latexify, "llm", boom)
    assert latexify.latexify_node({"error": "gather_failed", "master_latex": MASTER}) == {}


def test_the_structural_gate_is_symmetric():
    """Opening and closing markers are checked the same way.

    The shipped gate used `startswith` for `\\documentclass` and `in` for
    `\\end{document}`. That asymmetry is the whole bug: a symmetric gate would
    have accepted the model's output by accident.
    """
    assert latexify._is_complete_document(DOC)
    assert not latexify._is_complete_document("\\documentclass{article}")  # no closing
    assert not latexify._is_complete_document("\\end{document}")  # no opening
    # A prefix must not decide the outcome on its own — _clean removes it first.
    assert latexify._is_complete_document(latexify._clean(REAL_PREAMBLE + DOC))
    # The contract is MEMBERSHIP, and this is the assertion that pins it. Reverting
    # to `startswith` for the opening marker survives every other test in this file,
    # because `_clean` always strips the prefix first — so within the pipeline the
    # asymmetry is unobservable and the mutant is equivalent. It is pinned anyway:
    # the helper is callable directly, and the branch's own history says an
    # equivalence measured today can become load-bearing tomorrow (a `\b` measured
    # as equivalent in one round became the bug in the next, when a new alternative
    # turned out to contain an existing one).
    assert latexify._is_complete_document("junk " + DOC), (
        "membership, not position — _clean owns stripping, this owns completeness"
    )


def test_clean_is_idempotent():
    once = latexify._clean(REAL_PREAMBLE + DOC)
    assert latexify._clean(once) == once
