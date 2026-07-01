"""Deterministic technical indicators computed from a close-price series.

Pure Python (no numpy/pandas) so it stays dependency-free. Every function takes
`closes` as a list of floats ordered OLDEST -> NEWEST and returns plain numbers.
Indicators are descriptive facts, never trading advice — the digest frames them
as information only.
"""

from __future__ import annotations

from typing import Optional


def sma(closes: list[float], n: int) -> Optional[float]:
    """Simple moving average of the last `n` closes, or None if too short."""
    if len(closes) < n or n <= 0:
        return None
    return sum(closes[-n:]) / n


def ema_series(closes: list[float], n: int) -> list[float]:
    """Full EMA series (same length region) — seeded with the first SMA(n)."""
    if len(closes) < n or n <= 0:
        return []
    k = 2 / (n + 1)
    seed = sum(closes[:n]) / n
    out = [seed]
    for price in closes[n:]:
        out.append(price * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    """Wilder's RSI over `n` periods (0-100), or None if too short."""
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    # Wilder smoothing over the remaining points.
    for i in range(n + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(change, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-change, 0.0)) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram) latest values, or (None,)*3."""
    if len(closes) < slow + signal:
        return None, None, None
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    # Align the tails (slow_ema is shorter because it seeds later).
    offset = len(fast_ema) - len(slow_ema)
    macd_line = [f - s for f, s in zip(fast_ema[offset:], slow_ema)]
    if len(macd_line) < signal:
        return None, None, None
    signal_line = ema_series(macd_line, signal)
    if not signal_line:
        return None, None, None
    latest_macd = macd_line[-1]
    latest_signal = signal_line[-1]
    return latest_macd, latest_signal, latest_macd - latest_signal


def pct_from_high(closes: list[float], lookback: int = 252):
    """Return (high, pct_below_high) over the last `lookback` closes."""
    if not closes:
        return None, None
    window = closes[-lookback:]
    high = max(window)
    if high == 0:
        return high, None
    latest = closes[-1]
    return high, (latest - high) / high * 100.0
