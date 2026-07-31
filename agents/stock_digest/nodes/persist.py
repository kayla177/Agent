"""Persist node — save this run's analysis so the /stocks desk can serve it
without re-running the model on every page load. Best-effort: a storage failure
must never fail the run (the digest message is already assembled)."""

from __future__ import annotations

from agents.stock_digest import store
from agents.stock_digest.state import StockDigestState


def persist_node(state: StockDigestState) -> StockDigestState:
    analysis = state.get("analysis") or {}
    by_symbol = analysis.get("by_symbol") or {}
    if not by_symbol:
        return {}
    overview = state.get("overview") or {}
    try:
        store.save_analysis(
            by_symbol,
            order=analysis.get("order", list(by_symbol)),
            overview=overview,
            market_read=analysis.get("market_read", ""),
        )
    except Exception as exc:  # storage is a convenience, not a hard dependency
        print(f"⚠️ stock analysis persist failed: {exc}")
    return {}
