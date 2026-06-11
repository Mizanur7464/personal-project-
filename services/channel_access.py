"""Private channel one-time invites and kick on subscription expiry."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional, Union

logger = logging.getLogger("bot.channels")

ChatId = Union[int, str]


def _parse_channel_id(env_key: str) -> Optional[ChatId]:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


def pro_channel_id() -> Optional[ChatId]:
    return _parse_channel_id("PRO_CHANNEL_ID")


def elite_channel_id() -> Optional[ChatId]:
    return _parse_channel_id("ELITE_CHANNEL_ID")


def free_channel_id() -> Optional[ChatId]:
    return _parse_channel_id("FREE_CHANNEL_ID")


def channels_configured() -> bool:
    return pro_channel_id() is not None or elite_channel_id() is not None


def channel_id_for_tier(tier: str) -> Optional[ChatId]:
    tier = (tier or "").lower().strip()
    if tier == "pro":
        return pro_channel_id()
    if tier == "elite":
        return elite_channel_id()
    return None


async def create_one_time_invite(
    bot: Any,
    chat_id: ChatId,
    *,
    user_id: int,
) -> Optional[str]:
    """Create a single-use invite link for a private channel."""
    try:
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            name=f"u{user_id}-{int(time.time())}",
        )
        return link.invite_link
    except Exception as e:
        logger.warning("create_invite failed chat=%s user=%s: %s", chat_id, user_id, e)
        return None


async def kick_from_channel(bot: Any, chat_id: ChatId, user_id: int) -> bool:
    """Remove user from a channel (ban + unban so they can rejoin with a new link)."""
    try:
        await bot.ban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            revoke_messages=False,
        )
        await bot.unban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            only_if_banned=True,
        )
        return True
    except Exception as e:
        logger.warning("kick failed chat=%s user=%s: %s", chat_id, user_id, e)
        return False


async def grant_tier_channel_access(
    bot: Any,
    user_id: int,
    tier: str,
) -> bool:
    """
    DM a one-time private channel invite for pro or elite.
    Returns True if a link was sent.
    """
    tier = (tier or "").lower().strip()
    if tier not in ("pro", "elite"):
        return False

    chat_id = channel_id_for_tier(tier)
    if not chat_id:
        logger.info("No channel configured for tier=%s", tier)
        return False

    label = tier.upper()
    url = await create_one_time_invite(bot, chat_id, user_id=user_id)
    if not url:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"Could not create your {label} channel invite right now.\n"
                    "Try /my_channel later or contact support."
                ),
            )
        except Exception:
            pass
        return False

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"🔐 Private {label} channel\n\n"
                f"One-time join link:\n{url}\n\n"
                "⚠️ This link works only once. Do not share it."
            ),
        )
        logger.info("Channel invite sent tier=%s user=%s", tier, user_id)
        return True
    except Exception as e:
        logger.warning("DM invite failed user=%s: %s", user_id, e)
        return False


async def revoke_tier_channel_access(
    bot: Any,
    user_id: int,
    tier: str,
) -> bool:
    """Kick user from the private channel for their expired paid tier."""
    tier = (tier or "").lower().strip()
    chat_id = channel_id_for_tier(tier)
    if not chat_id:
        return False
    ok = await kick_from_channel(bot, chat_id, user_id)
    if ok:
        logger.info("Kicked user=%s from %s channel", user_id, tier)
    return ok


async def revoke_all_paid_channels(bot: Any, user_id: int) -> None:
    """Kick from both Pro and Elite channels (e.g. admin set to free)."""
    await revoke_tier_channel_access(bot, user_id, "elite")
    await revoke_tier_channel_access(bot, user_id, "pro")


async def on_tier_activated(bot: Any, user_id: int, tier: str) -> None:
    """Called after a user gains pro or elite access."""
    await grant_tier_channel_access(bot, user_id, tier)


async def on_tier_revoked(bot: Any, user_id: int, tier: str) -> None:
    """Called when paid access ends."""
    await revoke_tier_channel_access(bot, user_id, tier)
