"""Additional bot command handlers."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.backup import backup_database
from core.config import (
    ALERT_LIMIT_ELITE,
    ALERT_LIMIT_PRO,
    WATCHLIST_LIMIT_ELITE,
    WATCHLIST_LIMIT_PRO,
)
from core.db import (
    add_technical_alert,
    add_watchlist_symbol,
    admin_set_user_tier,
    apply_referral_code,
    count_active_alerts,
    count_referrals,
    count_watchlist,
    ensure_referral_code,
    get_admin_stats,
    get_tier,
    get_tier_expires_at,
    get_user,
    list_technical_alerts,
    list_watchlist,
    remove_technical_alert,
    remove_watchlist_symbol,
)
from core.admin import is_admin
from core.config import REFERRAL_REFEREE_BONUS_DAYS, REFERRAL_REFERRER_BONUS_DAYS
from core.notifications import notify_referrer
from core.ratelimit import reply_if_rate_limited
from services.channel_access import (
    channels_configured,
    grant_tier_channel_access,
    on_tier_activated,
    on_tier_revoked,
    revoke_all_paid_channels,
)
from services.news import fetch_crypto_news
from services.watchlist import get_watchlist_summary
from services.weekly_summary import generate_weekly_summary


def _normalize_symbol(sym: str) -> str:
    sym = (sym or "").upper().replace("/", "").replace(" ", "")
    if not sym:
        return ""
    if sym.endswith("USDT"):
        return sym
    return f"{sym}USDT"


def _alert_limit(tier: str) -> int:
    if tier == "elite":
        return ALERT_LIMIT_ELITE
    if tier == "pro":
        return ALERT_LIMIT_PRO
    return 0


def _watchlist_limit(tier: str) -> int:
    if tier == "elite":
        return WATCHLIST_LIMIT_ELITE
    if tier == "pro":
        return WATCHLIST_LIMIT_PRO
    return 0


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    text = await fetch_crypto_news(limit=5)
    await update.effective_message.reply_text(text)


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    tier = get_tier(uid)
    if not is_admin(uid) and tier == "free":
        await update.effective_message.reply_text(
            "Watchlist is for Pro and Elite. Use /upgrade."
        )
        return
    symbols = list_watchlist(uid)
    text = await get_watchlist_summary(symbols)
    await update.effective_message.reply_text(text)


async def watch_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    tier = get_tier(uid)
    if not is_admin(uid) and tier == "free":
        await update.effective_message.reply_text("Watchlist requires Pro or Elite.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /watch_add SYMBOL")
        return
    symbol = _normalize_symbol(args[0])
    limit = _watchlist_limit(tier)
    if not is_admin(uid) and count_watchlist(uid) >= limit:
        await update.effective_message.reply_text(f"Watchlist limit reached ({limit}).")
        return
    added = add_watchlist_symbol(uid, symbol)
    if added:
        await update.effective_message.reply_text(f"✅ Added {symbol} to watchlist.")
    else:
        await update.effective_message.reply_text(f"{symbol} is already on your watchlist.")


async def watch_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /watch_remove SYMBOL")
        return
    symbol = _normalize_symbol(args[0])
    ok = remove_watchlist_symbol(uid, symbol)
    await update.effective_message.reply_text(
        f"✅ Removed {symbol}." if ok else f"{symbol} not on your watchlist."
    )


async def tech_alert_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    tier = get_tier(uid)
    if not is_admin(uid) and tier == "free":
        await update.effective_message.reply_text("Technical alerts require Pro or Elite.")
        return
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /tech_alert SYMBOL CONDITION [interval]\n\n"
            "Conditions: rsi_above, rsi_below, ema_bullish, ema_bearish\n"
            "Example: /tech_alert BTC rsi_above 1h"
        )
        return
    limit = _alert_limit(tier)
    if not is_admin(uid) and count_active_alerts(uid) >= limit:
        await update.effective_message.reply_text(f"Alert limit reached ({limit}).")
        return
    symbol = _normalize_symbol(args[0])
    condition = args[1].lower()
    interval = args[2] if len(args) > 2 else "1h"
    threshold = 70.0 if condition == "rsi_above" else 30.0 if condition == "rsi_below" else None
    try:
        alert_id = add_technical_alert(
            telegram_id=uid,
            symbol=symbol,
            condition_type=condition,
            interval=interval,
            threshold=threshold,
        )
    except ValueError as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(
        f"✅ Technical alert #{alert_id}\n{symbol} {condition} ({interval})"
    )


async def tech_alert_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    args = context.args or []
    if len(args) != 1:
        await update.effective_message.reply_text("Usage: /tech_alert_remove ALERT_ID")
        return
    try:
        alert_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("ALERT_ID must be a number.")
        return
    ok = remove_technical_alert(telegram_id=uid, alert_id=alert_id)
    await update.effective_message.reply_text("✅ Removed." if ok else "Alert not found.")


async def my_tech_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    alerts = list_technical_alerts(uid, only_active=True)
    if not alerts:
        await update.effective_message.reply_text(
            "No technical alerts.\n\n/tech_alert BTC rsi_above 1h"
        )
        return
    lines = ["📐 Active technical alerts:\n"]
    for a in alerts:
        lines.append(
            f"#{a['alert_id']} {a['symbol']} {a['condition_type']} ({a['interval']})"
        )
    lines.append("\nRemove: /tech_alert_remove ALERT_ID")
    await update.effective_message.reply_text("\n".join(lines))


async def weekly_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not is_admin(uid) and get_tier(uid) != "elite":
        await update.effective_message.reply_text("Weekly AI summary is Elite only.")
        return
    await update.effective_message.reply_text("Generating weekly summary...")
    text = await generate_weekly_summary(uid)
    await update.effective_message.reply_text(text)


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    code = ensure_referral_code(uid)
    bot_username = (await context.bot.get_me()).username or "your_bot"
    refs = count_referrals(uid)
    await update.effective_message.reply_text(
        f"🔗 Your referral code: <code>{code}</code>\n"
        f"Share: https://t.me/{bot_username}?start=ref_{code}\n"
        f"Referrals: {refs}\n\n"
        f"Reward: friend gets {REFERRAL_REFEREE_BONUS_DAYS} bonus days, "
        f"you get +{REFERRAL_REFERRER_BONUS_DAYS} days.",
        parse_mode="HTML",
    )


async def use_referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /use_referral CODE")
        return
    ok, msg, referrer_id = apply_referral_code(uid, args[0])
    await update.effective_message.reply_text(msg)
    if ok and referrer_id:
        await notify_referrer(
            context.bot, referrer_id=referrer_id, referee_id=uid
        )
        tier = get_tier(uid)
        if tier in ("pro", "elite"):
            await on_tier_activated(context.bot, uid, tier)


async def my_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resend one-time private channel invite for active Pro/Elite."""
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not channels_configured():
        await update.effective_message.reply_text(
            "Private channels are not configured yet."
        )
        return
    tier = get_tier(uid)
    if is_admin(uid):
        await update.effective_message.reply_text(
            "Admins have full bot access. Channel invites are for paid members."
        )
        return
    if tier not in ("pro", "elite"):
        await update.effective_message.reply_text(
            "Private channel access is for Pro and Elite members.\n"
            "Use /upgrade to subscribe."
        )
        return
    sent = await grant_tier_channel_access(context.bot, uid, tier)
    if sent:
        await update.effective_message.reply_text(
            f"✅ New one-time {tier.upper()} channel link sent to your DM."
        )
    else:
        await update.effective_message.reply_text(
            "Could not create a channel link. Make sure you can receive DMs from the bot."
        )


