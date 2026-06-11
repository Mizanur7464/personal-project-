import logging
import os
import sys
import time
import traceback
from datetime import time as dt_time, timezone
from typing import Optional

# Allow running as `python main.py` from the `bot/` folder (parent = project root).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.admin import is_admin
from core.config import (
    ALERT_LIMIT_ELITE,
    ALERT_LIMIT_PRO,
    AUTO_BROADCAST_ENABLED,
    DB_BACKUP_HOURS,
    ELITE_BROADCAST_HOURS,
    FREE_BROADCAST_HOURS,
    PRO_BROADCAST_HOURS,
    SUBSCRIPTION_CHECK_HOURS,
    WEEKLY_SUMMARY_DAY,
    WEEKLY_SUMMARY_HOUR,
)
from core.ratelimit import reply_if_rate_limited
from core.db import create_or_update_user, get_tier
from core.db import create_payment_order, get_latest_payment_order, update_payment_order
from core.db import apply_referral_code, count_active_alerts
from core.db import (
    add_price_alert,
    list_price_alerts,
    list_technical_alerts,
    remove_price_alert,
    get_active_price_alerts,
    get_active_technical_alerts,
    deactivate_price_alert,
    deactivate_technical_alert,
)
from core.logging_setup import setup_logging
from core.db import (
    add_trade_journal_entry,
    list_trade_journal_entries,
    get_trade_journal_entry,
    delete_trade_journal_entry,
    update_trade_journal_ai_feedback,
)
from services.ai_journal import ai_enabled, generate_trade_feedback
from services.binance import get_top_coins_summary, get_volatility_alert, fetch_price
from services.indicators import get_ema_rsi_summary
from services.cryptomus import create_payment_invoice, cryptomus_enabled
from services.nowpayments import (
    create_invoice,
    get_invoice,
    create_payment,
    get_payment,
    nowpayments_enabled,
)
from services.payment_flow import is_paid_status, process_payment_status
from services.technical_alerts import check_technical_condition
from bot.extra_handlers import (
    admin_backup_command,
    admin_set_tier_command,
    admin_stats_command,
    news_command,
    referral_command,
    tier_expiry_line,
    use_referral_command,
    watch_add_command,
    watch_remove_command,
    watchlist_command,
    weekly_summary_command,
    my_channel_command,
    my_tech_alerts_command,
    tech_alert_add_command,
    tech_alert_remove_command,
)
from bot.broadcast_workers import (
    elite_channel_broadcast_worker,
    free_channel_broadcast_worker,
    pro_channel_broadcast_worker,
)
from bot.workers import backup_worker, subscription_worker, weekly_summary_worker
from core.notifications import notify_admin_error, notify_referrer
from services.channel_access import on_tier_activated
from webhooks.ipn_server import start_webhook_server

logger = logging.getLogger("bot")


RISK_REMINDER_TEXT = (
    "⚠️ Risk Reminder (Educational)\n\n"
    "• Use at most 1–2% risk per trade.\n"
    "• Always set a stop loss before entering.\n"
    "• Avoid revenge trading after a loss.\n"
    "• Do not over-leverage; high leverage = high risk.\n"
    "• This is not financial advice. Trade at your own risk."
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Daily Summary"), KeyboardButton(text="Volatility Alert")],
            [KeyboardButton(text="Watchlist"), KeyboardButton(text="Crypto News")],
            [KeyboardButton(text="Risk Reminder"), KeyboardButton(text="My Alerts")],
            [KeyboardButton(text="AI Trade Journal"), KeyboardButton(text="Weekly Summary")],
            [KeyboardButton(text="Upgrade Plan")],
            [KeyboardButton(text="Buy PRO"), KeyboardButton(text="Buy ELITE")],
            [KeyboardButton(text="Check Payment")],
        ],
        resize_keyboard=True,
    )


def _log(msg: str) -> None:
    logger.info(msg)


def _alert_limit_for_tier(tier: str) -> int:
    if tier == "elite":
        return ALERT_LIMIT_ELITE
    if tier == "pro":
        return ALERT_LIMIT_PRO
    return 0


def _normalize_symbol(sym: str) -> str:
    sym = (sym or "").upper().replace("/", "").replace(" ", "")
    if not sym:
        return ""
    if sym.endswith("USDT"):
        return sym
    return f"{sym}USDT"


async def my_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    price_alerts = list_price_alerts(uid, only_active=True)
    tech_alerts = list_technical_alerts(uid, only_active=True)
    if not price_alerts and not tech_alerts:
        await update.effective_message.reply_text(
            "🔔 You have no active alerts.\n\n"
            "Price: /add_alert BTC 65000 above\n"
            "Technical: /tech_alert BTC rsi_above 1h"
        )
        return

    lines = ["🔔 Your active alerts:\n"]
    if price_alerts:
        lines.append("Price alerts:")
        for a in price_alerts[:20]:
            lines.append(
                f"#{a['alert_id']} {a['symbol']} {a['direction'].upper()} {a['target_price']}"
            )
    if tech_alerts:
        lines.append("\nTechnical alerts:")
        for a in tech_alerts[:20]:
            lines.append(
                f"#{a['alert_id']} {a['symbol']} {a['condition_type']} ({a['interval']})"
            )
    lines.append("\nRemove price: /remove_alert ID")
    lines.append("Remove technical: /tech_alert_remove ID")
    await update.effective_message.reply_text("\n".join(lines))


