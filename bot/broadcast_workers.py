"""Scheduled auto-posting to Free / Pro / Elite channels."""
from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from services.auto_signals import build_elite_channel_post, build_free_market_update, build_pro_channel_post
from services.channel_access import elite_channel_id, free_channel_id, pro_channel_id
from services.channel_broadcast import post_to_channel

logger = logging.getLogger("bot.broadcast_workers")

_free_rotation = 0


async def free_channel_broadcast_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rotate summary → volatility → news on the free public channel."""
    global _free_rotation
    cid = free_channel_id()
    if not cid:
        return

    modes = ("summary", "volatility", "news")
    mode = modes[_free_rotation % len(modes)]
    _free_rotation += 1

    try:
        text = await build_free_market_update(mode)
        await post_to_channel(context.bot, cid, text, label=f"free:{mode}")
    except Exception as e:
        logger.warning("free_channel_broadcast failed: %s", e)


async def pro_channel_broadcast_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto signals + market update to Pro private channel."""
    cid = pro_channel_id()
    if not cid:
        return
    try:
        text = await build_pro_channel_post()
        await post_to_channel(context.bot, cid, text, label="pro")
    except Exception as e:
        logger.warning("pro_channel_broadcast failed: %s", e)


async def elite_channel_broadcast_worker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elite signals + AI brief to Elite private channel."""
    cid = elite_channel_id()
    if not cid:
        return
    try:
        text = await build_elite_channel_post()
        await post_to_channel(context.bot, cid, text, label="elite")
    except Exception as e:
        logger.warning("elite_channel_broadcast failed: %s", e)
