"""LangGraph definition for the resume-generator agent.

Linear pipeline: gather -> research -> keywords -> draft -> save. Each stage
depends on the previous one's output, so the edges form a straight chain (like
the job scraper). Prerequisite failures don't need conditional edges: `gather`
sets `state["error"]` and every later node returns early on it, flowing straight
through to `save`, which passes the error message out untouched.

`build_resume_generator_graph(send=...)` takes the same `send` kwarg the agent
registry passes to every builder; this agent has no Discord delivery, so the flag
is accepted and ignored. Invoke with `{"job_id": "<id>"}`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.resume_generator.nodes.draft import draft_node
from agents.resume_generator.nodes.gather import gather_node
from agents.resume_generator.nodes.keywords import keywords_node
from agents.resume_generator.nodes.research import research_node
from agents.resume_generator.nodes.save import save_node
from agents.resume_generator.state import ResumeState


def build_resume_generator_graph(*, send: bool = False):
    """Compile and return the resume-generator graph (`send` is accepted, unused)."""
    g = StateGraph(ResumeState)

    g.add_node("gather", gather_node)
    g.add_node("research", research_node)
    g.add_node("keywords", keywords_node)
    g.add_node("draft", draft_node)
    g.add_node("save", save_node)

    g.add_edge(START, "gather")
    g.add_edge("gather", "research")
    g.add_edge("research", "keywords")
    g.add_edge("keywords", "draft")
    g.add_edge("draft", "save")
    g.add_edge("save", END)

    return g.compile()
