"""LangGraph definition for the gmail-sync agent (single node)."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.gmail_sync.node import scan_gmail_node
from agents.gmail_sync.state import GmailSyncState


def build_gmail_sync_graph(*, send: bool = False):
    """Compile the one-node gmail-sync graph. `send` is accepted for the
    registry contract but unused (no Discord delivery)."""
    g = StateGraph(GmailSyncState)
    g.add_node("scan_gmail", scan_gmail_node)
    g.add_edge(START, "scan_gmail")
    g.add_edge("scan_gmail", END)
    return g.compile()
