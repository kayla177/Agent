"""Execute node — place the decided orders on the Alpaca PAPER account.

Only wired into the graph when send=True. Even then it refuses to trade if the
kill switch is set (data/TRADING_HALTED) or the broker isn't configured. Every
placed order is written to the local audit ledger.
"""

from __future__ import annotations

from agents.paper_trader import broker, store
from agents.paper_trader.state import TraderState


def execute_node(state: TraderState) -> TraderState:
    decisions = state.get("decisions", [])
    trades = [d for d in decisions if d["action"] in ("buy", "sell")]

    if store.is_halted():
        return {"executed": [{"halted": True}]}
    if not broker.is_configured():
        return {"executed": []}  # no keys — synthesize reports dry-run
    if not broker.market_is_open():
        return {"executed": [{"market_closed": True}]}  # don't queue orders off-hours

    executed: list[dict] = []
    for d in trades:
        try:
            if d["action"] == "buy":
                o = broker.submit_order(d["symbol"], "buy", notional=d["notional"])
                executed.append({"symbol": d["symbol"], "side": "buy",
                                 "notional": round(d["notional"], 2),
                                 "status": o.get("status", "submitted"), "reason": d["reason"]})
            else:
                o = broker.submit_order(d["symbol"], "sell", qty=d["qty"])
                executed.append({"symbol": d["symbol"], "side": "sell", "qty": d["qty"],
                                 "status": o.get("status", "submitted"), "reason": d["reason"]})
        except Exception as exc:
            executed.append({"symbol": d["symbol"], "side": d["action"], "error": str(exc)})

    store.append_trades([e for e in executed if "error" not in e and "halted" not in e])
    return {"executed": executed}
