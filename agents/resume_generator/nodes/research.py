"""Research node — pull company/posting context the local model can't fetch itself.

A local model has no internet, so we fetch here in Python (httpx) and hand the
text to later nodes:
  1. the stored posting `url` (the full JD, in case the saved description was
     truncated), and
  2. a best-effort company page (home / about / careers), guessed from the
     company name.

Everything is best-effort with a short timeout. Any failure degrades to less (or
no) research text and records a warning — it never raises, so a flaky network or
an unreachable site can't block resume generation.
"""

from __future__ import annotations

import re

import httpx

from agents.resume_generator.state import ResumeState

_TIMEOUT = 8.0
_MAX_CHARS = 4000  # cap per fetched page before handing to the model
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-agents/1.0)"}

_TAG_RE = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")
_ANY_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _html_to_text(html: str) -> str:
    """Crude but dependency-free HTML->text: drop script/style, strip tags."""
    no_blocks = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", no_blocks)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n", text)
    return text.strip()


def _fetch(client: httpx.Client, url: str) -> str:
    """Return cleaned text for a URL, capped; "" on any non-200 or error."""
    try:
        resp = client.get(url)
        if resp.status_code != 200 or not resp.text:
            return ""
        return _html_to_text(resp.text)[:_MAX_CHARS]
    except (httpx.HTTPError, ValueError):
        return ""


def _company_candidates(company: str) -> list[str]:
    """Guess a few likely company URLs from the company name."""
    slug = _SLUG_RE.sub("", (company or "").lower())
    if not slug:
        return []
    base = f"https://www.{slug}.com"
    return [f"{base}/careers", f"{base}/about", base]


def research_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}

    job = state.get("job") or {}
    warnings = list(state.get("warnings", []))
    parts: list[str] = []

    with httpx.Client(
        timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
    ) as client:
        url = (job.get("url") or "").strip()
        if url:
            posting = _fetch(client, url)
            if posting:
                parts.append(f"POSTING PAGE ({url}):\n{posting}")
            else:
                warnings.append(f"research: could not fetch posting {url}")

        # First company page that responds wins — don't hammer every guess.
        for candidate in _company_candidates(job.get("company", "")):
            page = _fetch(client, candidate)
            if page:
                parts.append(f"COMPANY PAGE ({candidate}):\n{page}")
                break

    return {"company_research": "\n\n".join(parts), "warnings": warnings}
