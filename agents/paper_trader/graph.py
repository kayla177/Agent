"""LangGraph definition for the paper-trader agent.

Reuses the stock-digest analyst nodes for signals, then decides and (optionally)
executes on Alpaca PAPER:

  market_data ─▶ technical ─▶ decision ─▶ [execute] ─▶ synthesize ─▶ [deliver]

`send=False` (preview / dry-run) stops after synthesize and places NO orders.
`send=True` adds execute (paper orders) + deliver (Discord). Even with send=True,
execute refuses to trade without Alpaca keys or when the kill switch is set.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

# Reused signal nodes from the stock-digest agent.
from agents.stock_digest.nodes.market_data import market_data_node
from agents.stock_digest.nodes.technical import technical_node

from agents.paper_trader.nodes.decision import decision_node
from agents.paper_trader.nodes.deliver import deliver_node
from agents.paper_trader.nodes.execute import execute_node
from agents.paper_trader.nodes.synthesize import synthesize_node
from agents.paper_trader.state import TraderState


def build_paper_trader_graph(*, send: bool = True):
    """Compile and return the trader graph. send=False = dry-run preview."""
    g = StateGraph(TraderState)

    g.add_node("market_data", market_data_node)
    g.add_node("technical", technical_node)
    g.add_node("decision", decision_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "market_data")
    g.add_edge("market_data", "technical")
    g.add_edge("technical", "decision")

    if send:
        g.add_node("execute", execute_node)
        g.add_node("deliver", deliver_node)
        g.add_edge("decision", "execute")
        g.add_edge("execute", "synthesize")
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("decision", "synthesize")
        g.add_edge("synthesize", END)

    return g.compile()
