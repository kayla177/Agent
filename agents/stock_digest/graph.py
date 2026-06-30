"""LangGraph definition for the stock-digest agent.

START fans out to the two data nodes (quotes, headlines) in parallel; they
converge on synthesize, which (optionally) flows to deliver. Reuses the same
shell (model_router, discord_client) as the other agents.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.stock_digest.nodes.deliver import deliver_node
from agents.stock_digest.nodes.headlines import headlines_node
from agents.stock_digest.nodes.quotes import quotes_node
from agents.stock_digest.nodes.synthesize import synthesize_node
from agents.stock_digest.state import StockDigestState

_DATA_NODES = {
    "quotes": quotes_node,
    "headlines": headlines_node,
}


def build_stock_digest_graph(*, send: bool = True):
    """Compile and return the digest graph. If `send` is False, stop at synthesize."""
    g = StateGraph(StockDigestState)

    for name, fn in _DATA_NODES.items():
        g.add_node(name, fn)
        g.add_edge(START, name)          # parallel fan-out from START
        g.add_edge(name, "synthesize")   # converge — synthesize waits for both

    g.add_node("synthesize", synthesize_node)

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("synthesize", END)

    return g.compile()
