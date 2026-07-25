"""Analyst node — the beginner's translator. Turns the deterministic indicators
+ news sentiment into a plain-English verdict per stock, plus a one-line market
read for the overview.

The numbers and the per-signal explanations ALWAYS come from `analysis.py`
(pure math). This node adds only holistic prose: a short summary, a
bullish/neutral/bearish verdict, and the main risks / catalysts / a learn note.
It uses the hosted "smart" model (Claude) via the shared `llm()` router; on any
model or JSON-parse failure it degrades to the deterministic report so the page
always renders. Constrained to DESCRIBE — never buy/sell advice.
"""

from __future__ import annotations

import json
import re

from agents.stock_digest import analysis
from agents.stock_digest.state import StockDigestState
from shell.model_router import llm

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_ROLE = "smart"  # hosted Claude; falls back to deterministic if the key is unset

_SYSTEM = (
    "You are a patient investing tutor writing for a COMPLETE BEGINNER. You are "
    "given already-computed facts about some stocks (prices, momentum indicators, "
    "recent-news sentiment) and a deterministic 'lean' for each. You must NOT "
    "invent or change any number, and you must NOT give buy/sell/hold advice or "
    "price predictions — only describe what's going on in plain, jargon-free "
    "language and note what a beginner should watch. "
    "Reply with ONLY a JSON object (no prose, no code fences):\n"
    '{"stocks": [{"i": <index>, "verdict": "bullish|neutral|bearish", '
    '"score": <-2..2 integer>, "summary": "<2-3 plain sentences: what\'s going on '
    'and why, beginner tone>", "risks": ["<short risk>", ...], '
    '"catalysts": ["<short thing that could push it up>", ...], '
    '"learn": "<one concept from this stock worth understanding, one sentence>"}], '
    '"market_read": "<one plain sentence on the overall market mood today>"}'
)


def _facts(idx: int, row: dict, tech: dict, news: dict, base: dict) -> str:
    price = row.get("price")
    pct = row.get("pct")
    bits = [
        f"[{idx}] {row.get('symbol', '?')}",
        f"price={'n/a' if price is None else f'{price:.2f}'} "
        f"({'n/a' if pct is None else f'{pct:+.1f}% today'})",
        f"deterministic_lean={base['verdict']} (score {base['score']:+d}, {base['reason']})",
    ]
    if tech.get("rsi") is not None:
        bits.append(f"RSI={tech['rsi']:.0f}")
    if tech.get("trend"):
        bits.append(f"trend={tech['trend']}")
    if tech.get("macd_hist") is not None:
        bits.append(f"MACD={'positive' if tech['macd_hist'] > 0 else 'negative'}")
    if tech.get("pct_from_high") is not None:
        bits.append(f"{tech['pct_from_high']:+.1f}% vs 52wk high")
    if news:
        bits.append(f"news_sentiment={news.get('score', 0):+.2f} ({news.get('theme', '')})")
    return " · ".join(bits)


def _prompt(facts: list[str], overview: dict) -> str:
    indices = overview.get("indices", [])
    idx_line = ", ".join(
        f"{i.get('name', i.get('symbol'))} {i.get('pct'):+.1f}%"
        for i in indices if i.get("pct") is not None
    ) or "n/a"
    return (
        f"MARKET TODAY: {idx_line}\n\nSTOCKS:\n" + "\n".join(facts) +
        "\n\nWrite the JSON now."
    )


def _parse(reply: str) -> dict:
    match = _JSON_RE.search(reply or "")
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _clean_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:4]


def analyst_node(state: StockDigestState) -> StockDigestState:
    rows = (state.get("market") or {}).get("rows", [])
    tech_by = (state.get("technical") or {}).get("by_symbol", {})
    news_by = (state.get("news") or {}).get("by_symbol", {})
    overview = state.get("overview") or {}
    warnings: list[str] = []

    # 1) Deterministic base for every symbol (never fails).
    order: list[str] = []
    reports: dict[str, dict] = {}
    facts: list[str] = []
    for row in rows:
        sym = row.get("symbol")
        if not sym:
            continue
        tech = tech_by.get(sym, {})
        base = analysis.deterministic_report(row, tech)
        base["price"] = row.get("price")
        base["pct"] = row.get("pct")
        reports[sym] = base
        order.append(sym)
        facts.append(_facts(len(order) - 1, row, tech, news_by.get(sym, {}), base))

    market_read = overview.get("read") or ""

    # 2) Enrich with the LLM (best-effort; deterministic base already stands).
    if facts:
        try:
            reply = llm(_ROLE, _prompt(facts, overview), system=_SYSTEM,
                        temperature=0.4, max_tokens=2000)
            parsed = _parse(reply)
        except Exception as exc:  # no key / transport / model error
            parsed = {}
            warnings.append(f"ℹ️ AI analysis unavailable ({exc}); showing signal-only read.")

        for item in parsed.get("stocks", []) if isinstance(parsed, dict) else []:
            try:
                i = int(item["i"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= i < len(order)):
                continue
            rep = reports[order[i]]
            verdict = str(item.get("verdict", "")).lower()
            if verdict in analysis.VERDICTS:
                rep["verdict"] = verdict
            raw = item.get("score")
            if isinstance(raw, (int, float)):
                rep["score"] = max(-2, min(2, int(raw)))
            if str(item.get("summary", "")).strip():
                rep["summary"] = str(item["summary"]).strip()
            rep["risks"] = _clean_list(item.get("risks")) or rep["risks"]
            rep["catalysts"] = _clean_list(item.get("catalysts")) or rep["catalysts"]
            if str(item.get("learn", "")).strip():
                rep["learn"] = str(item["learn"]).strip()

        read = str(parsed.get("market_read", "")).strip() if isinstance(parsed, dict) else ""
        if read:
            market_read = read

    return {"analysis": {"by_symbol": reports, "order": order,
                         "market_read": market_read, "warnings": warnings}}
