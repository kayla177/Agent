"""Base résumé loading, draft persistence, and model-role selection.

Base résumé lives at data/resume.md (the user replaces the placeholder).
Tailored drafts are saved under data/drafts/ as markdown.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import config

_RESUME = config.PROJECT_ROOT / "data" / "resume.md"
_DRAFTS = config.PROJECT_ROOT / "data" / "drafts"


def smart_role() -> str:
    """Use the hosted 'smart' model when its key is set, else fall back to local.

    Résumé writing wants the strongest model available; this makes the agent
    work today on the local model and auto-upgrade the moment a key is added.
    """
    return "smart" if config.ANTHROPIC_API_KEY else "local"


def load_base_resume() -> str:
    try:
        return _RESUME.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def base_resume_status() -> dict:
    """For the UI: whether a résumé exists and whether it's still the placeholder."""
    text = load_base_resume()
    return {
        "present": bool(text),
        "is_placeholder": "PLACEHOLDER RÉSUMÉ" in text,
        "chars": len(text),
        "path": str(_RESUME),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "untitled"


def save_draft(company: str, role: str, content: str) -> Path:
    """Persist a tailored draft; returns the file path."""
    _DRAFTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    path = _DRAFTS / f"{stamp}_{_slug(company)}_{_slug(role)}.md"
    path.write_text(content, encoding="utf-8")
    return path


def list_drafts() -> list[dict]:
    """Recent drafts (newest first) for the UI."""
    if not _DRAFTS.exists():
        return []
    files = sorted(_DRAFTS.glob("*.md"), reverse=True)
    return [{"name": f.name, "path": str(f)} for f in files]
