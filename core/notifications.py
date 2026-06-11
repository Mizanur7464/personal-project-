"""Admin and user notifications via Telegram."""
from __future__ import annotations

import logging
from typing import Any, Optional

from core.admin import admin_telegram_ids
from core.config import REFERRAL_REFERRER_BONUS_DAYS

logger = logging.getLogger("bot.notifications")


async def notify_admins(bot: Any, text: str) -> None:
    """Send a message to all configured admins."""
    for admin_id in admin_telegram_ids():
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning("Admin notify failed id=%s: %s", admin_id, e)


async def notify_payment_confirmed(
    bot: Any,
    *,
    telegram_id: int,
    plan: str,
    order_id: str,
    amount_usd: Optional[float] = None,
) -> None:
    amount_line = f"\nAmount: ${amount_usd:.2f}" if amount_usd is not None else ""
    await notify_admins(
        bot,
        "💰 New payment confirmed\n"
        f"User: {telegram_id}\n"
        f"Plan: {plan.upper()}\n"
        f"Order: {order_id}"
        + amount_line,
    )


async def notify_admin_error(bot: Any, error: BaseException, *, context: str = "") -> None:
    prefix = f"[{context}] " if context else ""
    await notify_admins(
        bot,
        f"🚨 Bot error\n{prefix}{type(error).__name__}: {error}",
    )


async def notify_referrer(
    bot: Any,
    *,
    referrer_id: int,
    referee_id: int,
    bonus_days: int = REFERRAL_REFERRER_BONUS_DAYS,
) -> None:
    try:
        await bot.send_message(
            chat_id=referrer_id,
            text=(
                "🎉 Referral reward!\n\n"
                f"User {referee_id} used your referral code.\n"
                f"You received +{bonus_days} days on your plan."
            ),
        )
    except Exception as e:
        logger.warning("Referrer notify failed id=%s: %s", referrer_id, e)