async def add_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/add_alert SYMBOL PRICE [above|below]\n\n"
            "Examples:\n"
            "/add_alert BTC 65000 above\n"
            "/add_alert ETH 3000 below"
        )
        return

    symbol = _normalize_symbol(args[0])
    if not symbol:
        await update.effective_message.reply_text("Invalid SYMBOL. Example: BTC or BTCUSDT")
        return
    try:
        target = float(args[1])
    except Exception:
        await update.effective_message.reply_text("Invalid PRICE. Use a number, e.g. 65000")
        return
    direction = (args[2].lower().strip() if len(args) >= 3 else "above")
    if direction not in ("above", "below"):
        await update.effective_message.reply_text("Direction must be 'above' or 'below'.")
        return

    tier = get_tier(uid)
    limit = _alert_limit_for_tier(tier)
    if not is_admin(uid) and count_active_alerts(uid) >= limit:
        await update.effective_message.reply_text(f"Alert limit reached ({limit}). Use /upgrade.")
        return

    alert_id = add_price_alert(
        telegram_id=uid,
        symbol=symbol,
        direction=direction,
        target_price=target,
    )
    await update.effective_message.reply_text(
        f"✅ Alert created: #{alert_id}\n"
        f"{symbol} {direction.upper()} {target}\n\n"
        "View: /my_alerts"
    )


async def remove_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    args = context.args or []
    if len(args) != 1:
        await update.effective_message.reply_text("Usage: /remove_alert ALERT_ID")
        return
    try:
        alert_id = int(args[0])
    except Exception:
        await update.effective_message.reply_text("ALERT_ID must be a number.")
        return
    ok = remove_price_alert(telegram_id=uid, alert_id=alert_id)
    await update.effective_message.reply_text("✅ Alert removed." if ok else "Alert not found.")


def _journal_allowed(uid: int) -> bool:
    return is_admin(uid) or get_tier(uid) == "elite"


async def journal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not _journal_allowed(uid):
        await update.effective_message.reply_text("AI Trade Journal is for Elite only. Use /upgrade.")
        return

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Add entry", callback_data="journal:add")],
            [InlineKeyboardButton("My entries", callback_data="journal:list")],
            [InlineKeyboardButton("Home", callback_data="home")],
        ]
    )
    await update.effective_message.reply_text(
        "📒 AI Trade Journal\n\n"
        "Add a trade note and (optionally) get AI feedback.\n"
        + ("✅ AI feedback: enabled" if ai_enabled() else "ℹ️ AI feedback: disabled (set OPENAI_API_KEY to enable)"),
        reply_markup=kb,
    )


async def journal_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not _journal_allowed(uid):
        await update.effective_message.reply_text("AI Trade Journal is for Elite only. Use /upgrade.")
        return

    raw = update.effective_message.text or ""
    # Expected: /journal_add <free text...>
    parts = raw.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text(
            "Usage:\n"
            "/journal_add SYMBOL SIDE | notes...\n\n"
            "Example:\n"
            "/journal_add BTCUSDT long | Entered on breakout, SL below swing, partial at 1R"
        )
        return

    rest = parts[1].strip()
    meta, notes = (rest.split("|", 1) + [""])[:2]
    meta = meta.strip()
    notes = notes.strip() or meta  # allow notes-only

    symbol = None
    side = None
    if "|" in rest:
        tokens = meta.split()
        if tokens:
            symbol = tokens[0].upper()
        if len(tokens) >= 2:
            side = tokens[1].lower()

    entry_id = add_trade_journal_entry(
        telegram_id=uid,
        symbol=symbol,
        side=side,
        notes=notes,
    )

    await update.effective_message.reply_text(
        f"✅ Journal entry saved (#{entry_id}).\n"
        + ("Generating AI feedback..." if ai_enabled() else "Tip: set OPENAI_API_KEY to enable AI feedback.")
    )

    if ai_enabled():
        fb = await generate_trade_feedback(notes=notes, symbol=symbol, side=side)
        if fb:
            update_trade_journal_ai_feedback(uid, entry_id, fb)
            await update.effective_message.reply_text(
                f"🧠 AI feedback for entry #{entry_id}:\n\n{fb}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("My entries", callback_data="journal:list")],
                        [InlineKeyboardButton("Home", callback_data="home")],
                    ]
                ),
            )


