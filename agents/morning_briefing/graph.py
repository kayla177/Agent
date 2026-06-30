"""LangGraph definition for the morning-briefing agent.

START fans out to the four data nodes in parallel; they converge on synthesize,
which (optionally) flows to deliver. Each future agent will define its own graph
like this and reuse the same shell (model_router, discord_client).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.morning_briefing.nodes.calendar import calendar_node
from agents.morning_briefing.nodes.commute import commute_node
from agents.morning_briefing.nodes.deliver import deliver_node
from agents.morning_briefing.nodes.news import news_node
from agents.morning_briefing.nodes.synthesize import synthesize_node
from agents.morning_briefing.nodes.weather import weather_node
from agents.morning_briefing.state import BriefingState

_DATA_NODES = {
    "weather": weather_node,
    "commute": commute_node,
    "calendar": calendar_node,
    "news": news_node,
}


def build_briefing_graph(*, send: bool = True):
    """Compile and return the briefing graph. If `send` is False, stop at synthesize."""
    g = StateGraph(BriefingState)

    for name, fn in _DATA_NODES.items():
        g.add_node(name, fn)
        g.add_edge(START, name)          # parallel fan-out from START
        g.add_edge(name, "synthesize")   # converge — synthesize waits for all four

    g.add_node("synthesize", synthesize_node)

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("synthesize", END)

    return g.compile()
