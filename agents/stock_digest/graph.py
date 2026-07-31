"""LangGraph definition for the multi-analyst stock-digest agent.

Branches fan out from START and converge on the analyst, which writes the
beginner-facing verdicts; synthesize then formats the digest and persist saves
the analysis for the /stocks desk:

  START ─┬─▶ market_data ─┬─▶ technical ────────┐
         │                └─▶ market_overview ───┤
         └─▶ news_sentiment ─────────────────────┴─▶ analyst ─▶ synthesize ─▶ persist ─▶ [deliver]

market_data and news_sentiment run in parallel (disjoint state keys). technical
and market_overview both depend on market_data's rows (price history / movers)
and run in parallel with each other. analyst fans in on all three so it has the
indicators, the overview, and the news sentiment. Reuses the shared shell
(model_router, discord_client) like the other agents.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.stock_digest.nodes.analyst import analyst_node
from agents.stock_digest.nodes.deliver import deliver_node
from agents.stock_digest.nodes.market_data import market_data_node
from agents.stock_digest.nodes.market_overview import market_overview_node
from agents.stock_digest.nodes.news_sentiment import news_sentiment_node
from agents.stock_digest.nodes.persist import persist_node
from agents.stock_digest.nodes.synthesize import synthesize_node
from agents.stock_digest.nodes.technical import technical_node
from agents.stock_digest.state import StockDigestState


def build_stock_digest_graph(*, send: bool = True):
    """Compile and return the digest graph. If `send` is False, stop at persist."""
    g = StateGraph(StockDigestState)

    g.add_node("market_data", market_data_node)
    g.add_node("technical", technical_node)
    g.add_node("market_overview", market_overview_node)
    g.add_node("news_sentiment", news_sentiment_node)
    g.add_node("analyst", analyst_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("persist", persist_node)

    # Parallel branches from START.
    g.add_edge(START, "market_data")
    g.add_edge(START, "news_sentiment")
    # technical + market_overview both need market_data's rows.
    g.add_edge("market_data", "technical")
    g.add_edge("market_data", "market_overview")
    # analyst fans in on indicators, overview, and news sentiment.
    g.add_edge("technical", "analyst")
    g.add_edge("market_overview", "analyst")
    g.add_edge("news_sentiment", "analyst")
    # Format the digest, then persist the analysis for the desk.
    g.add_edge("analyst", "synthesize")
    g.add_edge("synthesize", "persist")

    if send:
        g.add_node("deliver", deliver_node)
        g.add_edge("persist", "deliver")
        g.add_edge("deliver", END)
    else:
        g.add_edge("persist", END)

    return g.compile()
