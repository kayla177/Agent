"""LangGraph definition for the cover-letter agent.

Linear chain: gather -> draft -> save.

THREE nodes, not the résumé's six, and each omission is deliberate:
  * no `keywords` — ATS keyword-matching a letter produces prose that reads as
    keyword-stuffed, which costs more than it gains;
  * no `research` — the posting's description is already in the `jobs` row, and a
    letter needs the posting's own words more than a summary of them;
  * no `latexify` — PDF export is Phase 2.

`send` is accepted and ignored, the contract every registry builder has; there is
no Discord delivery for a per-job document. Invoke with `{"job_id": "<id>"}`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.cover_letter_generator.nodes.draft import draft_node
from agents.cover_letter_generator.nodes.gather import gather_node
from agents.cover_letter_generator.nodes.save import save_node
from agents.cover_letter_generator.state import CoverLetterState


def build_cover_letter_graph(*, send: bool = False):
    """Compile and return the cover-letter graph (`send` is accepted, unused)."""
    g = StateGraph(CoverLetterState)

    g.add_node("gather", gather_node)
    g.add_node("draft", draft_node)
    g.add_node("save", save_node)

    g.add_edge(START, "gather")
    g.add_edge("gather", "draft")
    g.add_edge("draft", "save")
    g.add_edge("save", END)

    return g.compile()
