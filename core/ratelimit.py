"""Simple in-memory per-user rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from core.config import RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW_SEC

if TYPE_CHECKING:
    from telegram import Update

_buckets: dict[tuple[int, str], list[float]] = defaultdict(list)

_RATE_MSG = "Too many requests. Please wait a minute and try again."


def is_rate_limited(
    user_id: int,
    action: str = "default",
    *,
    max_calls: int | None = None,
    window_sec: float | None = None,
) -> bool:
    """Return True if the user exceeded the rate limit."""
    max_calls = max_calls if max_calls is not None else RATE_LIMIT_CALLS
    window_sec = window_sec if window_sec is not None else RATE_LIMIT_WINDOW_SEC
    now = time.monotonic()
    key = (user_id, action)
    times = _buckets[key]
    times[:] = [t for t in times if now - t < window_sec]
    if len(times) >= max_calls:
        return True
    times.append(now)
    return False


async def reply_if_rate_limited(update: "Update", *, action: str = "default") -> bool:
    """Reply and return True when the user is rate-limited."""
    uid = update.effective_user.id if update.effective_user else 0
    if not uid or not is_rate_limited(uid, action):
        return False
    msg = update.effective_message or update.message
    if msg:
        await msg.reply_text(_RATE_MSG)
    return True
