"""Post an embed through a Discord webhook. Used for the weekly feed when the
bot has no token yet (webhook-only mode) or as the fallback destination when
/feed on was never run in a channel."""

from __future__ import annotations

import logging
from typing import Final

import aiohttp
import discord

LOG: Final = logging.getLogger("hobbybot.webhook")

# Shown as the author of webhook posts, so they are not mistaken for the
# channel's other webhook consumers (the outings digest posts as Adventure Bot).
USERNAME: Final[str] = "Hobby Bot"


async def post_embed(url: str, embed: discord.Embed, username: str = USERNAME) -> bool:
    """Returns True on success. Failures are logged without the URL."""
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            hook = discord.Webhook.from_url(url, session=session)
            await hook.send(embed=embed, username=username)
        return True
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as exc:
        LOG.error("webhook post failed: %s", exc.__class__.__name__)
        return False
