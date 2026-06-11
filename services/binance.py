from __future__ import annotations

import os
import time
import httpx
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Cache full ticker list: two heavy calls in a row (Summary + Volatility) share one download.
_ticker_cache: Optional[Tuple[float, List[dict]]] = None
_TICKER_CACHE_TTL_SEC = 45.0

# Official mirrors (if api.binance.com is slow/blocked, next may work)
_DEFAULT_BINANCE_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

BINANCE_BASE_URL = "https://api.binance.com"  # legacy name; real calls use mirrors below


def _candidate_bases() -> List[str]:
    custom = os.getenv("BINANCE_API_BASE_URL", "").strip().rstrip("/")
    if custom:
        out = [custom]
        for b in _DEFAULT_BINANCE_BASES:
            if b not in out:
                out.append(b)
        return out
    return list(_DEFAULT_BINANCE_BASES)


# Longer timeouts: slow ISPs / TLS handshake can exceed 10s
_HTTP_TIMEOUT = httpx.Timeout(connect=25.0, read=60.0, write=25.0, pool=10.0)


async def binance_get_json(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """
    GET Binance public API; try mirror hosts if ConnectTimeout / network errors.
    Set BINANCE_API_BASE_URL in .env to force one host first (e.g. https://api1.binance.com).
    """
    last_exc: Optional[Exception] = None
    for base in _candidate_bases():
        url = f"{base}{path}"
        try:
            print(f"[BINANCE] GET {url}", flush=True)
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            print(f"[BINANCE] OK via {base}", flush=True)
            return data
        except Exception as e:
            last_exc = e
            print(f"[BINANCE] FAIL {base}: {type(e).__name__}: {e}", flush=True)
    assert last_exc is not None
    raise last_exc

# Phase 2: fallback list if top-by-volume fetch fails
TOP_COINS_FALLBACK: List[str] = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
]


async def fetch_24h_ticker(symbol: str) -> dict:
    """Fetch 24h ticker stats for a single symbol from Binance (with mirror fallback)."""
    return await binance_get_json("/api/v3/ticker/24hr", {"symbol": symbol})


async def fetch_price(symbol: str) -> float:
    """
    Fetch latest spot price for a symbol (e.g. BTCUSDT).
    """
    data = await binance_get_json("/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])


def _format_number(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


async def get_simple_market_summary(symbols: Iterable[str]) -> str:
    """
    Return a human-readable summary for a list of symbols.
    """
    results: List[str] = []

    for symbol in symbols:
        data = await fetch_24h_ticker(symbol)

        last_price = float(data["lastPrice"])
        price_change_percent = float(data["priceChangePercent"])
        high_price = float(data["highPrice"])
        low_price = float(data["lowPrice"])

        direction_emoji = "📈" if price_change_percent >= 0 else "📉"

        line = (
            f"{direction_emoji} {symbol}\n"
            f"  Price: {_format_number(last_price, 2)}\n"
            f"  24h Change: {_format_number(price_change_percent, 2)}%\n"
            f"  24h High/Low: {_format_number(high_price, 2)} / {_format_number(low_price, 2)}\n"
        )
        results.append(line)

    header = "📊 Daily Market Summary (Binance Spot)\n\n"
    risk_text = (
        "⚠️ Risk Reminder: Crypto markets are highly volatile. "
        "Always use proper risk management and only trade with money "
        "you can afford to lose."
    )

    body = "\n".join(results)
    return f"{header}{body}\n{risk_text}"


async def fetch_all_tickers_24h() -> List[dict]:
    """Fetch 24h ticker for all symbols (Binance public), with short TTL cache."""
    global _ticker_cache
    now = time.monotonic()
    if _ticker_cache is not None:
        age = now - _ticker_cache[0]
        if age < _TICKER_CACHE_TTL_SEC:
            print(
                f"[BINANCE] Using cached 24h tickers (rows={len(_ticker_cache[1])}, age={age:.1f}s)",
                flush=True,
            )
            return _ticker_cache[1]

    print("[BINANCE] Downloading full /api/v3/ticker/24hr (large payload)...", flush=True)
    t0 = time.perf_counter()
    data = await binance_get_json("/api/v3/ticker/24hr")
    elapsed = time.perf_counter() - t0
    _ticker_cache = (time.monotonic(), data)
    print(f"[BINANCE] Done: {len(data)} symbols in {elapsed:.2f}s", flush=True)
    return data


async def get_top_coins_summary(limit: int = 6) -> str:
    """
    Phase 2: Top coins by 24h quote volume (USDT pairs), then format summary.
    """
    try:
        all_tickers = await fetch_all_tickers_24h()
    except Exception:
        return await get_simple_market_summary(TOP_COINS_FALLBACK[:limit])

    usdt = [t for t in all_tickers if t["symbol"].endswith("USDT")]
    for t in usdt:
        t["_quoteVolume"] = float(t.get("quoteVolume", 0))
    usdt.sort(key=lambda t: t["_quoteVolume"], reverse=True)
    top = usdt[:limit]

    results: List[str] = []
    for t in top:
        last_price = float(t["lastPrice"])
        price_change_percent = float(t["priceChangePercent"])
        high_price = float(t["highPrice"])
        low_price = float(t["lowPrice"])
        direction_emoji = "📈" if price_change_percent >= 0 else "📉"
        line = (
            f"{direction_emoji} {t['symbol']}\n"
            f"  Price: {_format_number(last_price, 2)}\n"
            f"  24h Change: {_format_number(price_change_percent, 2)}%\n"
            f"  24h High/Low: {_format_number(high_price, 2)} / {_format_number(low_price, 2)}\n"
        )
        results.append(line)

    header = "📊 Top Coins Summary (by 24h volume, Binance Spot)\n\n"
    risk_text = (
        "⚠️ Risk Reminder: Crypto markets are highly volatile. "
        "Always use proper risk management and only trade with money "
        "you can afford to lose."
    )
    body = "\n".join(results)
    return f"{header}{body}\n{risk_text}"


async def get_volatility_alert(threshold_percent: float = 5.0) -> str:
    """
    Phase 2: List coins (from top by volume) with 24h move >= threshold %.
    """
    try:
        all_tickers = await fetch_all_tickers_24h()
    except Exception:
        return "Could not fetch market data. Try again later."

    usdt = [t for t in all_tickers if t["symbol"].endswith("USDT")]
    for t in usdt:
        t["_quoteVolume"] = float(t.get("quoteVolume", 0))
    usdt.sort(key=lambda t: t["_quoteVolume"], reverse=True)
    top = usdt[:20]

    alert_lines: List[str] = []
    for t in top:
        pct = float(t["priceChangePercent"])
        if abs(pct) >= threshold_percent:
            emoji = "📈" if pct >= 0 else "📉"
            alert_lines.append(f"{emoji} {t['symbol']}: {_format_number(pct, 2)}% (24h)")

    if not alert_lines:
        return (
            f"🔔 Volatility Alert (24h move ≥ {threshold_percent}%)\n\n"
            f"No top coins moved ≥{threshold_percent}% in the last 24h. "
            f"Market is relatively calm."
        )

    header = (
        f"🔔 Volatility Alert — 24h move ≥ {threshold_percent}%\n"
        "(Top coins by volume)\n\n"
    )
    body = "\n".join(alert_lines)
    footer = "\n\n⚠️ High volatility = higher risk. Use stop loss and position sizing."
    return f"{header}{body}{footer}"

