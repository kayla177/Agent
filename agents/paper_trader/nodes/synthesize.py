"""Synthesize node — assemble the trader report. Facts (portfolio, decisions,
fills) come straight from state; the LLM only supplied per-trade rationales
earlier. Always makes clear this is SIMULATED paper money, not advice."""

from __future__ import annotations

import datetime as dt

from agents.paper_trader.state import TraderState

_ICON = {"buy": "🟢 BUY", "sell": "🔴 SELL", "hold": "⚪ HOLD"}
_DISCLAIMER = (
    "_Simulated paper trading (Alpaca) — not real money and not financial "
    "advice. A rule-based experiment; it may lose (fake) money._"
)


def synthesize(state: TraderState) -> str:
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    account = state.get("account") or {}
    decisions = state.get("decisions", [])
    executed = state.get("executed")  # None => preview graph (no execute node)

    parts = [f"**🤖 Paper Trader — {today}**", _DISCLAIMER, ""]

    pv, cash = account.get("portfolio_value"), account.get("cash")
    if pv is not None:
        tag = " _(simulated)_" if account.get("simulated") else ""
        parts.append(f"**Portfolio** ${pv:,.2f} · cash ${cash:,.2f}{tag}")
        parts.append("")

    trades = [d for d in decisions if d["action"] in ("buy", "sell")]
    holds = [d for d in decisions if d["action"] == "hold"]

    parts.append("**Decisions**")
    if trades:
        for d in trades:
            amt = (f"${d['notional']:.0f}" if d["action"] == "buy" else f"{d['qty']:g} sh")
            parts.append(f"{_ICON[d['action']]} {d['symbol']} {amt} — {d.get('rationale', d['reason'])}")
    else:
        parts.append("No buy/sell signals today — holding.")
    if holds:
        parts.append("")
        parts.append("_Holding:_ " + ", ".join(f"{h['symbol']}" for h in holds))

    # Execution status
    parts.append("")
    if executed is None:
        parts.append("**Status** — preview (dry-run): no orders placed. Use *Run & send* to trade paper.")
    elif executed and executed[0].get("halted"):
        parts.append("**Status** — ⛔ trading halted by kill switch (data/TRADING_HALTED).")
    elif not executed and not trades:
        parts.append("**Status** — nothing to execute.")
    elif not executed:
        parts.append("**Status** — ⚠️ no Alpaca keys set, so no paper orders placed. Add ALPACA_API_KEY_ID / _SECRET_KEY.")
    else:
        ok = [e for e in executed if "error" not in e]
        errs = [e for e in executed if "error" in e]
        parts.append(f"**Executed** {len(ok)} paper order(s):")
        for e in ok:
            amt = f"${e['notional']:.0f}" if e["side"] == "buy" else f"{e.get('qty','?')} sh"
            parts.append(f"• {e['side'].upper()} {e['symbol']} {amt} — {e.get('status','')}")
        for e in errs:
            parts.append(f"• ⚠️ {e['side'].upper()} {e['symbol']} failed: {e['error']}")

    return "\n".join(parts)


def synthesize_node(state: TraderState) -> TraderState:
    return {"message": synthesize(state)}
