"""
Phase 3: SQLite user store for Free/Pro/Elite tier.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from core.config import (
    REFERRAL_REFEREE_BONUS_DAYS,
    REFERRAL_REFEREE_TIER,
    REFERRAL_REFERRER_BONUS_DAYS,
    SUBSCRIPTION_DAYS,
)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.db")
VALID_TIERS = ("free", "pro", "elite")
VALID_PAYMENT_PROVIDERS = ("nowpayments", "cryptomus")
VALID_TECH_CONDITIONS = (
    "rsi_above",
    "rsi_below",
    "ema_bullish",
    "ema_bearish",
)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                tier TEXT NOT NULL DEFAULT 'free',
                created_at TEXT NOT NULL,
                expires_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                order_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                invoice_id TEXT,
                invoice_url TEXT,
                payment_kind TEXT NOT NULL DEFAULT 'invoice',
                payment_status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Lightweight migrations for existing DBs.
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(payment_orders)").fetchall()
        }
        if "payment_kind" not in cols:
            conn.execute(
                "ALTER TABLE payment_orders ADD COLUMN payment_kind TEXT NOT NULL DEFAULT 'invoice'"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_orders_telegram_id ON payment_orders (telegram_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders (payment_status)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL, -- 'above' | 'below'
                target_price REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                triggered_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts (is_active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts (telegram_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_journal_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                symbol TEXT,
                side TEXT, -- 'long'|'short'|'spot'
                entry_price REAL,
                exit_price REAL,
                size REAL,
                leverage REAL,
                pnl REAL,
                notes TEXT,
                ai_feedback TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_journal_user ON trade_journal_entries (telegram_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(telegram_id, symbol)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist (telegram_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS technical_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                condition_type TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '1h',
                threshold REAL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                triggered_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_technical_alerts_active ON technical_alerts (is_active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_technical_alerts_user ON technical_alerts (telegram_id)"
        )

        user_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "referral_code" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
        if "referred_by" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "renewal_reminder_sent" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN renewal_reminder_sent INTEGER NOT NULL DEFAULT 0"
            )
        if "renewal_reminders_done" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN renewal_reminders_done TEXT NOT NULL DEFAULT ''"
            )
        if "expiry_notice_sent" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN expiry_notice_sent INTEGER NOT NULL DEFAULT 0"
            )

        conn.commit()


def get_user(telegram_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT telegram_id, username, tier, created_at, expires_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def create_or_update_user(telegram_id: int, username: Optional[str] = None) -> dict:
    from datetime import datetime

    init_db()
    now = datetime.utcnow().isoformat() + "Z"
    with _connect() as conn:
        existing = conn.execute(
            "SELECT telegram_id, tier, expires_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET username = ?, created_at = COALESCE(created_at, ?) WHERE telegram_id = ?",
                (username or "", now, telegram_id),
            )
        else:
            conn.execute(
                "INSERT INTO users (telegram_id, username, tier, created_at) VALUES (?, ?, 'free', ?)",
                (telegram_id, username or "", now),
            )
        conn.commit()
    out = get_user(telegram_id)
    assert out is not None
    return out


def _parse_iso_z(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _tier_is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    exp = _parse_iso_z(expires_at)
    if exp is None:
        return False
    return datetime.utcnow() > exp


def set_tier(
    telegram_id: int,
    tier: str,
    expires_at: Optional[str] = None,
) -> None:
    if tier not in VALID_TIERS:
        raise ValueError(f"tier must be one of {VALID_TIERS}")
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET tier = ?, expires_at = ? WHERE telegram_id = ?",
            (tier, expires_at, telegram_id),
        )
        conn.commit()


def _reset_subscription_notifications(telegram_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET renewal_reminder_sent = 0,
                renewal_reminders_done = '',
                expiry_notice_sent = 0
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        conn.commit()


def _parse_reminders_done(raw: Optional[str]) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def _format_reminders_done(done: set[int]) -> str:
    return ",".join(str(d) for d in sorted(done, reverse=True))


def activate_paid_tier(
    telegram_id: int,
    tier: str,
    *,
    days: Optional[int] = None,
) -> None:
    """Set paid tier with subscription expiry."""
    days = days if days is not None else SUBSCRIPTION_DAYS
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"
    set_tier(telegram_id, tier, expires_at=expires)
    _reset_subscription_notifications(telegram_id)


def extend_subscription_days(
    telegram_id: int,
    days: int,
    *,
    tier: Optional[str] = None,
) -> None:
    """Add bonus days to an active subscription or start a short trial."""
    user = get_user(telegram_id)
    if user is None:
        create_or_update_user(telegram_id)
        user = get_user(telegram_id)
    assert user is not None

    current_tier = (user.get("tier") or "free").lower()
    target_tier = (tier or current_tier or "pro").lower()
    if target_tier == "free":
        target_tier = "pro"

    exp_raw = user.get("expires_at")
    if current_tier != "free" and exp_raw and not _tier_is_expired(exp_raw):
        exp = _parse_iso_z(exp_raw)
        assert exp is not None
        new_exp = exp + timedelta(days=days)
    else:
        new_exp = datetime.utcnow() + timedelta(days=days)

    set_tier(telegram_id, target_tier, expires_at=new_exp.isoformat() + "Z")
    _reset_subscription_notifications(telegram_id)


def get_tier(telegram_id: int) -> str:
    """Effective tier (expired paid plans count as free)."""
    user = get_user(telegram_id)
    if user is None:
        return "free"
    tier = (user["tier"] or "free").lower()
    if tier != "free" and _tier_is_expired(user.get("expires_at")):
        return "free"
    return tier


def downgrade_expired_user(telegram_id: int) -> None:
    set_tier(telegram_id, "free", expires_at=None)


def get_users_for_renewal_reminder(*, days_before: int = 3) -> list[dict]:
    """Legacy single-threshold helper."""
    return get_users_for_renewal_reminder_at_threshold(threshold_days=days_before)


def get_users_for_renewal_reminder_at_threshold(*, threshold_days: int) -> list[dict]:
    """Users who should receive a reminder at this days-before-expiry threshold."""
    init_db()
    now = datetime.utcnow()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, tier, expires_at, renewal_reminders_done
            FROM users
            WHERE tier IN ('pro', 'elite')
              AND expires_at IS NOT NULL
            """
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        exp = _parse_iso_z(row["expires_at"])
        if exp is None or exp <= now:
            continue
        days_left = (exp - now).total_seconds() / 86400.0
        if days_left > threshold_days:
            continue
        done = _parse_reminders_done(row["renewal_reminders_done"])
        if threshold_days in done:
            continue
        item = dict(row)
        item["days_left"] = int(days_left) if days_left >= 1 else 1
        item["threshold_days"] = threshold_days
        out.append(item)
    return out