async def journal_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not _journal_allowed(uid):
        await update.effective_message.reply_text("AI Trade Journal is for Elite only. Use /upgrade.")
        return

    items = list_trade_journal_entries(uid, limit=10)
    if not items:
        await update.effective_message.reply_text("No journal entries yet. Use /journal_add")
        return
    lines = ["📒 Your last 10 entries:\n"]
    for it in items:
        s = it.get("symbol") or "-"
        side = (it.get("side") or "-").upper()
        created = it.get("created_at") or ""
        lines.append(f"#{it['entry_id']} {s} {side}  ({created[:10]})")
    lines.append("\nView: /journal_view ENTRY_ID\nDelete: /journal_delete ENTRY_ID")
    await update.effective_message.reply_text("\n".join(lines))


async def journal_view_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not _journal_allowed(uid):
        await update.effective_message.reply_text("AI Trade Journal is for Elite only. Use /upgrade.")
        return

    args = context.args or []
    if len(args) != 1:
        await update.effective_message.reply_text("Usage: /journal_view ENTRY_ID")
        return
    try:
        entry_id = int(args[0])
    except Exception:
        await update.effective_message.reply_text("ENTRY_ID must be a number.")
        return
    it = get_trade_journal_entry(uid, entry_id)
    if not it:
        await update.effective_message.reply_text("Entry not found.")
        return
    text = (
        f"📒 Entry #{it['entry_id']}\n"
        f"Symbol: {it.get('symbol') or '-'}\n"
        f"Side: {(it.get('side') or '-').upper()}\n"
        f"Created: {it.get('created_at')}\n\n"
        f"Notes:\n{it.get('notes') or '-'}\n"
    )
    if it.get("ai_feedback"):
        text += f"\n🧠 AI feedback:\n{it['ai_feedback']}\n"
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Delete", callback_data=f"journal:delete:{entry_id}")],
                [InlineKeyboardButton("Home", callback_data="home")],
            ]
        ),
    )


async def journal_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if not _journal_allowed(uid):
        await update.effective_message.reply_text("AI Trade Journal is for Elite only. Use /upgrade.")
        return
    args = context.args or []
    if len(args) != 1:
        await update.effective_message.reply_text("Usage: /journal_delete ENTRY_ID")
        return
    try:
        entry_id = int(args[0])
    except Exception:
        await update.effective_message.reply_text("ENTRY_ID must be a number.")
        return
    ok = delete_trade_journal_entry(uid, entry_id)
    await update.effective_message.reply_text("✅ Deleted." if ok else "Entry not found.")


async def technical_alerts_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        alerts = get_active_technical_alerts()
    except Exception as e:
        _log(f"technical_alerts_worker: DB error: {e!r}")
        return
    if not alerts:
        return

    for a in alerts:
        try:
            triggered, detail = await check_technical_condition(
                symbol=a["symbol"],
                condition_type=a["condition_type"],
                interval=a.get("interval") or "1h",
                threshold=a.get("threshold"),
            )
            if not triggered:
                continue
            deactivate_technical_alert(alert_id=int(a["alert_id"]))
            await context.bot.send_message(
                chat_id=int(a["telegram_id"]),
                text=(
                    "📐 Technical Alert Triggered!\n\n"
                    f"{a['symbol']} — {a['condition_type']}\n"
                    f"{detail}\n"
                    f"Alert ID: #{a['alert_id']}"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Home", callback_data="home")]]
                ),
            )
        except Exception as e:
            _log(f"technical_alerts_worker: failed alert_id={a.get('alert_id')}: {e!r}")


