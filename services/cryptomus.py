from __future__ import annotations

"""
Cryptomus payment helper for plan upgrades.
"""

import base64
import hashlib
import os
from typing import Optional, Dict, Any

import httpx


API_BASE_URL = os.getenv("CRYPTOMUS_API_BASE_URL", "https://api.cryptomus.com/v1")


def _get_creds() -> Optional[tuple[str, str]]:
    merchant = os.getenv("CRYPTOMUS_MERCHANT_UUID", "").strip()
    api_key = os.getenv("CRYPTOMUS_API_KEY", "").strip()
    if not merchant or not api_key:
        return None
    return merchant, api_key


def cryptomus_enabled() -> bool:
    return _get_creds() is not None


def _make_sign(data: Dict[str, Any], api_key: str) -> str:
    """
    Sign according to Cryptomus docs:
    md5( base64_encode(json_body) + api_key )
    """
    import json

    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
    return hashlib.md5((b64 + api_key).encode("utf-8")).hexdigest()


async def create_payment_invoice(
    plan: str,
    amount_usd: float,
    telegram_id: int,
) -> Optional[str]:
    """
    Create a Cryptomus payment invoice and return the redirect URL, or None on error.
    """
    creds = _get_creds()
    if creds is None:
        return None
    merchant, api_key = creds

    order_id = f"{plan}-{telegram_id}"

    data: Dict[str, Any] = {
        "amount": f"{amount_usd:.2f}",
        "currency": "USDT",
        "order_id": order_id,
    }

    url_return = os.getenv("CRYPTOMUS_RETURN_URL", "").strip()
    url_success = os.getenv("CRYPTOMUS_SUCCESS_URL", "").strip()
    url_callback = os.getenv("CRYPTOMUS_CALLBACK_URL", "").strip()

    if url_return:
        data["url_return"] = url_return
    if url_success:
        data["url_success"] = url_success
    if url_callback:
        data["url_callback"] = url_callback

    sign = _make_sign(data, api_key)

    headers = {
        "merchant": merchant,
        "sign": sign,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(f"{API_BASE_URL}/payment", json=data, headers=headers)
            resp.raise_for_status()
        except Exception:
            return None

    try:
        payload = resp.json()
    except Exception:
        return None

    # According to docs, result.url contains redirect link
    result = payload.get("result") or {}
    return result.get("url")