def mark_renewal_threshold_sent(telegram_id: int, threshold_days: int) -> None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT renewal_reminders_done FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        done = _parse_reminders_done(row["renewal_reminders_done"] if row else "")
        done.add(threshold_days)
        conn.execute(
            """
            UPDATE users
            SET renewal_reminders_done = ?, renewal_reminder_sent = 1
            WHERE telegram_id = ?
            """,
            (_format_reminders_done(done), telegram_id),
        )
        conn.commit()


def get_users_for_expiry_notice() -> list[dict]:
    init_db()
    now = datetime.utcnow()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, tier, expires_at
            FROM users
            WHERE tier IN ('pro', 'elite')
              AND expires_at IS NOT NULL
              AND expiry_notice_sent = 0
            """
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        exp = _parse_iso_z(row["expires_at"])
        if exp is None or exp > now:
            continue
        out.append(dict(row))
    return out


def mark_renewal_reminder_sent(telegram_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET renewal_reminder_sent = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        conn.commit()


def mark_expiry_notice_sent(telegram_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET expiry_notice_sent = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        conn.commit()


def list_elite_user_ids() -> list[int]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT telegram_id, tier, expires_at FROM users WHERE tier = 'elite'"
        ).fetchall()
    ids: list[int] = []
    for row in rows:
        uid = int(row["telegram_id"])
        if get_tier(uid) == "elite":
            ids.append(uid)
    return ids


def get_tier_expires_at(telegram_id: int) -> Optional[str]:
    user = get_user(telegram_id)
    if user is None:
        return None
    get_tier(telegram_id)
    user = get_user(telegram_id)
    return user.get("expires_at") if user else None


def create_payment_order(
    *,
    order_id: str,
    provider: str,
    telegram_id: int,
    plan: str,
    amount_usd: float,
    invoice_id: Optional[str] = None,
    invoice_url: Optional[str] = None,
    payment_kind: str = "invoice",
    payment_status: str = "created",
) -> None:
    from datetime import datetime

    if provider not in VALID_PAYMENT_PROVIDERS:
        raise ValueError(f"provider must be one of {VALID_PAYMENT_PROVIDERS}")
    init_db()
    now = datetime.utcnow().isoformat() + "Z"
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO payment_orders
                (order_id, provider, telegram_id, plan, amount_usd, invoice_id, invoice_url, payment_kind, payment_status, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM payment_orders WHERE order_id = ?), ?), ?)
            """,
            (
                order_id,
                provider,
                telegram_id,
                plan,
                float(amount_usd),
                invoice_id,
                invoice_url,
                payment_kind,
                payment_status,
                order_id,
                now,
                now,
            ),
        )
        conn.commit()


