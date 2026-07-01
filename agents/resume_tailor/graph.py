"""LangGraph definition for the résumé-tailor agent.

load -> (tailor, cover in parallel) -> synthesize -> (deliver). Input is passed
in state (job_description / company / role); unlike the scheduled agents this one
is invoked on demand from the web page or CLI.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.resume_tailor.nodes.cover import cover_node
from agents.resume_tailor.nodes.deliver import deliver_node
from agents.resume_tailor.nodes.load import load_node
from agents.resume_tailor.nodes.synthesize import synthesize_node
from agents.resume_tailor.nodes.tailor import tailor_node
from agents.resume_tailor.state import TailorState


def build_resume_tailor_graph(*, send: bool = True):
    """Compile and return the résumé-tailor graph."""
    g = StateGraph(TailorState)

    g.add_node("load", load_node)
    g.add_node("tailor", tailor_node)
    g.add_node("cover", cover_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "load")
    g.add_edge("load", "tailor")     # both read base_resume + jd from state;
    g.add_edge("load", "cover")      # run in parallel after load
    g.add_edge("tailor", "synthesize")
    g.add_edge("cover", "synthesize")

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("synthesize", END)

    return g.compile()
