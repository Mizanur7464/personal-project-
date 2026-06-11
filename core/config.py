"""App-wide configuration from environment."""
from __future__ import annotations

import os

SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))

ALERT_LIMIT_FREE = int(os.getenv("ALERT_LIMIT_FREE", "0"))
ALERT_LIMIT_PRO = int(os.getenv("ALERT_LIMIT_PRO", "10"))
ALERT_LIMIT_ELITE = int(os.getenv("ALERT_LIMIT_ELITE", "50"))

WATCHLIST_LIMIT_PRO = int(os.getenv("WATCHLIST_LIMIT_PRO", "10"))
WATCHLIST_LIMIT_ELITE = int(os.getenv("WATCHLIST_LIMIT_ELITE", "25"))

RATE_LIMIT_CALLS = int(os.getenv("RATE_LIMIT_CALLS", "15"))
RATE_LIMIT_WINDOW_SEC = float(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))

WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

PAID_STATUSES = frozenset(
    {"finished", "confirmed", "paid", "complete", "completed"}
)

RENEWAL_REMINDER_DAYS = int(os.getenv("RENEWAL_REMINDER_DAYS", "3"))
# Comma-separated days-before-expiry reminders, e.g. 7,3,1
RENEWAL_REMINDER_THRESHOLDS = tuple(
    int(x.strip())
    for x in os.getenv("RENEWAL_REMINDER_THRESHOLDS", "7,3,1").split(",")
    if x.strip().isdigit()
) or (7, 3, 1)
SUBSCRIPTION_CHECK_HOURS = float(os.getenv("SUBSCRIPTION_CHECK_HOURS", "6"))
DB_BACKUP_HOURS = float(os.getenv("DB_BACKUP_HOURS", "24"))

REFERRAL_REFEREE_BONUS_DAYS = int(os.getenv("REFERRAL_REFEREE_BONUS_DAYS", "3"))
REFERRAL_REFERRER_BONUS_DAYS = int(os.getenv("REFERRAL_REFERRER_BONUS_DAYS", "3"))
REFERRAL_REFEREE_TIER = os.getenv("REFERRAL_REFEREE_TIER", "pro").lower().strip()

WEEKLY_SUMMARY_DAY = int(os.getenv("WEEKLY_SUMMARY_DAY", "0"))  # 0=Monday (UTC)
WEEKLY_SUMMARY_HOUR = int(os.getenv("WEEKLY_SUMMARY_HOUR", "9"))

AUTO_BROADCAST_ENABLED = os.getenv("AUTO_BROADCAST_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
FREE_BROADCAST_HOURS = float(os.getenv("FREE_BROADCAST_HOURS", "8"))
PRO_BROADCAST_HOURS = float(os.getenv("PRO_BROADCAST_HOURS", "4"))
ELITE_BROADCAST_HOURS = float(os.getenv("ELITE_BROADCAST_HOURS", "4"))
VOLATILITY_THRESHOLD = float(os.getenv("VOLATILITY_THRESHOLD", "5.0"))
SIGNAL_INTERVAL = os.getenv("SIGNAL_INTERVAL", "1h").strip()
SIGNAL_COINS = tuple(
    s.strip().upper()
    for s in os.getenv("SIGNAL_COINS", "BTC,ETH,SOL,BNB,XRP").split(",")
    if s.strip()
) or ("BTC", "ETH", "SOL", "BNB", "XRP")
