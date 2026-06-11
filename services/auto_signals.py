"""Automated rule-based market signals (educational, not trade advice)."""
from __future__ import annotations

import os
from typing import List, Optional

import httpx

from core.config import SIGNAL_COINS, SIGNAL_INTERVAL, VOLATILITY_THRESHOLD
from services.ai_journal import ai_enabled
from services.binance import get_top_coins_summary, get_volatility_alert
from services.indicators import ema, fetch_klines, rsi
from services.news import fetch_crypto_news


DISCLAIMER = "\n\n⚠️ Educational only. Not financial advice. DYOR."


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip().replace("/", "")
    if not s.endswith("USDT"):
        s = f"{s}USDT"
    return s


async def _analyze_coin(symbol: str, interval: str) -> Optional[dict]:
    symbol = _normalize_symbol(symbol)
    try:
        candles = await fetch_klines(symbol, interval=interval, limit=100)
    except Exception:
        return None
    if len(candles) < 22:
        return None

    closes = [float(c[4]) for c in candles]
    ema9_series = ema(closes, 9)
    ema21_series = ema(closes, 21)
    rsi_val = rsi(closes, 14)
    price = closes[-1]
    ema9 = ema9_series[-1]
    ema21 = ema21_series[-1]
    prev_ema9 = ema9_series[-2]
    prev_ema21 = ema21_series[-2]

    tags: list[str] = []
    if rsi_val <= 30:
        tags.append(f"RSI oversold ({rsi_val:.1f})")
    elif rsi_val >= 70:
        tags.append(f"RSI overbought ({rsi_val:.1f})")

    if prev_ema9 <= prev_ema21 and ema9 > ema21:
        tags.append("EMA9 crossed above EMA21 (bullish)")
    elif prev_ema9 >= prev_ema21 and ema9 < ema21:
        tags.append("EMA9 crossed below EMA21 (bearish)")
    elif ema9 > ema21:
        tags.append("EMA structure bullish")
    elif ema9 < ema21:
        tags.append("EMA structure bearish")

    if not tags:
        return None

    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi_val,
        "tags": tags,
    }


async def build_rule_based_signals(
    *,
    coins: Optional[List[str]] = None,
    interval: Optional[str] = None,
) -> str:
    """Scan coins and return actionable-style educational alerts."""
    coins = coins or list(SIGNAL_COINS)
    interval = interval or SIGNAL_INTERVAL
    hits: list[str] = []

    for raw in coins:
        data = await _analyze_coin(raw, interval)
        if not data:
            continue
        tag_text = " | ".join(data["tags"])
        hits.append(
            f"• {data['symbol']} @ {data['price']:.4f}\n  → {tag_text}"
        )

    header = f"🚨 Auto Signal Scan ({interval})\n"
    if not hits:
        body = "No strong RSI/EMA setups on the watchlist right now.\nMarket is relatively quiet."
    else:
        body = "\n".join(hits)
    return header + body + DISCLAIMER


async def build_free_market_update(mode: str) -> str:
    """Rotating free-tier channel content."""
    mode = (mode or "summary").lower()
    if mode == "volatility":
        return await get_volatility_alert(threshold_percent=VOLATILITY_THRESHOLD)
    if mode == "news":
        return await fetch_crypto_news(limit=5)
    return await get_top_coins_summary(limit=8)


async def build_pro_channel_post() -> str:
    """Pro channel: rule signals + compact top-coin snapshot."""
    signals = await build_rule_based_signals()
    summary = await get_top_coins_summary(limit=5)
    return (
        "⭐ PRO Update\n\n"
        "━━━ Signals ━━━\n"
        f"{signals}\n\n"
        "━━━ Market ━━━\n"
        f"{summary}"
    )


async def build_elite_ai_brief() -> Optional[str]:
    """Short AI market brief for Elite channel."""
    if not ai_enabled():
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    market = await get_top_coins_summary(limit=6)
    signals = await build_rule_based_signals()

    prompt = (
        "You are a crypto market analyst. Write a concise briefing (max 12 lines).\n"
        "Cover: market mood, notable movers, risk notes, 2-3 watch items.\n"
        "No title, no heading, no 'End of Brief' line — start directly with the analysis.\n"
        "No direct buy/sell commands. No price targets. Educational tone.\n\n"
        f"Market data:\n{market}\n\nSignals:\n{signals}"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": prompt}

    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

    for item in data.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") == "output_text" and c.get("text"):
                return _clean_elite_brief(str(c["text"]).strip())
    return None


def _clean_elite_brief(text: str) -> str:
    """Strip common AI headings the user does not want in channel posts."""
    lines = text.splitlines()
    while lines:
        head = lines[0].strip().lower().rstrip(":")
        if head in ("elite crypto briefing", "elite briefing", "crypto briefing"):
            lines.pop(0)
            continue
        break
    cleaned = "\n".join(lines).strip()
    for suffix in ("—end of brief—", "-end of brief-", "end of brief"):
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip(" -—")
    return cleaned.strip()


async def build_elite_channel_post() -> str:
    """Elite channel: Pro content + optional AI brief."""
    pro_block = await build_pro_channel_post()
    ai = await build_elite_ai_brief()
    if not ai:
        return "👑 ELITE Update\n\n" + pro_block
    return (
        "👑 ELITE Update\n\n"
        f"{ai}\n\n"
        "━━━ Signals & Market ━━━\n"
        f"{pro_block}"
    )
