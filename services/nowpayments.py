from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx


API_BASE_URL = os.getenv("NOWPAYMENTS_API_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")


def _get_api_key() -> str:
    return os.getenv("NOWPAYMENTS_API_KEY", "").strip()


def nowpayments_enabled() -> bool:
    return bool(_get_api_key())


def _headers() -> Dict[str, str]:
    api_key = _get_api_key()
    if not api_key:
        return {}
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


def _err_payload(*, where: str, status_code: Optional[int], detail: str, body: Optional[object] = None) -> dict:
    out: Dict[str, Any] = {"_error": True, "where": where, "detail": detail}
    if status_code is not None:
        out["status_code"] = status_code
    if body is not None:
        out["body"] = body
    return out


async def create_invoice(
    *,
    order_id: str,
    order_description: str,
    price_amount: float,
    price_currency: str = "usd",
    pay_currency: Optional[str] = None,
    ipn_callback_url: Optional[str] = None,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a NOWPayments invoice and return full JSON response, or None on error.
    """
    if not nowpayments_enabled():
        return None

    data: Dict[str, Any] = {
        "price_amount": float(price_amount),
        "price_currency": (price_currency or "usd").lower(),
        "order_id": order_id,
        "order_description": order_description,
    }
    if pay_currency:
        data["pay_currency"] = pay_currency.lower()
    if ipn_callback_url:
        data["ipn_callback_url"] = ipn_callback_url
    if success_url:
        data["success_url"] = success_url
    if cancel_url:
        data["cancel_url"] = cancel_url

    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.post(f"{API_BASE_URL}/invoice", json=data, headers=_headers())
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body: object
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            return _err_payload(
                where="create_invoice",
                status_code=e.response.status_code,
                detail="HTTP error",
                body=body,
            )
        except Exception as e:
            return _err_payload(where="create_invoice", status_code=None, detail=repr(e))


async def get_invoice(invoice_id: str) -> Optional[dict]:
    """
    Try to fetch invoice details. Endpoint support may depend on NOWPayments account/API version.
    Returns JSON response or None.
    """
    if not nowpayments_enabled() or not invoice_id:
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/invoice/{invoice_id}", headers=_headers())
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body: object
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            return _err_payload(
                where="get_invoice",
                status_code=e.response.status_code,
                detail="HTTP error",
                body=body,
            )
        except Exception as e:
            return _err_payload(where="get_invoice", status_code=None, detail=repr(e))


async def create_payment(
    *,
    order_id: str,
    order_description: str,
    price_amount: float,
    price_currency: str = "usd",
    pay_currency: str,
    ipn_callback_url: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a NOWPayments payment (deposit address flow) and return JSON, or None on error.

    This returns fields like payment_id, pay_address, pay_amount, pay_currency, payment_status.
    """
    if not nowpayments_enabled():
        return None

    pay_currency = (pay_currency or "").strip().lower()
    if not pay_currency:
        return None

    data: Dict[str, Any] = {
        "price_amount": float(price_amount),
        "price_currency": (price_currency or "usd").lower(),
        "pay_currency": pay_currency,
        "order_id": order_id,
        "order_description": order_description,
    }
    if ipn_callback_url:
        data["ipn_callback_url"] = ipn_callback_url

    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.post(f"{API_BASE_URL}/payment", json=data, headers=_headers())
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body: object
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            return _err_payload(
                where="create_payment",
                status_code=e.response.status_code,
                detail="HTTP error",
                body=body,
            )
        except Exception as e:
            return _err_payload(where="create_payment", status_code=None, detail=repr(e))


async def get_payment(payment_id: str) -> Optional[dict]:
    """
    Fetch payment status/details for an address-flow payment.
    """
    if not nowpayments_enabled() or not payment_id:
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/payment/{payment_id}", headers=_headers())
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body: object
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            return _err_payload(
                where="get_payment",
                status_code=e.response.status_code,
                detail="HTTP error",
                body=body,
            )
        except Exception as e:
            return _err_payload(where="get_payment", status_code=None, detail=repr(e))