async def alerts_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodically checks active alerts and notifies users when triggered.
    """
    try:
        alerts = get_active_price_alerts()
    except Exception as e:
        _log(f"alerts_worker: DB error: {e!r}")
        return
    if not alerts:
        return

    # Group by symbol to reduce API calls
    symbols: dict[str, list[dict]] = {}
    for a in alerts:
        symbols.setdefault(a["symbol"], []).append(a)

    for symbol, items in symbols.items():
        try:
            price = await fetch_price(symbol)
        except Exception as e:
            _log(f"alerts_worker: price fetch failed {symbol}: {e!r}")
            continue

        for a in items:
            try:
                direction = (a.get("direction") or "").lower().strip()
                target = float(a.get("target_price") or 0)
                hit = (price >= target) if direction == "above" else (price <= target)
                if not hit:
                    continue

                deactivate_price_alert(alert_id=int(a["alert_id"]))
                await context.bot.send_message(
                    chat_id=int(a["telegram_id"]),
                    text=(
                        "🔔 Price Alert Triggered!\n\n"
                        f"{symbol} is now {price}\n"
                        f"Target: {direction.upper()} {target}\n"
                        f"Alert ID: #{a['alert_id']}"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("Home", callback_data="home")]]
                    ),
                )
            except Exception as e:
                _log(f"alerts_worker: notify failed alert_id={a.get('alert_id')}: {e!r}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log("start: begin")
    msg = update.effective_message
    user = update.effective_user
    ref_line = ""
    admin_note = ""
    if user:
        _log(f"start: DB create_or_update_user id={user.id}")
        create_or_update_user(user.id, user.username)
        if context.args and context.args[0].startswith("ref_"):
            ok, ref_msg, referrer_id = apply_referral_code(
                user.id, context.args[0][4:]
            )
            if ok:
                ref_line = f"\n{ref_msg}\n"
                if referrer_id:
                    await notify_referrer(
                        context.bot,
                        referrer_id=referrer_id,
                        referee_id=user.id,
                    )
                    ref_tier = get_tier(user.id)
                    if ref_tier in ("pro", "elite"):
                        await on_tier_activated(context.bot, user.id, ref_tier)
        tier = get_tier(user.id)
        _log(f"start: tier={tier}")
        if is_admin(user.id):
            admin_note = "\n⭐ Admin: full access (no payment required).\n"
    else:
        tier = "free"
    expiry = tier_expiry_line(user.id) if user else ""
    text = (
        "🚀 Welcome to the Crypto Trading Assistant Bot.\n\n"
        "Top coins summary, volatility alerts, risk reminders, "
        "watchlist, news, and (Pro/Elite) EMA/RSI + alerts.\n\n"
        f"Your plan: {tier.upper()}{expiry}{ref_line}{admin_note}\n"
        "Use /upgrade for plan details."
    )
    if msg:
        await msg.reply_text(text, reply_markup=main_menu_keyboard())
    _log("start: reply sent")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    _log("summary: begin get_top_coins_summary")
    t0 = time.perf_counter()
    try:
        summary_text = await get_top_coins_summary(limit=6)
    except Exception as e:
        _log(f"summary: ERROR after {time.perf_counter() - t0:.2f}s: {e!r}")
        traceback.print_exc()
        await update.message.reply_text(
            "Sorry, I could not fetch market data right now. Please try again in a moment."
        )
        return
    _log(f"summary: OK in {time.perf_counter() - t0:.2f}s, sending reply")
    await update.message.reply_text(summary_text)
    _log("summary: reply sent")


async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    _log("risk: sending static text")
    await update.message.reply_text(RISK_REMINDER_TEXT)
    _log("risk: done")


async def volatility_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    _log("volatility: begin get_volatility_alert")
    t0 = time.perf_counter()
    try:
        text = await get_volatility_alert(threshold_percent=5.0)
    except Exception as e:
        _log(f"volatility: ERROR after {time.perf_counter() - t0:.2f}s: {e!r}")
        traceback.print_exc()
        text = "Could not fetch volatility data. Try again in a moment."
    _log(f"volatility: OK in {time.perf_counter() - t0:.2f}s, sending reply")
    await update.message.reply_text(text)
    _log("volatility: reply sent")


async def technical_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    _log(f"technical: args={context.args!r}")
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid) and get_tier(uid) == "free":
        await update.message.reply_text(
            "EMA/RSI technical snapshot is for Pro and Elite.\n"
            "Use /upgrade for plan info. Ask admin to set tier for testing."
        )
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /technical SYMBOL [INTERVAL]\n"
            "Example: /technical BTC 1h  or  /technical ETH 4h"
        )
        return
    symbol = args[0]
    interval = args[1] if len(args) > 1 else "1h"
    _log(f"technical: fetching EMA/RSI {symbol} {interval}")
    t0 = time.perf_counter()
    text = await get_ema_rsi_summary(symbol=symbol, interval=interval)
    _log(f"technical: done in {time.perf_counter() - t0:.2f}s")
    await update.message.reply_text(text)


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    uid = update.effective_user.id if update.effective_user else 0
    _log(f"upgrade: user {uid}")
    tier = get_tier(uid)
    admin_line = ""
    if is_admin(uid):
        admin_line = "\n⭐ You are an admin — all features are unlocked without payment.\n"
    expiry = tier_expiry_line(uid)
    text = (
        f"🔓 Your plan: {tier.upper()}{expiry}{admin_line}\n"
        "Plans:\n"
        "• Free: Daily summary, volatility, risk reminder, news\n"
        f"• Pro (${_plan_price_usd('pro'):.2f}/mo): EMA/RSI, watchlist, price & tech alerts\n"
        f"• Elite (${_plan_price_usd('elite'):.2f}/mo): + AI journal, weekly AI summary\n\n"
    )
    markup = None
    if nowpayments_enabled() and not is_admin(uid):
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Pay Pro", callback_data="pay:pro"),
                    InlineKeyboardButton("Pay Elite", callback_data="pay:elite"),
                ]
            ]
        )
    await update.message.reply_text(text, reply_markup=markup)


def _plan_price_usd(plan: str) -> Optional[float]:
    plan = (plan or "").lower().strip()
    if plan == "pro":
        return float(os.getenv("PLAN_PRO_USD", "15.00"))
    if plan == "elite":
        return float(os.getenv("PLAN_ELITE_USD", "19.99"))
    return None


async def _start_payment_for_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    plan: str,
) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if is_admin(uid):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Admin users don't need to pay. You're already unlocked."
            )
        return
    plan = (plan or "").lower().strip()
    if plan not in ("pro", "elite"):
        if update.effective_message:
            await update.effective_message.reply_text("Invalid plan. Use Pro or Elite.")
        return
    amount_usd = _plan_price_usd(plan)
    if amount_usd is None:
        if update.effective_message:
            await update.effective_message.reply_text("Unknown plan.")
        return

    create_or_update_user(uid, update.effective_user.username if update.effective_user else None)

    payment_provider = os.getenv("PAYMENT_PROVIDER", "nowpayments").lower().strip()
    if payment_provider == "cryptomus" and cryptomus_enabled():
        order_id = f"tg{uid}-{plan}-{int(time.time())}"
        create_payment_order(
            order_id=order_id,
            provider="cryptomus",
            telegram_id=uid,
            plan=plan,
            amount_usd=amount_usd,
            payment_kind="invoice",
            payment_status="waiting",
        )
        url = await create_payment_invoice(plan, amount_usd, uid)
        if not url:
            update_payment_order(order_id=order_id, payment_status="error")
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Could not create Cryptomus payment. Try again."
                )
            return
        update_payment_order(order_id=order_id, invoice_url=url)
        if update.effective_message:
            await update.effective_message.reply_text(
                f"✅ Cryptomus payment created.\n"
                f"Plan: {plan.upper()} (${amount_usd:.2f})\n"
                f"Pay here: {url}\n"
                "After payment, run /check_payment"
            )
        return

    if not nowpayments_enabled():
        if update.effective_message:
            await update.effective_message.reply_text(
                "Payment not configured.\n"
                "Set NOWPAYMENTS_API_KEY or CRYPTOMUS credentials in .env."
            )
        return

    # Unique per request (avoid collisions if user retries).
    order_id = f"tg{uid}-{plan}-{int(time.time())}"
    create_payment_order(
        order_id=order_id,
        provider="nowpayments",
        telegram_id=uid,
        plan=plan,
        amount_usd=amount_usd,
        payment_kind="creating",
        payment_status="creating",
    )

    ipn_callback_url = os.getenv("NOWPAYMENTS_IPN_CALLBACK_URL", "").strip() or None
    success_url = os.getenv("NOWPAYMENTS_SUCCESS_URL", "").strip() or None
    cancel_url = os.getenv("NOWPAYMENTS_CANCEL_URL", "").strip() or None
    pay_currency = os.getenv("NOWPAYMENTS_PAY_CURRENCY", "").strip()

    mode = os.getenv("NOWPAYMENTS_MODE", "invoice").strip().lower()
    if mode in ("address", "payment", "deposit"):
        if not pay_currency:
            pay_currency = "usdttrc20"
        pay = await create_payment(
            order_id=order_id,
            order_description=f"Telegram plan upgrade: {plan.upper()} (tg:{uid})",
            price_amount=amount_usd,
            price_currency="usd",
            pay_currency=pay_currency,
            ipn_callback_url=ipn_callback_url,
        )
        if not pay or pay.get("_error"):
            update_payment_order(order_id=order_id, payment_kind="payment", payment_status="error")
            if update.effective_message:
                detail = ""
                if isinstance(pay, dict) and pay.get("_error"):
                    body = pay.get("body")
                    # Common NOWPayments error payloads include 'message' or 'errors'
                    if isinstance(body, dict):
                        msg = body.get("message") or body.get("error") or body.get("errors")
                        if msg:
                            detail = f"\nReason: {msg}"
                    code = pay.get("status_code")
                    if not detail and code:
                        detail = f"\n(HTTP {code})"
                await update.effective_message.reply_text(
                    "Could not create payment address right now. Please try again."
                    + detail
                )
            return

        payment_id = str(pay.get("payment_id") or pay.get("id") or "")
        pay_address = (pay.get("pay_address") or "").strip()
        pay_amount = pay.get("pay_amount")
        pay_currency_out = (pay.get("pay_currency") or pay_currency).upper()

        update_payment_order(
            order_id=order_id,
            invoice_id=payment_id or None,
            invoice_url=None,
            payment_kind="payment",
            payment_status=(pay.get("payment_status") or "waiting"),
        )

        if update.effective_message:
            lines = [
                "✅ Payment created.",
                f"Plan: {plan.upper()} (${amount_usd:.2f})",
                "",
            ]
            if pay_amount:
                lines += ["Send:", f"<code>{pay_amount} {pay_currency_out}</code>", ""]
            else:
                lines += ["Currency:", f"<code>{pay_currency_out}</code>", ""]
            if pay_address:
                lines += ["To address:", f"<code>{pay_address}</code>", ""]
            lines += ["After payment, run /check_payment"]

            await update.effective_message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Check payment", callback_data="check_payment")]]
                ),
            )
        return

    inv = await create_invoice(
        order_id=order_id,
        order_description=f"Telegram plan upgrade: {plan.upper()} (tg:{uid})",
        price_amount=amount_usd,
        price_currency="usd",
        pay_currency=pay_currency or None,
        ipn_callback_url=ipn_callback_url,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if not inv:
        update_payment_order(order_id=order_id, payment_kind="invoice", payment_status="error")
        if update.effective_message:
            await update.effective_message.reply_text(
                "Could not create invoice right now. Please try again."
            )
        return

    invoice_id = str(inv.get("id") or "")
    invoice_url = inv.get("invoice_url") or inv.get("url") or ""
    update_payment_order(
        order_id=order_id,
        invoice_id=invoice_id or None,
        invoice_url=invoice_url or None,
        payment_kind="invoice",
        payment_status="waiting",
    )

    if update.effective_message:
        await update.effective_message.reply_text(
            "✅ Invoice created.\n"
            f"Plan: {plan.upper()} (${amount_usd:.2f})\n"
            f"Order: {order_id}\n"
            + (f"Pay here: {invoice_url}\n" if invoice_url else "")
            + "After payment, run /check_payment"
        )


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    args = context.args or []
    if len(args) != 1 or args[0].lower() not in ("pro", "elite"):
        await update.message.reply_text("Usage: /pay pro  or  /pay elite")
        return
    await _start_payment_for_plan(update, context, plan=args[0].lower())


async def pay_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = (query.data or "").strip()
    if data == "pay:pro":
        await _start_payment_for_plan(update, context, plan="pro")
        return
    if data == "pay:elite":
        await _start_payment_for_plan(update, context, plan="elite")
        return
    if data == "check_payment":
        await check_payment_command(update, context)
        return
    if data == "home":
        await start(update, context)
        return
    if data == "journal:add":
        if update.effective_message:
            await update.effective_message.reply_text(
                "Send:\n"
                "/journal_add SYMBOL SIDE | notes...\n\n"
                "Example:\n"
                "/journal_add BTCUSDT long | Setup, SL, TP, mistakes, lessons"
            )
        return
    if data == "journal:list":
        await journal_list_command(update, context)
        return
    if data.startswith("journal:delete:"):
        uid = update.effective_user.id if update.effective_user else 0
        if not uid or not _journal_allowed(uid):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "AI Trade Journal is for Elite only. Use /upgrade."
                )
            return
        try:
            entry_id = int(data.split(":")[-1])
        except Exception:
            return
        ok = delete_trade_journal_entry(
            uid,
            entry_id,
        )
        if update.effective_message:
            await update.effective_message.reply_text("✅ Deleted." if ok else "Entry not found.")
        return


async def check_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reply_if_rate_limited(update):
        return
    msg = update.effective_message
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return
    if is_admin(uid):
        if msg:
            await msg.reply_text("Admin: payment not required.")
        return
    provider = os.getenv("PAYMENT_PROVIDER", "nowpayments").lower().strip()
    if provider == "cryptomus":
        if not cryptomus_enabled():
            if msg:
                await msg.reply_text("Cryptomus is not configured yet.")
            return
        last = get_latest_payment_order(uid, provider="cryptomus")
        if not last:
            if msg:
                await msg.reply_text("No recent Cryptomus payment. Use /pay first.")
            return
        if msg:
            await msg.reply_text(
                f"Cryptomus order: {last.get('order_id')}\n"
                f"Status: {last.get('payment_status')}\n"
                + (f"Pay: {last.get('invoice_url')}\n" if last.get("invoice_url") else "")
                + "Cryptomus auto-confirm requires CALLBACK_URL webhook on your server."
            )
        return

    if not nowpayments_enabled():
        if msg:
            await msg.reply_text("NOWPayments is not configured yet.")
        return

    last = get_latest_payment_order(uid, provider="nowpayments")
    if not last:
        if msg:
            await msg.reply_text("No recent payment found. Use /pay pro or /pay elite first.")
        return
    invoice_id = (last.get("invoice_id") or "").strip()
    if not invoice_id:
        if msg:
            await msg.reply_text(
                f"Latest order: {last.get('order_id')}\n"
                "Invoice id is missing; please recreate payment with /pay."
            )
        return

    kind = (last.get("payment_kind") or "invoice").lower().strip()
    data = await (get_payment(invoice_id) if kind == "payment" else get_invoice(invoice_id))
    if not data or data.get("_error"):
        if msg:
            await msg.reply_text(
                f"Could not fetch payment status right now.\n"
                f"Order: {last.get('order_id')}\n"
                "You can also check status from NOWPayments dashboard."
            )
        return

    status = (data.get("payment_status") or data.get("status") or "").lower().strip()
    update_payment_order(
        order_id=last["order_id"],
        payment_status=status or last.get("payment_status") or "unknown",
    )

    if is_paid_status(status):
        await process_payment_status(last["order_id"], status, bot=context.bot)
        if msg:
            await msg.reply_text(
                f"✅ Payment confirmed. Your plan is now {get_tier(uid).upper()}.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Home", callback_data="home")]]
                ),
            )
        return

    if msg:
        await msg.reply_text(
            f"Payment status: {status or 'unknown'}\n"
            f"Order: {last.get('order_id')}\n"
            + (f"Invoice: {last.get('invoice_url')}\n" if last.get("invoice_url") else "")
            + "If you just paid, wait 1–2 minutes.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Check payment", callback_data="check_payment")],
                    [InlineKeyboardButton("Home", callback_data="home")],
                ]
            ),
        )


async def set_tier_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("Not allowed.")
        return
    args = context.args or []
    if len(args) != 1 or args[0].lower() not in ("free", "pro", "elite"):
        await update.message.reply_text("Usage: /set_tier free|pro|elite")
        return
    from core.db import admin_set_user_tier

    create_or_update_user(update.effective_user.id, update.effective_user.username)
    admin_set_user_tier(update.effective_user.id, args[0].lower())
    await update.message.reply_text(f"Tier set to {args[0].upper()}.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Commands:\n\n"
        "/start - Menu\n"
        "/summary - Top coins daily summary\n"
        "/volatility - 24h move ≥5%\n"
        "/risk - Risk reminder\n"
        "/news - Crypto news\n"
        "/watchlist - Your watchlist (Pro/Elite)\n"
        "/watch_add SYMBOL - Add to watchlist\n"
        "/technical SYMBOL [interval] - EMA/RSI (Pro/Elite)\n"
        "/add_alert - Price alert (Pro/Elite)\n"
        "/tech_alert - Technical alert (Pro/Elite)\n"
        "/journal - AI trade journal (Elite)\n"
        "/weekly_summary - Weekly AI summary (Elite)\n"
        "/referral - Your referral code\n"
        "/my_channel - Private channel invite (Pro/Elite)\n"
        "/upgrade - Plans\n"
        "/pay pro|elite - Payment\n"
        "/check_payment - Check payment\n"
        "/help - This message\n"
        "(Admin) /admin_stats /admin_backup /admin_set_tier"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = (update.message.text or "").strip()
    uid = update.effective_user.id if update.effective_user else 0
    _log(f"text_fallback: user={uid} text={message_text!r}")

    if message_text == "Daily Summary":
        await summary_command(update, context)
        return
    if message_text == "Risk Reminder":
        await risk_command(update, context)
        return
    if message_text == "Volatility Alert":
        await volatility_command(update, context)
        return
    if message_text == "Watchlist":
        await watchlist_command(update, context)
        return
    if message_text == "Crypto News":
        await news_command(update, context)
        return
    if message_text == "Weekly Summary":
        await weekly_summary_command(update, context)
        return
    if message_text == "Upgrade Plan":
        await upgrade_command(update, context)
        return
    if message_text == "Buy PRO":
        context.args = ["pro"]
        await pay_command(update, context)
        return
    if message_text == "Buy ELITE":
        context.args = ["elite"]
        await pay_command(update, context)
        return
    if message_text == "Check Payment":
        await check_payment_command(update, context)
        return
    if message_text == "My Alerts":
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid) and get_tier(uid) == "free":
            await update.message.reply_text(
                "Alerts (price / volatility / EMA-RSI) are for Pro and Elite. Use /upgrade."
            )
        else:
            await my_alerts_command(update, context)
        return
    if message_text == "AI Trade Journal":
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid) and get_tier(uid) != "elite":
            await update.message.reply_text(
                "AI Trade Journal is for Elite only. Use /upgrade."
            )
        else:
            await journal_menu(update, context)
        return

    _log("text_fallback: unknown -> fallback reply")
    await update.message.reply_text(
        "I did not understand that message. "
        "Use the menu or send /start.",
        reply_markup=main_menu_keyboard(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log(f"ERROR handler: {context.error!r}")
    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__,
    )
    if context.error and context.bot:
        try:
            await notify_admin_error(
                context.bot, context.error, context="bot handler"
            )
        except Exception:
            pass


def main() -> None:
    load_dotenv()
    setup_logging()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Create a .env file in the project root with TELEGRAM_BOT_TOKEN=YOUR_TOKEN"
        )

    _log("Building application (concurrent_updates=True so multiple chats don't queue)")
    application = (
        ApplicationBuilder()
        .token(bot_token)
        .concurrent_updates(True)
        .build()
    )

    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("risk", risk_command))
    application.add_handler(CommandHandler("volatility", volatility_command))
    application.add_handler(CommandHandler("technical", technical_command))
    application.add_handler(CommandHandler("upgrade", upgrade_command))
    application.add_handler(CommandHandler("my_alerts", my_alerts_command))
    application.add_handler(CommandHandler("add_alert", add_alert_command))
    application.add_handler(CommandHandler("remove_alert", remove_alert_command))
    application.add_handler(CommandHandler("journal", journal_menu))
    application.add_handler(CommandHandler("journal_add", journal_add_command))
    application.add_handler(CommandHandler("journal_list", journal_list_command))
    application.add_handler(CommandHandler("journal_view", journal_view_command))
    application.add_handler(CommandHandler("journal_delete", journal_delete_command))
    application.add_handler(CommandHandler("pay", pay_command))
    application.add_handler(CommandHandler("check_payment", check_payment_command))
    application.add_handler(
        CallbackQueryHandler(
            pay_button_callback,
            pattern=r"^(pay:(pro|elite)|check_payment|home|journal:(add|list)|journal:delete:\\d+)$",
        )
    )
    application.add_handler(CommandHandler("set_tier", set_tier_command))
    application.add_handler(CommandHandler("news", news_command))
    application.add_handler(CommandHandler("watchlist", watchlist_command))
    application.add_handler(CommandHandler("watch_add", watch_add_command))
    application.add_handler(CommandHandler("watch_remove", watch_remove_command))
    application.add_handler(CommandHandler("tech_alert", tech_alert_add_command))
    application.add_handler(CommandHandler("tech_alert_remove", tech_alert_remove_command))
    application.add_handler(CommandHandler("my_tech_alerts", my_tech_alerts_command))
    application.add_handler(CommandHandler("weekly_summary", weekly_summary_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("use_referral", use_referral_command))
    application.add_handler(CommandHandler("my_channel", my_channel_command))
    application.add_handler(CommandHandler("admin_stats", admin_stats_command))
    application.add_handler(CommandHandler("admin_backup", admin_backup_command))
    application.add_handler(CommandHandler("admin_set_tier", admin_set_tier_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback)
    )

    async def post_init(app):
        _log("post_init: set_my_commands")
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Start bot and menu"),
                BotCommand("summary", "Top coins daily summary"),
                BotCommand("volatility", "Volatility alert ≥5%"),
                BotCommand("risk", "Risk reminder"),
                BotCommand("news", "Crypto news"),
                BotCommand("watchlist", "Your watchlist (Pro/Elite)"),
                BotCommand("technical", "EMA/RSI (Pro/Elite)"),
                BotCommand("upgrade", "Plans"),
                BotCommand("my_alerts", "List your active alerts"),
                BotCommand("add_alert", "Add a price alert"),
                BotCommand("tech_alert", "Add technical alert"),
                BotCommand("journal", "Trade journal menu (Elite)"),
                BotCommand("weekly_summary", "Weekly AI summary (Elite)"),
                BotCommand("referral", "Your referral code"),
                BotCommand("my_channel", "Private channel invite"),
                BotCommand("pay", "Pay for pro/elite"),
                BotCommand("check_payment", "Check last payment status"),
                BotCommand("help", "Help"),
            ]
        )
        await start_webhook_server(app.bot)

    application.post_init = post_init

    # Background alert checkers
    try:
        interval = float(os.getenv("ALERTS_POLL_SECONDS", "20"))
    except Exception:
        interval = 20.0
    application.job_queue.run_repeating(alerts_worker, interval=interval, first=5)
    application.job_queue.run_repeating(technical_alerts_worker, interval=interval, first=10)

    sub_interval = max(3600.0, SUBSCRIPTION_CHECK_HOURS * 3600.0)
    application.job_queue.run_repeating(
        subscription_worker, interval=sub_interval, first=120
    )
    application.job_queue.run_daily(
        weekly_summary_worker,
        time=dt_time(
            hour=WEEKLY_SUMMARY_HOUR, minute=0, tzinfo=timezone.utc
        ),
        days=(WEEKLY_SUMMARY_DAY,),
        name="weekly_summary",
    )

    backup_interval = max(3600.0, DB_BACKUP_HOURS * 3600.0)
    application.job_queue.run_repeating(
        backup_worker, interval=backup_interval, first=300, name="db_backup"
    )

    if AUTO_BROADCAST_ENABLED:
        _log("Auto channel broadcast: enabled")
        free_iv = max(1800.0, FREE_BROADCAST_HOURS * 3600.0)
        pro_iv = max(1800.0, PRO_BROADCAST_HOURS * 3600.0)
        elite_iv = max(1800.0, ELITE_BROADCAST_HOURS * 3600.0)
        application.job_queue.run_repeating(
            free_channel_broadcast_worker,
            interval=free_iv,
            first=180,
            name="broadcast_free",
        )
        application.job_queue.run_repeating(
            pro_channel_broadcast_worker,
            interval=pro_iv,
            first=240,
            name="broadcast_pro",
        )
        application.job_queue.run_repeating(
            elite_channel_broadcast_worker,
            interval=elite_iv,
            first=300,
            name="broadcast_elite",
        )
    else:
        _log("Auto channel broadcast: disabled")

    _log("Starting polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