def update_payment_order(
    *,
    order_id: str,
    invoice_id: Optional[str] = None,
    invoice_url: Optional[str] = None,
    payment_kind: Optional[str] = None,
    payment_status: Optional[str] = None,
) -> None:
    from datetime import datetime

    init_db()
    now = datetime.utcnow().isoformat() + "Z"
    fields: list[str] = []
    values: list[object] = []
    if invoice_id is not None:
        fields.append("invoice_id = ?")
        values.append(invoice_id)
    if invoice_url is not None:
        fields.append("invoice_url = ?")
        values.append(invoice_url)
    if payment_kind is not None:
        fields.append("payment_kind = ?")
        values.append(payment_kind)
    if payment_status is not None:
        fields.append("payment_status = ?")
        values.append(payment_status)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(order_id)

    with _connect() as conn:
        conn.execute(
            f"UPDATE payment_orders SET {', '.join(fields)} WHERE order_id = ?",
            tuple(values),
        )
        conn.commit()


def get_payment_order_by_order_id(order_id: str) -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT order_id, provider, telegram_id, plan, amount_usd, invoice_id, invoice_url,
                   payment_kind, payment_status, created_at, updated_at
            FROM payment_orders WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_latest_payment_order(telegram_id: int, *, provider: str = "nowpayments") -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT order_id, provider, telegram_id, plan, amount_usd, invoice_id, invoice_url, payment_kind, payment_status, created_at, updated_at
            FROM payment_orders
            WHERE telegram_id = ? AND provider = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (telegram_id, provider),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def add_price_alert(
    *,
    telegram_id: int,
    symbol: str,
    direction: str,
    target_price: float,
) -> int:
    from datetime import datetime

    init_db()
    direction = (direction or "").lower().strip()
    if direction not in ("above", "below"):
        raise ValueError("direction must be 'above' or 'below'")
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise ValueError("symbol is required")

    now = datetime.utcnow().isoformat() + "Z"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO price_alerts (telegram_id, symbol, direction, target_price, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (telegram_id, symbol, direction, float(target_price), now),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_price_alerts(telegram_id: int, *, only_active: bool = True) -> list[dict]:
    init_db()
    q = """
        SELECT alert_id, telegram_id, symbol, direction, target_price, is_active, created_at, triggered_at
        FROM price_alerts
        WHERE telegram_id = ?
    """
    params: list[object] = [telegram_id]
    if only_active:
        q += " AND is_active = 1"
    q += " ORDER BY alert_id DESC"

    with _connect() as conn:
        rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def remove_price_alert(*, telegram_id: int, alert_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM price_alerts WHERE alert_id = ? AND telegram_id = ?",
            (int(alert_id), int(telegram_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def get_active_price_alerts() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT alert_id, telegram_id, symbol, direction, target_price, is_active, created_at, triggered_at
            FROM price_alerts
            WHERE is_active = 1
            ORDER BY alert_id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_price_alert(*, alert_id: int) -> None:
    from datetime import datetime

    init_db()
    now = datetime.utcnow().isoformat() + "Z"
    with _connect() as conn:
        conn.execute(
            "UPDATE price_alerts SET is_active = 0, triggered_at = COALESCE(triggered_at, ?) WHERE alert_id = ?",
            (now, int(alert_id)),
        )
        conn.commit()


def add_trade_journal_entry(
    *,
    telegram_id: int,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    size: Optional[float] = None,
    leverage: Optional[float] = None,
    pnl: Optional[float] = None,
    notes: Optional[str] = None,
    ai_feedback: Optional[str] = None,
) -> int:
    from datetime import datetime

    init_db()
    now = datetime.utcnow().isoformat() + "Z"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO trade_journal_entries
                (telegram_id, symbol, side, entry_price, exit_price, size, leverage, pnl, notes, ai_feedback, created_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(telegram_id),
                (symbol or "").upper().strip() or None,
                (side or "").lower().strip() or None,
                entry_price,
                exit_price,
                size,
                leverage,
                pnl,
                notes,
                ai_feedback,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_trade_journal_entries(telegram_id: int, *, limit: int = 10) -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT entry_id, telegram_id, symbol, side, entry_price, exit_price, size, leverage, pnl, notes, ai_feedback, created_at
            FROM trade_journal_entries
            WHERE telegram_id = ?
            ORDER BY entry_id DESC
            LIMIT ?
            """,
            (int(telegram_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def get_trade_journal_entry(telegram_id: int, entry_id: int) -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT entry_id, telegram_id, symbol, side, entry_price, exit_price, size, leverage, pnl, notes, ai_feedback, created_at
            FROM trade_journal_entries
            WHERE telegram_id = ? AND entry_id = ?
            """,
            (int(telegram_id), int(entry_id)),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def delete_trade_journal_entry(telegram_id: int, entry_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM trade_journal_entries WHERE telegram_id = ? AND entry_id = ?",
            (int(telegram_id), int(entry_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def update_trade_journal_ai_feedback(telegram_id: int, entry_id: int, ai_feedback: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE trade_journal_entries SET ai_feedback = ? WHERE telegram_id = ? AND entry_id = ?",
            (ai_feedback, int(telegram_id), int(entry_id)),
        )
        conn.commit()


def count_active_alerts(telegram_id: int) -> int:
    init_db()
    with _connect() as conn:
        price = conn.execute(
            "SELECT COUNT(*) FROM price_alerts WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        ).fetchone()[0]
        tech = conn.execute(
            "SELECT COUNT(*) FROM technical_alerts WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        ).fetchone()[0]
    return int(price) + int(tech)


def list_trade_journal_entries_since(
    telegram_id: int,
    *,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    init_db()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT entry_id, telegram_id, symbol, side, entry_price, exit_price, size,
                   leverage, pnl, notes, ai_feedback, created_at
            FROM trade_journal_entries
            WHERE telegram_id = ? AND created_at >= ?
            ORDER BY entry_id DESC
            LIMIT ?
            """,
            (int(telegram_id), since, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Watchlist ---


def add_watchlist_symbol(telegram_id: int, symbol: str) -> bool:
    from datetime import datetime as dt

    init_db()
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise ValueError("symbol is required")
    now = dt.utcnow().isoformat() + "Z"
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (telegram_id, symbol, created_at) VALUES (?, ?, ?)",
                (telegram_id, symbol, now),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_watchlist_symbol(telegram_id: int, symbol: str) -> bool:
    init_db()
    symbol = (symbol or "").upper().strip()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE telegram_id = ? AND symbol = ?",
            (telegram_id, symbol),
        )
        conn.commit()
        return cur.rowcount > 0


def list_watchlist(telegram_id: int) -> list[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE telegram_id = ? ORDER BY id ASC",
            (telegram_id,),
        ).fetchall()
    return [r["symbol"] for r in rows]


def count_watchlist(telegram_id: int) -> int:
    init_db()
    with _connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM watchlist WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()[0]
        )


# --- Technical alerts ---


def add_technical_alert(
    *,
    telegram_id: int,
    symbol: str,
    condition_type: str,
    interval: str = "1h",
    threshold: Optional[float] = None,
) -> int:
    from datetime import datetime as dt

    init_db()
    condition_type = (condition_type or "").lower().strip()
    if condition_type not in VALID_TECH_CONDITIONS:
        raise ValueError(f"condition_type must be one of {VALID_TECH_CONDITIONS}")
    symbol = (symbol or "").upper().strip()
    if not symbol:
        raise ValueError("symbol is required")
    now = dt.utcnow().isoformat() + "Z"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO technical_alerts
                (telegram_id, symbol, condition_type, interval, threshold, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (telegram_id, symbol, condition_type, interval, threshold, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_technical_alerts(telegram_id: int, *, only_active: bool = True) -> list[dict]:
    init_db()
    q = """
        SELECT alert_id, telegram_id, symbol, condition_type, interval, threshold,
               is_active, created_at, triggered_at
        FROM technical_alerts WHERE telegram_id = ?
    """
    params: list[object] = [telegram_id]
    if only_active:
        q += " AND is_active = 1"
    q += " ORDER BY alert_id DESC"
    with _connect() as conn:
        rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def remove_technical_alert(*, telegram_id: int, alert_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM technical_alerts WHERE alert_id = ? AND telegram_id = ?",
            (int(alert_id), int(telegram_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def get_active_technical_alerts() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT alert_id, telegram_id, symbol, condition_type, interval, threshold,
                   is_active, created_at, triggered_at
            FROM technical_alerts WHERE is_active = 1
            ORDER BY alert_id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_technical_alert(*, alert_id: int) -> None:
    from datetime import datetime as dt

    init_db()
    now = dt.utcnow().isoformat() + "Z"
    with _connect() as conn:
        conn.execute(
            """
            UPDATE technical_alerts
            SET is_active = 0, triggered_at = COALESCE(triggered_at, ?)
            WHERE alert_id = ?
            """,
            (now, int(alert_id)),
        )
        conn.commit()


# --- Referrals ---


def _generate_referral_code() -> str:
    return secrets.token_hex(4).upper()


def ensure_referral_code(telegram_id: int) -> str:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT referral_code FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row and row["referral_code"]:
            return str(row["referral_code"])
        code = _generate_referral_code()
        for _ in range(10):
            exists = conn.execute(
                "SELECT 1 FROM users WHERE referral_code = ?",
                (code,),
            ).fetchone()
            if not exists:
                break
            code = _generate_referral_code()
        conn.execute(
            "UPDATE users SET referral_code = ? WHERE telegram_id = ?",
            (code, telegram_id),
        )
        conn.commit()
        return code


def apply_referral_code(
    telegram_id: int, code: str
) -> tuple[bool, str, Optional[int]]:
    init_db()
    code = (code or "").upper().strip()
    if not code:
        return False, "Invalid referral code.", None
    with _connect() as conn:
        referrer = conn.execute(
            "SELECT telegram_id FROM users WHERE referral_code = ?",
            (code,),
        ).fetchone()
        if not referrer:
            return False, "Referral code not found.", None
        ref_id = int(referrer["telegram_id"])
        if ref_id == telegram_id:
            return False, "You cannot use your own referral code.", None
        user = conn.execute(
            "SELECT referred_by FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if user and user["referred_by"]:
            return False, "You already used a referral code.", None
        conn.execute(
            "UPDATE users SET referred_by = ? WHERE telegram_id = ?",
            (ref_id, telegram_id),
        )
        conn.commit()

    _grant_referral_rewards(referee_id=telegram_id, referrer_id=ref_id)
    referee_days = REFERRAL_REFEREE_BONUS_DAYS
    referrer_days = REFERRAL_REFERRER_BONUS_DAYS
    tier_label = REFERRAL_REFEREE_TIER.upper()
    return (
        True,
        f"Referral applied! You got {referee_days} days of {tier_label}. "
        f"Your referrer received +{referrer_days} days.",
        ref_id,
    )


def _grant_referral_rewards(*, referee_id: int, referrer_id: int) -> None:
    referee_tier = REFERRAL_REFEREE_TIER if REFERRAL_REFEREE_TIER in VALID_TIERS else "pro"
    if REFERRAL_REFEREE_BONUS_DAYS > 0:
        if get_tier(referee_id) == "free":
            activate_paid_tier(referee_id, referee_tier, days=REFERRAL_REFEREE_BONUS_DAYS)
        else:
            extend_subscription_days(referee_id, REFERRAL_REFEREE_BONUS_DAYS)
    if REFERRAL_REFERRER_BONUS_DAYS > 0:
        if get_tier(referrer_id) == "free":
            activate_paid_tier(referrer_id, "pro", days=REFERRAL_REFERRER_BONUS_DAYS)
        else:
            extend_subscription_days(referrer_id, REFERRAL_REFERRER_BONUS_DAYS)


def count_referrals(telegram_id: int) -> int:
    init_db()
    with _connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by = ?",
                (telegram_id,),
            ).fetchone()[0]
        )


# --- Admin stats ---


def get_admin_stats() -> dict:
    init_db()
    with _connect() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        tiers = conn.execute(
            "SELECT tier, COUNT(*) AS cnt FROM users GROUP BY tier"
        ).fetchall()
        active_price = conn.execute(
            "SELECT COUNT(*) FROM price_alerts WHERE is_active = 1"
        ).fetchone()[0]
        active_tech = conn.execute(
            "SELECT COUNT(*) FROM technical_alerts WHERE is_active = 1"
        ).fetchone()[0]
        paid_orders = conn.execute(
            """
            SELECT COUNT(*) FROM payment_orders
            WHERE payment_status IN ('finished','confirmed','paid','complete','completed')
            """
        ).fetchone()[0]
        journal_entries = conn.execute(
            "SELECT COUNT(*) FROM trade_journal_entries"
        ).fetchone()[0]
    return {
        "total_users": int(total_users),
        "tiers": {r["tier"]: int(r["cnt"]) for r in tiers},
        "active_price_alerts": int(active_price),
        "active_technical_alerts": int(active_tech),
        "paid_orders": int(paid_orders),
        "journal_entries": int(journal_entries),
    }


def admin_set_user_tier(telegram_id: int, tier: str, *, days: Optional[int] = None) -> None:
    """Admin: set any user's tier. Paid tiers get expiry unless days=0 (lifetime)."""
    if tier not in VALID_TIERS:
        raise ValueError(f"tier must be one of {VALID_TIERS}")
    if tier == "free":
        set_tier(telegram_id, "free", expires_at=None)
        return
    if days == 0:
        set_tier(telegram_id, tier, expires_at=None)
    elif days is not None:
        activate_paid_tier(telegram_id, tier, days=days)
    else:
        activate_paid_tier(telegram_id, tier)
