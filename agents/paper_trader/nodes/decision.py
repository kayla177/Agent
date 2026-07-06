"""Decision node — fetch the (paper) account, apply the deterministic rules,
and have the LOCAL model write a one-line rationale for each trade. The rules
make the call; the LLM only explains it (grounded in the signals, no advice)."""

from __future__ import annotations

import config
from agents.paper_trader import broker, rules
from agents.paper_trader.state import TraderState
from shell.model_router import llm


def _rationale(d: dict) -> str:
    s = d.get("signals", {})
    try:
        return llm(
            "local",
            "In ONE short sentence for a beginner, explain this rule-based paper-"
            "trading decision by describing the signal only — no advice, no "
            f"predictions. Decision: {d['action'].upper()} {d['symbol']}; "
            f"RSI={s.get('rsi')}; rule reason: {d['reason']}.",
            max_tokens=80,
        ).strip()
    except Exception:
        return d["reason"]


def decision_node(state: TraderState) -> TraderState:
    rows = (state.get("market") or {}).get("rows", [])
    tech = (state.get("technical") or {}).get("by_symbol", {})

    if broker.is_configured():
        try:
            account = broker.get_account()
            positions = broker.get_positions()
        except Exception as exc:
            account = {"cash": config.TRADER_DRYRUN_CASH,
                       "portfolio_value": config.TRADER_DRYRUN_CASH, "error": str(exc)}
            positions = {}
    else:
        account = {"cash": config.TRADER_DRYRUN_CASH,
                   "portfolio_value": config.TRADER_DRYRUN_CASH, "simulated": True}
        positions = {}

    decisions = rules.decide(rows, tech, account, positions)
    for d in decisions:
        if d["action"] in ("buy", "sell"):
            d["rationale"] = _rationale(d)
    return {"account": account, "decisions": decisions}
