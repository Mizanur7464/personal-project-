"""Admin helpers."""
from __future__ import annotations

import os


def admin_telegram_ids() -> set[int]:
    raw = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            pass
    return out


def is_admin(user_id: int) -> bool:
    return user_id in admin_telegram_ids()
