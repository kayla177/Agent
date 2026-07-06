"""Append-only audit ledger for placed paper trades (data/paper_trades.json).

A durable local record of every order the agent actually submitted — separate
from Alpaca's own history — so runs are auditable. Atomic writes; reads degrade
gracefully to an empty ledger.
"""

from __future__ import annotations

import datetime as dt
import json

import config

_STORE = config.PROJECT_ROOT / "data" / "paper_trades.json"
_HALT_FILE = config.PROJECT_ROOT / "data" / "TRADING_HALTED"


def is_halted() -> bool:
    """Kill switch: create data/TRADING_HALTED to block all order placement."""
    return _HALT_FILE.exists()


def load_trades() -> list[dict]:
    try:
        with _STORE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("trades", []) if isinstance(data, dict) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def append_trades(entries: list[dict]) -> None:
    if not entries:
        return
    trades = load_trades()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for e in entries:
        e.setdefault("ts", stamp)
    trades.extend(entries)
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"trades": trades}, fh, indent=2)
    tmp.replace(_STORE)
