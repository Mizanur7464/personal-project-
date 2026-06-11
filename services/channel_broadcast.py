"""Post automated content to Telegram channels."""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

logger = logging.getLogger("bot.broadcast")

ChatId = Union[int, str]
MAX_MSG = 4096


async def post_to_channel(
    bot: Any,
    chat_id: Optional[ChatId],
    text: str,
    *,
    label: str = "channel",
) -> bool:
    """Send a message to a channel. Splits if longer than Telegram limit."""
    if not chat_id:
        logger.info("Skip broadcast %s: channel not configured", label)
        return False
    if not text or not text.strip():
        return False

    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        chunks.append(remaining[:MAX_MSG])
        remaining = remaining[MAX_MSG:]

    try:
        for chunk in chunks:
            await bot.send_message(chat_id=chat_id, text=chunk)
        logger.info("Broadcast sent to %s (%s)", label, chat_id)
        return True
    except Exception as e:
        logger.warning("Broadcast failed %s chat=%s: %s", label, chat_id, e)
        return False