async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.effective_message.reply_text("Not allowed.")
        return
    stats = get_admin_stats()
    tiers = stats.get("tiers") or {}
    lines = [
        "📊 Admin Stats\n",
        f"Total users: {stats['total_users']}",
        f"Free: {tiers.get('free', 0)} | Pro: {tiers.get('pro', 0)} | Elite: {tiers.get('elite', 0)}",
        f"Active price alerts: {stats['active_price_alerts']}",
        f"Active tech alerts: {stats['active_technical_alerts']}",
        f"Paid orders: {stats['paid_orders']}",
        f"Journal entries: {stats['journal_entries']}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def admin_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.effective_message.reply_text("Not allowed.")
        return
    try:
        path = backup_database()
        await update.effective_message.reply_text(f"✅ Backup saved:\n{path}")
    except Exception as e:
        await update.effective_message.reply_text(f"Backup failed: {e}")


async def admin_set_tier_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.effective_message.reply_text("Not allowed.")
        return
    args = context.args or []
    if len(args) != 2:
        await update.effective_message.reply_text(
            "Usage: /admin_set_tier TELEGRAM_ID free|pro|elite"
        )
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("TELEGRAM_ID must be a number.")
        return
    tier = args[1].lower()
    if tier not in ("free", "pro", "elite"):
        await update.effective_message.reply_text("Tier must be free, pro, or elite.")
        return
    old_user = get_user(target_id)
    old_tier = (old_user.get("tier") or "free").lower() if old_user else "free"
    admin_set_user_tier(target_id, tier)
    if tier == "free":
        await revoke_all_paid_channels(context.bot, target_id)
    else:
        if old_tier == "elite" and tier == "pro":
            await on_tier_revoked(context.bot, target_id, "elite")
        await on_tier_activated(context.bot, target_id, tier)
    await update.effective_message.reply_text(f"✅ User {target_id} tier set to {tier.upper()}.")


def tier_expiry_line(uid: int) -> str:
    exp = get_tier_expires_at(uid)
    if not exp:
        return ""
    return f"\nExpires: {exp[:10]}"
