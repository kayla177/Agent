"""LangGraph definition for the job-scraper agent.

Linear pipeline: fetch -> filter -> dedupe -> backfill -> freshness -> rank -> notify.
Unlike the morning briefing (parallel fan-out), each stage depends on the
previous one's output, so the edges are a straight chain.

`build_job_scraper_graph(send=True)` wires the notify node to deliver to
Discord; `send=False` builds the same graph but notify only assembles +
persists (no Discord), matching the briefing's print-only dev mode.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.job_scraper.nodes.backfill import backfill_node
from agents.job_scraper.nodes.dedupe import dedupe_node
from agents.job_scraper.nodes.fetch import fetch_node
from agents.job_scraper.nodes.filter import filter_node
from agents.job_scraper.nodes.freshness import freshness_node
from agents.job_scraper.nodes.notify import make_notify_node
from agents.job_scraper.nodes.rank import rank_node
from agents.job_scraper.state import JobScraperState


def build_job_scraper_graph(*, send: bool = True):
    """Compile and return the job-scraper graph."""
    g = StateGraph(JobScraperState)

    g.add_node("fetch", fetch_node)
    g.add_node("filter", filter_node)
    g.add_node("dedupe", dedupe_node)
    g.add_node("backfill", backfill_node)
    g.add_node("freshness", freshness_node)
    g.add_node("rank", rank_node)
    g.add_node("notify", make_notify_node(send=send))

    g.add_edge(START, "fetch")
    g.add_edge("fetch", "filter")
    g.add_edge("filter", "dedupe")
    g.add_edge("dedupe", "backfill")
    g.add_edge("backfill", "freshness")
    g.add_edge("freshness", "rank")
    g.add_edge("rank", "notify")
    g.add_edge("notify", END)

    return g.compile()
