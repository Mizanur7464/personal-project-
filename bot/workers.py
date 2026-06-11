"""Background jobs: subscription reminders, expiry notices, weekly summary, backup."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.backup import backup_database
from core.config import RENEWAL_REMINDER_THRESHOLDS
from core.db import (
    downgrade_expired_user,
    get_users_for_expiry_notice,
    get_users_for_renewal_reminder_at_threshold,
    list_elite_user_ids,
    mark_expiry_notice_sent,
    mark_renewal_threshold_sent,
)
from core.notifications import notify_admins
from services.channel_access import on_tier_revoked
from services.weekly_summary import generate_weekly_summary

logger = logging.getLogger("bot.workers")


async def subscription_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send renewal reminders (7/3/1 days) and expiry notifications."""
    bot = context.bot

    for threshold in sorted(RENEWAL_REMINDER_THRESHOLDS, reverse=True):
        for user in get_users_for_renewal_reminder_at_threshold(
            threshold_days=threshold
        ):
            uid = int(user["telegram_id"])
            tier = (user.get("tier") or "pro").upper()
            exp = (user.get("expires_at") or "")[:10]
            days_left = user.get("days_left", threshold)
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=(
                        f"⏳ Your {tier} plan expires on {exp} "
                        f"({days_left} day(s) left).\n\n"
                        "Renew now to keep Pro/Elite features.\n"
                        "Use /upgrade or /pay pro|elite"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("Upgrade", callback_data="pay:pro")]]
                    ),
                )
                mark_renewal_threshold_sent(uid, threshold)
                logger.info(
                    "Renewal reminder (%sd) sent to %s", threshold, uid
                )
            except Exception as e:
                logger.warning(
                    "Renewal reminder failed uid=%s threshold=%s: %s",
                    uid,
                    threshold,
                    e,
                )

    for user in get_users_for_expiry_notice():
        uid = int(user["telegram_id"])
        old_tier = (user.get("tier") or "pro").lower()
        old_tier_label = old_tier.upper()
        try:
            await on_tier_revoked(bot, uid, old_tier)
            downgrade_expired_user(uid)
            await bot.send_message(
                chat_id=uid,
                text=(
                    f"📭 Your {old_tier_label} plan has expired.\n"
                    "You are now on the FREE plan.\n"
                    "You have been removed from the private channel.\n\n"
                    "Renew anytime: /upgrade"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Upgrade", callback_data="pay:pro")]]
                ),
            )
            mark_expiry_notice_sent(uid)
            logger.info("Expiry notice sent to %s", uid)
        except Exception as e:
            logger.warning("Expiry notice failed uid=%s: %s", uid, e)


async def weekly_summary_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Push weekly AI summary to Elite users."""
    bot = context.bot
    for uid in list_elite_user_ids():
        try:
            text = await generate_weekly_summary(uid)
            if "No journal entries" in text:
                continue
            await bot.send_message(chat_id=uid, text=text)
            logger.info("Weekly summary sent to %s", uid)
        except Exception as e:
            logger.warning("Weekly summary failed uid=%s: %s", uid, e)


async def backup_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Automatic SQLite backup."""
    try:
        path = backup_database()
        logger.info("Auto backup saved: %s", path)
        await notify_admins(
            context.bot,
            f"✅ Database backup saved\n{path}",
        )
    except Exception as e:
        logger.error("Auto backup failed: %s", e)
        await notify_admins(context.bot, f"🚨 Database backup failed\n{e}")
