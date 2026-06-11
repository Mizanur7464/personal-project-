"""
Phase 3: EMA & RSI from Binance klines (technical check).
"""
from __future__ import annotations

from typing import List

from services.binance import binance_get_json


def _format_num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


async def fetch_klines(symbol: str, interval: str = "1h", limit: int = 100) -> List[list]:
    """Binance klines: [open_time, open, high, low, close, volume, ...]."""
    return await binance_get_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )


def ema(prices: List[float], period: int) -> List[float]:
    out: List[float] = []
    k = 2 / (period + 1)
    for i, p in enumerate(prices):
        if i == 0:
            out.append(p)
        else:
            out.append(k * p + (1 - k) * out[-1])
    return out


def rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _normalize_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if not s.endswith("USDT"):
        s = s + "USDT"
    return s


async def get_ema_rsi_summary(symbol: str, interval: str = "1h") -> str:
    """
    Phase 3: Return EMA 9, EMA 21, RSI(14) and short interpretation.
    """
    symbol = _normalize_symbol(symbol)
    try:
        candles = await fetch_klines(symbol, interval=interval, limit=100)
    except Exception:
        return f"Could not fetch data for {symbol}. Check symbol (e.g. BTC or BTCUSDT) and try again."

    if not candles:
        return f"No kline data for {symbol}."

    closes = [float(c[4]) for c in candles]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi_val = rsi(closes, 14)
    last_close = closes[-1]
    last_ema9 = ema9[-1]
    last_ema21 = ema21[-1]

    # Simple crossover / RSI hint
    if last_ema9 > last_ema21:
        trend = "Bullish (EMA9 > EMA21)"
    elif last_ema9 < last_ema21:
        trend = "Bearish (EMA9 < EMA21)"
    else:
        trend = "Neutral"

    if rsi_val >= 70:
        rsi_hint = "Overbought zone (RSI ≥ 70)"
    elif rsi_val <= 30:
        rsi_hint = "Oversold zone (RSI ≤ 30)"
    else:
        rsi_hint = "Neutral RSI"

    lines = [
        f"📐 Technical ({symbol}) — {interval}",
        "",
        f"Price: {_format_num(last_close)}",
        f"EMA 9:  {_format_num(last_ema9)}",
        f"EMA 21: {_format_num(last_ema21)}",
        f"RSI(14): {_format_num(rsi_val)}",
        "",
        f"Trend: {trend}",
        f"RSI: {rsi_hint}",
        "",
        "⚠️ Not financial advice. For education only.",
    ]
    return "\n".join(lines)
