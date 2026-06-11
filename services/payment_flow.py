"""Shared payment confirmation and tier activation."""
from __future__ import annotations

import logging
from typing import Any, Optional

from core.config import PAID_STATUSES
from core.db import (
    activate_paid_tier,
    get_payment_order_by_order_id,
    get_tier,
    update_payment_order,
)
from core.notifications import notify_payment_confirmed
from services.channel_access import on_tier_activated

logger = logging.getLogger("bot.payments")


def is_paid_status(status: str) -> bool:
    return (status or "").lower().strip() in PAID_STATUSES


async def process_payment_status(
    order_id: str,
    status: str,
    *,
    bot: Any = None,
) -> bool:
    """
    Update order status; activate tier when paid.
    Returns True if payment was confirmed and tier upgraded.
    """
    order = get_payment_order_by_order_id(order_id)
    if not order:
        logger.warning("Unknown order_id=%s", order_id)
        return False

    status = (status or "").lower().strip()
    update_payment_order(order_id=order_id, payment_status=status or "unknown")

    if not is_paid_status(status):
        return False

    plan = (order.get("plan") or "pro").lower()
    if plan not in ("pro", "elite"):
        plan = "pro"

    uid = int(order["telegram_id"])
    activate_paid_tier(uid, plan)

    if bot is not None:
        try:
            await bot.send_message(
                chat_id=uid,
                text=(
                    f"✅ Payment confirmed!\n"
                    f"Your plan is now {get_tier(uid).upper()}.\n"
                    f"Order: {order_id}"
                ),
            )
        except Exception as e:
            logger.error("Failed to notify user %s: %s", uid, e)
        try:
            await notify_payment_confirmed(
                bot,
                telegram_id=uid,
                plan=plan,
                order_id=order_id,
                amount_usd=order.get("amount_usd"),
            )
        except Exception as e:
            logger.error("Failed to notify admins: %s", e)
        try:
            await on_tier_activated(bot, uid, plan)
        except Exception as e:
            logger.error("Channel invite failed user=%s: %s", uid, e)

    return True
