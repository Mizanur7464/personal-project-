"""Watchlist market summary."""
from __future__ import annotations

from services.binance import get_simple_market_summary


async def get_watchlist_summary(symbols: list[str]) -> str:
    if not symbols:
        return (
            "📋 Your watchlist is empty.\n\n"
            "Add coins:\n"
            "/watch_add BTC\n"
            "/watch_add ETH"
        )
    return await get_simple_market_summary(symbols)
