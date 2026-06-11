"""Evaluate technical alert conditions."""
from __future__ import annotations

from services.indicators import ema, fetch_klines, rsi


async def check_technical_condition(
    *,
    symbol: str,
    condition_type: str,
    interval: str = "1h",
    threshold: float | None = None,
) -> tuple[bool, str]:
    """
    Return (triggered, detail_message).
    """
    condition_type = (condition_type or "").lower().strip()
    candles = await fetch_klines(symbol, interval=interval, limit=100)
    if not candles:
        return False, "no data"

    closes = [float(c[4]) for c in candles]
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    rsi_val = rsi(closes, 14)

    if condition_type == "rsi_above":
        thr = threshold if threshold is not None else 70.0
        if rsi_val >= thr:
            return True, f"RSI {rsi_val:.1f} >= {thr}"
        return False, f"RSI {rsi_val:.1f}"
    if condition_type == "rsi_below":
        thr = threshold if threshold is not None else 30.0
        if rsi_val <= thr:
            return True, f"RSI {rsi_val:.1f} <= {thr}"
        return False, f"RSI {rsi_val:.1f}"
    if condition_type == "ema_bullish":
        if ema9 > ema21:
            return True, f"EMA9 {ema9:.2f} > EMA21 {ema21:.2f}"
        return False, f"EMA9 {ema9:.2f} <= EMA21 {ema21:.2f}"
    if condition_type == "ema_bearish":
        if ema9 < ema21:
            return True, f"EMA9 {ema9:.2f} < EMA21 {ema21:.2f}"
        return False, f"EMA9 {ema9:.2f} >= EMA21 {ema21:.2f}"

    return False, "unknown condition"
