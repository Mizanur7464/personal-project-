"""NOWPayments IPN webhook HTTP server (runs alongside the Telegram bot)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Optional

from aiohttp import web

from services.payment_flow import process_payment_status

logger = logging.getLogger("bot.webhook")


def _ipn_secret() -> str:
    return os.getenv("NOWPAYMENTS_IPN_SECRET", "").strip()


def verify_ipn_signature(body_bytes: bytes, signature: str) -> bool:
    """Verify NOWPayments IPN HMAC-SHA512 signature."""
    secret = _ipn_secret()
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_nowpayments_ipn(request: web.Request) -> web.Response:
    body = await request.read()
    sig = request.headers.get("x-nowpayments-sig", "")
    if not verify_ipn_signature(body, sig):
        logger.warning("Invalid IPN signature")
        return web.Response(status=403, text="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return web.Response(status=400, text="invalid json")

    order_id = str(payload.get("order_id") or "")
    status = str(payload.get("payment_status") or payload.get("status") or "")
    if not order_id:
        return web.Response(status=400, text="missing order_id")

    bot = request.app.get("telegram_bot")
    await process_payment_status(order_id, status, bot=bot)
    logger.info("IPN processed order_id=%s status=%s", order_id, status)
    return web.Response(status=200, text="ok")


async def start_webhook_server(bot: Any) -> Optional[web.AppRunner]:
    """Start aiohttp server if NOWPAYMENTS_IPN_SECRET is set."""
    if not _ipn_secret():
        logger.info("NOWPAYMENTS_IPN_SECRET not set — webhook server disabled")
        return None

    port = int(os.getenv("WEBHOOK_PORT", "8080"))
    app = web.Application()
    app["telegram_bot"] = bot
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_ipn)
    app.router.add_get("/health", lambda _r: web.Response(text="ok"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("NOWPayments webhook listening on :%s/webhook/nowpayments", port)
    return runner
