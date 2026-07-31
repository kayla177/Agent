"""Unified model access via LiteLLM.

One function, `llm(role, ...)`, talks to either the local Ollama model or a
hosted API depending on the logical role defined in config.MODEL_ROLES. This is
the single seam that makes the hybrid (local <-> hosted) strategy a one-line
config change.

Being the single seam, it is also where the local context window is PINNED
(`config.OLLAMA_NUM_CTX`) rather than inherited from whatever the local Ollama
happens to default to. Callers here size their prompts against a token budget;
that budget is meaningless if the limit it is measured against is unstated.
"""

from __future__ import annotations

import re

import litellm

import config

# DeepSeek-R1 is a reasoning model that wraps its chain-of-thought in
# <think>...</think>. We strip it so downstream consumers get clean text.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean(text: str) -> str:
    return _THINK_BLOCK.sub("", text).strip()


def llm(
    role: str,
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    """Send a single-turn prompt to the model bound to `role`.

    Args:
        role: a key in config.MODEL_ROLES, e.g. "local" or "smart".
        prompt: the user message.
        system: optional system prompt.
        temperature / max_tokens: standard generation controls.

    Returns:
        The model's reply text, with reasoning blocks stripped.
    """
    model = config.MODEL_ROLES[role]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Local Ollama models need the API base; hosted models read their own env key.
    if model.startswith("ollama/"):
        kwargs["api_base"] = config.OLLAMA_API_BASE
        # Pin the context window instead of inheriting Ollama's default, which is
        # an UNKNOWN: it has changed across Ollama releases (2048 in older ones,
        # 32768 on 0.30.11 here) and is auto-sized against free VRAM at load
        # time, so the same prompt can fit on one run and be silently truncated
        # on the next — with no error, just a model that answers as if the end of
        # the prompt did not exist. Every prompt builder in this repo sizes
        # itself against a token budget (see the job scraper's `_desc_slice`),
        # and a budget measured against an unstated limit is not a budget.
        # `config.OLLAMA_NUM_CTX` documents the value and how it was chosen.
        kwargs["num_ctx"] = config.OLLAMA_NUM_CTX

    response = litellm.completion(**kwargs)
    return _clean(response["choices"][0]["message"]["content"] or "")


if __name__ == "__main__":
    # Smoke test: round-trip to the local model.
    reply = llm("local", "Reply with exactly: platform online")
    print(f"local model says -> {reply!r}")
