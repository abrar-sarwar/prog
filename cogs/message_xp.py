"""Message XP cog.

Per-message flow:

1. Skip if author is a bot, the message is a DM, or the content is too short.
2. Look up (or create) the guild config; skip if the channel is blacklisted.
3. Initialize the member (progsuvian role + users row) via the onboarding API.
4. Check the per-user 60s cooldown.
5. Roll base XP, apply channel + role multipliers, persist.
6. If level changed, post a level-up embed.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

import discord
from discord.ext import commands

from cogs.onboarding import ensure_member_initialized
from core.constants import (
    MESSAGE_XP_COOLDOWN_SECONDS,
    MIN_MESSAGE_LENGTH,
    TEXT_XP_MAX,
    TEXT_XP_MIN,
    level_up_message,
)
from core.multipliers import compute_final_xp
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


class MessageXP(commands.Cog):
    """Grants text XP on qualifying messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Award text XP for a qualifying message."""
        if message.author.bot:
            return
        if message.guild is None:
            return
        if not isinstance(message.author, discord.Member):
            return
        if len(message.content) < MIN_MESSAGE_LENGTH:
            return

        guild_id = message.guild.id
        author = message.author
        channel_id = message.channel.id

        # Step 1: make sure the member is initialized (users row + role).
        # This opens and commits its own session.
        await ensure_member_initialized(author)

        # Step 2: blacklist + cooldown check + XP grant in a single transaction.
        now = datetime.now(timezone.utc)
        session_factory = get_session_factory()
        async with session_factory() as session:
            config = await crud.get_or_create_guild_config(session, guild_id)
            blacklist = {int(c) for c in config.xp_blacklist}
            if channel_id in blacklist:
                await session.commit()  # commit the get_or_create
                return

            user = await crud.get_user(session, guild_id, author.id)
            if user is None:
                # Safety net: ensure_member_initialized should have created it.
                log.warning(
                    "user %s missing after ensure_member_initialized; "
                    "skipping XP grant",
                    author.id,
                )
                return

            if (
                user.last_message_at is not None
                and (now - user.last_message_at).total_seconds()
                < MESSAGE_XP_COOLDOWN_SECONDS
            ):
                return

            base_xp = random.randint(TEXT_XP_MIN, TEXT_XP_MAX)
            role_ids = [r.id for r in author.roles]
            final_xp = compute_final_xp(base_xp, channel_id, role_ids, config)
            change = await crud.add_text_xp(
                session, guild_id, author.id, final_xp, now
            )
            level_up_channel_id = config.level_up_channel_id
            await session.commit()

        log.info(
            "text xp: guild=%s user=%s channel=%s base=%d final=%d total=%d level=%d->%d",
            guild_id,
            author.id,
            channel_id,
            base_xp,
            final_xp,
            change.user.xp,
            change.old_level,
            change.new_level,
        )

        # Every successful grant invalidates the leaderboard cache. The
        # leaderboard cog debounces these and only edits Discord when the
        # rendered top-N actually differs.
        self.bot.dispatch("xp_grant", guild_id)

        if change.leveled_up:
            await self._announce_level_up(message, change.new_level, level_up_channel_id)
            # Notify the rewards cog (and anyone else listening) so ladder
            # roles can be reassigned.
            self.bot.dispatch(
                "level_change", author, change.old_level, change.new_level
            )

    async def _announce_level_up(
        self,
        message: discord.Message,
        new_level: int,
        level_up_channel_id: int | None,
    ) -> None:
        """Post a level-up message that pings the member.

        Target resolution: ``guild_config.level_up_channel_id`` if set; else
        the channel the triggering message was sent in. Only the leveled-up
        user is pinged - role/@everyone pings are disabled.
        """
        assert message.guild is not None
        assert isinstance(message.author, discord.Member)
        target: discord.abc.Messageable = message.channel
        if level_up_channel_id is not None:
            configured = message.guild.get_channel(level_up_channel_id)
            if isinstance(
                configured,
                (discord.TextChannel, discord.VoiceChannel, discord.Thread),
            ):
                target = configured

        content = level_up_message(new_level, message.author.mention)
        try:
            await target.send(
                content=content,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.Forbidden:
            log.warning(
                "missing permission to post level-up message in guild %s",
                message.guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error posting level-up: %s", exc)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(MessageXP(bot))
