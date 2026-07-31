"""SQLite store for stock-analysis snapshots (the /stocks desk's data source).

Backed by the `stock_analysis` table in data/control_center.db (see schema.sql).
Every run writes one row per analyzed symbol plus a single `__market__` row
holding the overview blob; all rows of a run share one `run_at` timestamp.
`latest_analysis()` returns the most recent run. Reads degrade gracefully
(missing table -> None) so a fresh install just falls back to the live path.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import store_db

MARKET_KEY = "__market__"

_INSERT = (
    "INSERT INTO stock_analysis (symbol, run_at, verdict, score, signal, price, pct, data) "
    "VALUES (:symbol, :run_at, :verdict, :score, :signal, :price, :pct, :data) "
    "ON CONFLICT(run_at, symbol) DO UPDATE SET "
    "verdict=excluded.verdict, score=excluded.score, signal=excluded.signal, "
    "price=excluded.price, pct=excluded.pct, data=excluded.data"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def save_analysis(
    by_symbol: dict[str, dict],
    *,
    order: list[str],
    overview: dict,
    market_read: str,
    run_at: str | None = None,
) -> str:
    """Persist one run's per-symbol reports + the overview. Returns the run_at."""
    run_at = run_at or _now()
    store_db.init_db()
    with store_db.connect() as conn:
        for rank, sym in enumerate(order):
            rep = by_symbol.get(sym)
            if not rep:
                continue
            conn.execute(_INSERT, {
                "symbol": sym,
                "run_at": run_at,
                "verdict": rep.get("verdict", ""),
                "score": rep.get("score"),
                "signal": rep.get("signal", ""),
                "price": rep.get("price"),
                "pct": rep.get("pct"),
                "data": json.dumps({**rep, "ord": rank}, ensure_ascii=False),
            })
        conn.execute(_INSERT, {
            "symbol": MARKET_KEY,
            "run_at": run_at,
            "verdict": "", "score": None, "signal": "", "price": None, "pct": None,
            "data": json.dumps({"overview": overview, "read": market_read}, ensure_ascii=False),
        })
    return run_at


def latest_analysis() -> dict | None:
    """Return the most recent run as {run_at, cards, market} or None if empty."""
    try:
        with store_db.connect() as conn:
            top = conn.execute("SELECT MAX(run_at) AS r FROM stock_analysis").fetchone()
            if not top or not top["r"]:
                return None
            run_at = top["r"]
            rows = conn.execute(
                "SELECT symbol, verdict, score, signal, price, pct, data "
                "FROM stock_analysis WHERE run_at = ?", (run_at,)
            ).fetchall()
    except sqlite3.OperationalError:
        return None

    cards: list[dict] = []
    market: dict | None = None
    for r in rows:
        try:
            data = json.loads(r["data"]) if r["data"] else {}
        except json.JSONDecodeError:
            data = {}
        if r["symbol"] == MARKET_KEY:
            market = data
            continue
        cards.append({
            "symbol": r["symbol"],
            "verdict": r["verdict"] or "neutral",
            "score": r["score"],
            "signal": r["signal"] or "",
            "price": r["price"],
            "pct": r["pct"],
            "summary": data.get("summary", ""),
            "signals": data.get("signals", []),
            "risks": data.get("risks", []),
            "catalysts": data.get("catalysts", []),
            "learn": data.get("learn", ""),
            "rsi": data.get("rsi"),
            "reason": data.get("reason", ""),
            "ord": data.get("ord", 999),
        })
    cards.sort(key=lambda c: c.pop("ord", 999))
    return {"run_at": run_at, "cards": cards, "market": market}
