"""LangGraph definition for the application-tracker agent.

Linear pipeline: summary -> followups -> synthesize -> (deliver). Matches the
registry contract: starts from empty state, final output in state["message"].
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.application_tracker.nodes.deliver import deliver_node
from agents.application_tracker.nodes.followups import followups_node
from agents.application_tracker.nodes.summary import summary_node
from agents.application_tracker.nodes.synthesize import synthesize_node
from agents.application_tracker.state import TrackerState


def build_tracker_graph(*, send: bool = True):
    """Compile and return the tracker graph. If `send` is False, stop at synthesize."""
    g = StateGraph(TrackerState)

    g.add_node("summary", summary_node)
    g.add_node("followups", followups_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "summary")
    g.add_edge("summary", "followups")
    g.add_edge("followups", "synthesize")

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("synthesize", END)

    return g.compile()
