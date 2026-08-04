"""The suite-wide model guard, verified from a DIFFERENT file on purpose.

`no_real_model` used to be an autouse fixture inside
`tests/test_applier_drafting.py`, which meant it protected exactly one file. A
one-line test in another file calling `drafting.draft_one` without stubbing
reached the live Ollama and came back `source="drafted"` — passing on a machine
with Ollama up, failing everywhere else. Tasks 6, 7 and 8 all consume
`agents.job_applier.drafting`, so that was one file away from being everybody's
problem.

This file IS that experiment, encoded as tests that must keep passing. It has no
fixtures of its own, imports nothing from the drafting test module, and would
fail if the guard were narrowed back to one file.
"""

from __future__ import annotations

import litellm
import pytest

from agents.job_applier import drafting
from agents.job_applier.schema_greenhouse import Question
from conftest import ModelCalledInTest


def _draftable() -> Question:
    """A question that genuinely WOULD be drafted — the one real Lever prompt that
    reaches the model. A guard test built on a question drafting refuses anyway
    would pass with no guard at all."""
    question = Question(
        key="why", label="Why do you want to work at Palantir?",
        required=True, kind="textarea",
    )
    assert drafting.topic_of(question) in drafting.DRAFTABLE_TOPICS, "premise"
    return question


def test_an_unstubbed_draft_in_another_file_fails_loudly():
    """The reviewer's exact one-liner. `draft_one` catches `Exception` on purpose,
    so the guard has to raise something that escapes it — otherwise this would
    come back as an innocent-looking blank and assert nothing."""
    with pytest.raises(ModelCalledInTest):
        drafting.draft_one(_draftable(), job={"title": "T", "company": "C"})


def test_the_guard_escapes_the_broad_except_in_draft_one():
    """Stated as its own property, because it is the reason the guard class derives
    from `BaseException`. If someone "tidies" it to `Exception`, this fails while
    the test above starts passing for the wrong reason (a blank, not a raise)."""
    assert issubclass(ModelCalledInTest, BaseException)
    assert not issubclass(ModelCalledInTest, Exception)


def test_the_module_level_llm_binding_is_repointed():
    """`from shell.model_router import llm` rebinds the function INTO the importing
    module, so patching `model_router` alone would miss every real caller."""
    with pytest.raises(ModelCalledInTest):
        drafting.llm("local", "hello")


def test_the_litellm_backstop_is_repointed():
    """The actual network boundary, guarded for a caller imported after the fixture
    ran or one that calls `model_router.llm` directly."""
    with pytest.raises(ModelCalledInTest):
        litellm.completion(model="ollama/llama3.1:8b", messages=[])


def test_the_router_itself_is_left_callable():
    """`shell.model_router.llm` is deliberately NOT replaced —
    `tests/test_prompt_budgets.py` calls it on purpose with `litellm.completion`
    stubbed, to measure the kwargs it builds. It still cannot reach the network,
    because the backstop above is what it calls."""
    from shell import model_router
    assert model_router.llm is not drafting.llm
    with pytest.raises(ModelCalledInTest):
        model_router.llm("local", "hello")


def test_a_stubbed_caller_still_works():
    """The guard must not make legitimate stubbing harder: a test that provides a
    model gets its own model, in both supported shapes."""
    long_enough = "I would like this job because the posting matches my work." * 2
    answer = drafting.draft_one(
        _draftable(), job={"title": "T", "company": "C"},
        llm_fn=lambda *a, **k: long_enough,
    )
    assert answer.source == "drafted"
