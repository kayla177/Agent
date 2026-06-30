"""Synthesize node — assemble the deterministic stock digest message.

Numbers (the quotes block) come straight from the quotes node and are placed
verbatim so they can never be hallucinated. The LLM-written piece is only the
headline summary. A non-negotiable disclaimer makes clear this is information,
NOT financial advice.
"""

from __future__ import annotations

import datetime as dt

from agents.stock_digest.state import StockDigestState

_DISCLAIMER = (
    "_Information only — not financial advice. Figures are delayed and may be "
    "inaccurate; verify before acting._"
)


def synthesize(state: StockDigestState) -> str:
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    parts = [
        f"**📈 Market Digest — {today}**",
        _DISCLAIMER,
        "",
        "**Watchlist**",
        state.get("quotes", "n/a"),
        "",
        "**Market headlines**",
        state.get("headlines", "n/a"),
    ]
    return "\n".join(parts)


def synthesize_node(state: StockDigestState) -> StockDigestState:
    return {"message": synthesize(state)}
