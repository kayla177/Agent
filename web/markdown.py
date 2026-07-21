"""Server-side markdown → HTML for agent output blocks (Discord-flavored md)."""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"linkify": True, "breaks": True}).enable("linkify")


def render_markdown(text: str | None) -> str:
    return _md.render(text or "")
