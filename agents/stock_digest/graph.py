"""LangGraph definition for the multi-analyst stock-digest agent.

Two branches fan out from START and converge on synthesize:

  START ─┬─▶ market_data ─▶ technical ─┐
         └─▶ news_sentiment ───────────┴─▶ synthesize ─▶ [deliver]

market_data and news_sentiment run in parallel (disjoint state keys); technical
runs after market_data because it needs the price history. Reuses the shared
shell (model_router, discord_client) like the other agents.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.stock_digest.nodes.deliver import deliver_node
from agents.stock_digest.nodes.market_data import market_data_node
from agents.stock_digest.nodes.news_sentiment import news_sentiment_node
from agents.stock_digest.nodes.synthesize import synthesize_node
from agents.stock_digest.nodes.technical import technical_node
from agents.stock_digest.state import StockDigestState


def build_stock_digest_graph(*, send: bool = True):
    """Compile and return the digest graph. If `send` is False, stop at synthesize."""
    g = StateGraph(StockDigestState)

    g.add_node("market_data", market_data_node)
    g.add_node("technical", technical_node)
    g.add_node("news_sentiment", news_sentiment_node)
    g.add_node("synthesize", synthesize_node)

    # Parallel branches from START.
    g.add_edge(START, "market_data")
    g.add_edge(START, "news_sentiment")
    # technical depends on market_data's price history.
    g.add_edge("market_data", "technical")
    # Both branches converge on synthesize.
    g.add_edge("technical", "synthesize")
    g.add_edge("news_sentiment", "synthesize")

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("synthesize", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("synthesize", END)

    return g.compile()
