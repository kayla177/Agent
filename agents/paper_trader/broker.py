"""Alpaca PAPER-trading REST client (simulated money, real market data).

Thin httpx wrapper — no SDK dependency. Everything targets the paper-api base
URL from config, so real money is never touched. Buys use `notional` (dollar
amount, fractional shares); sells use `qty` (share count).
"""

from __future__ import annotations

import httpx

import config


class BrokerError(Exception):
    """Raised when an Alpaca request fails or the client isn't configured."""


def is_configured() -> bool:
    return bool(config.ALPACA_API_KEY_ID and config.ALPACA_API_SECRET_KEY)


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY_ID,
        "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET_KEY,
        "Content-Type": "application/json",
    }


def _get(path: str):
    if not is_configured():
        raise BrokerError("Alpaca keys not set (ALPACA_API_KEY_ID / _SECRET_KEY)")
    url = f"{config.ALPACA_BASE_URL}{path}"
    resp = httpx.get(url, headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_account() -> dict:
    """Return {cash, portfolio_value, buying_power} as floats."""
    a = _get("/v2/account")
    return {
        "cash": float(a.get("cash", 0)),
        "portfolio_value": float(a.get("portfolio_value", 0)),
        "buying_power": float(a.get("buying_power", 0)),
        "status": a.get("status", ""),
    }


def get_positions() -> dict[str, dict]:
    """Map symbol -> {qty, avg_entry, market_value, unrealized_plpc}."""
    out: dict[str, dict] = {}
    for p in _get("/v2/positions"):
        out[p["symbol"]] = {
            "qty": float(p.get("qty", 0)),
            "avg_entry": float(p.get("avg_entry_price", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)) * 100.0,
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
        }
    return out


def submit_order(symbol: str, side: str, *, notional: float = None, qty: float = None) -> dict:
    """Place a market DAY order on the paper account. Returns the order JSON."""
    if not is_configured():
        raise BrokerError("Alpaca keys not set")
    body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day"}
    if notional is not None:
        body["notional"] = round(notional, 2)
    elif qty is not None:
        body["qty"] = str(qty)
    else:
        raise BrokerError("submit_order needs notional or qty")

    url = f"{config.ALPACA_BASE_URL}/v2/orders"
    resp = httpx.post(url, headers=_headers(), json=body, timeout=20)
    if resp.status_code >= 400:
        raise BrokerError(f"order rejected ({resp.status_code}): {resp.text[:200]}")
    return resp.json()
