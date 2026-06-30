"""Shared Jinja2 templates + a server-side markdown renderer.

Agent output is Discord-flavored markdown; we render it to HTML here so the
browser shows formatted briefings. Imported by the routers so they all share one
configured ``templates`` instance.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_DIR))

_md = MarkdownIt("commonmark", {"linkify": True, "breaks": True}).enable("linkify")


def render_markdown(text: str | None) -> str:
    """Render markdown to HTML (used for agent output blocks)."""
    return _md.render(text or "")


# Make the markdown renderer available inside templates as `md(...)`.
templates.env.globals["md"] = render_markdown
