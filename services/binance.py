from __future__ import annotations

import asyncio
import os
import time
import httpx
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Cache full ticker list: two heavy calls in a row (Summary + Volatility) share one download.
_ticker_cache: Optional[Tuple[float, List[dict]]] = None
_TICKER_CACHE_TTL_SEC = 45.0

# Shared market rows (CoinGecko / Binance) — avoids repeat calls & rate limits.
_market_rows_cache: Optional[Tuple[float, List[dict]]] = None
_coingecko_cache: Optional[Tuple[float, List[dict]]] = None
_MARKET_CACHE_TTL_SEC = float(os.getenv("MARKET_CACHE_TTL_SEC", "300"))
_COINGECKO_HEADERS = {
    "User-Agent": os.getenv("MARKET_DATA_USER_AGENT", "CryptoSignalBot/1.0"),
    "Accept": "application/json",
}
_binance_semaphore = asyncio.Semaphore(max(1, int(os.getenv("BINANCE_MAX_PARALLEL", "3"))))

# Official mirrors (if api.binance.com is slow/blocked, next may work)
_DEFAULT_BINANCE_BASES = [
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.com",
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
    async with _binance_semaphore:
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

MAJOR_COINS_USDT: List[str] = TOP_COINS_FALLBACK + [
    "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "TRXUSDT",
    "UNIUSDT", "ATOMUSDT", "APTUSDT", "ARBUSDT",
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


def _rows_from_binance_tickers(tickers: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for t in tickers:
        rows.append(
            {
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "change_pct": float(t["priceChangePercent"]),
                "high": float(t["highPrice"]),
                "low": float(t["lowPrice"]),
                "source": "binance",
            }
        )
    return rows


def _rows_from_coingecko(coins: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for coin in coins:
        price = float(coin.get("current_price") or 0)
        rows.append(
            {
                "symbol": str(coin.get("symbol", "?")).upper() + "USDT",
                "price": price,
                "change_pct": float(coin.get("price_change_percentage_24h") or 0),
                "high": float(coin.get("high_24h") or price),
                "low": float(coin.get("low_24h") or price),
                "source": "coingecko",
            }
        )
    return rows


def _format_market_rows(rows: List[dict], limit: int) -> str:
    if not rows:
        return "Could not fetch market data. Try again later."

    source = rows[0].get("source", "binance")
    if source == "coingecko":
        header = "📊 Top Coins Summary (CoinGecko)\n\n"
    elif source == "binance_volume":
        header = "📊 Top Coins Summary (by 24h volume, Binance Spot)\n\n"
    else:
        header = "📊 Top Coins Summary (Binance Spot)\n\n"

    results: List[str] = []
    for row in rows[:limit]:
        pct = float(row["change_pct"])
        direction_emoji = "📈" if pct >= 0 else "📉"
        sym = row["symbol"]
        results.append(
            f"{direction_emoji} {sym}\n"
            f"  Price: {_format_number(float(row['price']), 2)}\n"
            f"  24h Change: {_format_number(pct, 2)}%\n"
            f"  24h High/Low: {_format_number(float(row['high']), 2)} / {_format_number(float(row['low']), 2)}\n"
        )

    risk_text = (
        "⚠️ Risk Reminder: Crypto markets are highly volatile. "
        "Always use proper risk management and only trade with money "
        "you can afford to lose."
    )
    return f"{header}" + "\n".join(results) + f"\n{risk_text}"


async def _fetch_market_rows() -> List[dict]:
    """One cached fetch for summaries & volatility (CoinGecko first — fewer blocks on Railway)."""
    global _market_rows_cache
    now = time.monotonic()
    if _market_rows_cache is not None:
        age = now - _market_rows_cache[0]
        if age < _MARKET_CACHE_TTL_SEC:
            print(f"[MARKET] Using cached rows (age={age:.0f}s)", flush=True)
            return _market_rows_cache[1]

    try:
        cg = await _coingecko_markets(20)
        if cg:
            rows = _rows_from_coingecko(cg)
            _market_rows_cache = (time.monotonic(), rows)
            print(f"[MARKET] OK via CoinGecko ({len(rows)} rows)", flush=True)
            return rows
    except Exception as e:
        print(f"[COINGECKO] market rows failed: {type(e).__name__}: {e}", flush=True)

    tickers = await _fetch_tickers_parallel(TOP_COINS_FALLBACK + MAJOR_COINS_USDT[6:12])
    if tickers:
        rows = _rows_from_binance_tickers(tickers)
        _market_rows_cache = (time.monotonic(), rows)
        print(f"[MARKET] OK via Binance parallel ({len(rows)} rows)", flush=True)
        return rows

    try:
        all_tickers = await fetch_all_tickers_24h()
        usdt = [t for t in all_tickers if t["symbol"].endswith("USDT")]
        for t in usdt:
            t["_quoteVolume"] = float(t.get("quoteVolume", 0))
        usdt.sort(key=lambda t: t["_quoteVolume"], reverse=True)
        rows = _rows_from_binance_tickers(usdt[:20])
        for row in rows:
            row["source"] = "binance_volume"
        _market_rows_cache = (time.monotonic(), rows)
        print(f"[MARKET] OK via Binance full ticker list", flush=True)
        return rows
    except Exception as e:
        print(f"[BINANCE] market rows failed: {type(e).__name__}: {e}", flush=True)

    return []


async def get_top_coins_summary(limit: int = 6) -> str:
    """Top coins summary with 5-min cache and CoinGecko-first to avoid API blocks."""
    rows = await _fetch_market_rows()
    return _format_market_rows(rows, limit)


async def _fetch_tickers_parallel(symbols: Iterable[str]) -> List[dict]:
    """Fetch multiple Binance 24h tickers in parallel."""

    async def _one(symbol: str) -> Optional[dict]:
        try:
            return await fetch_24h_ticker(symbol)
        except Exception:
            return None

    results = await asyncio.gather(*(_one(s) for s in symbols))
    return [r for r in results if r is not None]


async def _coingecko_markets(per_page: int = 20) -> List[dict]:
    """Top coins by market cap from CoinGecko (cached; retries on rate limit)."""
    global _coingecko_cache
    now = time.monotonic()
    if _coingecko_cache is not None:
        age = now - _coingecko_cache[0]
        if age < _MARKET_CACHE_TTL_SEC:
            return _coingecko_cache[1][:per_page]

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers=_COINGECKO_HEADERS,
            ) as client:
                response = await client.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": max(per_page, 20),
                        "page": 1,
                        "sparkline": "false",
                        "price_change_percentage": "24h",
                    },
                )
                if response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[COINGECKO] rate limited, retry in {wait}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                data = response.json()
                _coingecko_cache = (time.monotonic(), data)
                return data[:per_page]
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc


def _coingecko_volatility_alert(coins: List[dict], threshold_percent: float) -> str:
    alert_lines: List[str] = []
    for coin in coins:
        pct = coin.get("price_change_percentage_24h")
        if pct is None:
            continue
        pct = float(pct)
        if abs(pct) >= threshold_percent:
            emoji = "📈" if pct >= 0 else "📉"
            sym = str(coin.get("symbol", "?")).upper()
            alert_lines.append(f"{emoji} {sym}USDT: {_format_number(pct, 2)}% (24h)")

    if not alert_lines:
        return (
            f"🔔 Volatility Alert (24h move ≥ {threshold_percent}%)\n\n"
            f"No top coins moved ≥{threshold_percent}% in the last 24h. "
            f"Market is relatively calm."
        )

    header = (
        f"🔔 Volatility Alert — 24h move ≥ {threshold_percent}%\n"
        "(Top coins by market cap)\n\n"
    )
    body = "\n".join(alert_lines)
    footer = "\n\n⚠️ High volatility = higher risk. Use stop loss and position sizing."
    return f"{header}{body}{footer}"


def _format_volatility_alert(tickers: List[dict], threshold_percent: float) -> str:
    alert_lines: List[str] = []
    for t in tickers:
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


def _volatility_from_rows(rows: List[dict], threshold_percent: float) -> str:
    tickers = [
        {"symbol": r["symbol"], "priceChangePercent": r["change_pct"]}
        for r in rows
    ]
    if rows and rows[0].get("source") == "coingecko":
        return _coingecko_volatility_alert(
            [
                {
                    "symbol": r["symbol"].replace("USDT", "").lower(),
                    "price_change_percentage_24h": r["change_pct"],
                }
                for r in rows
            ],
            threshold_percent,
        )
    return _format_volatility_alert(tickers, threshold_percent)


async def get_volatility_alert(threshold_percent: float = 5.0) -> str:
    """Volatility alert using shared cached market rows (fewer API calls)."""
    rows = await _fetch_market_rows()
    if rows:
        return _volatility_from_rows(rows, threshold_percent)
    return "Could not fetch market data. Try again later."

