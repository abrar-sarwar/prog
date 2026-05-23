"""Voice XP cog.

Every 60 seconds, scans all voice channels in all guilds and grants base XP
to members that satisfy:

* not a bot
* sharing the channel with at least one other non-bot human
* not self-muted, server-muted, self-deafened, or server-deafened
* in a channel that is not in the guild's ``xp_blacklist``

Channel and role multipliers are then applied. Level-ups dispatch the
``level_change`` event so the rewards cog can update ladder roles, and post
a level-up embed in the configured level-up channel (or the guild's
system channel as a fallback; if neither is available, the announcement is
skipped silently per spec).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from cogs.onboarding import ensure_member_initialized
from core.constants import (
    VOICE_XP_PER_TICK,
    VOICE_XP_TICK_SECONDS,
    level_up_message,
)
from core.multipliers import compute_final_xp
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


def _is_voice_eligible(voice_state: discord.VoiceState | None) -> bool:
    """Return True iff the voice state shows active, undeafened participation."""
    if voice_state is None:
        return False
    if voice_state.self_mute or voice_state.mute:
        return False
    if voice_state.self_deaf or voice_state.deaf:
        return False
    return True


class VoiceXP(commands.Cog):
    """Grants voice XP every minute to eligible members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        """Cancel the background loop when the cog is unloaded."""
        self.tick.cancel()

    @tasks.loop(seconds=VOICE_XP_TICK_SECONDS)
    async def tick(self) -> None:
        """Scan every guild's voice channels and grant XP."""
        for guild in self.bot.guilds:
            try:
                await self._process_guild(guild)
            except Exception as exc:
                # One guild's failure must not stop the rest of the tick.
                log.exception("voice tick failed for guild %s: %s", guild.id, exc)

    @tick.before_loop
    async def _before_loop(self) -> None:
        """Hold the loop until the gateway handshake completes."""
        await self.bot.wait_until_ready()

    async def _process_guild(self, guild: discord.Guild) -> None:
        """Run the voice tick for a single guild."""
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(session, guild.id)
            blacklist = {int(c) for c in config.xp_blacklist}
            level_up_channel_id = config.level_up_channel_id
            await session.commit()

        for vc in guild.voice_channels:
            if vc.id in blacklist:
                continue
            non_bots = [m for m in vc.members if not m.bot]
            if len(non_bots) < 2:
                continue
            for member in non_bots:
                if not _is_voice_eligible(member.voice):
                    continue
                try:
                    await self._grant_one(member, vc, level_up_channel_id)
                except Exception as exc:
                    log.exception(
                        "voice grant failed for member %s in guild %s: %s",
                        member.id,
                        guild.id,
                        exc,
                    )

    async def _grant_one(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel,
        level_up_channel_id: int | None,
    ) -> None:
        """Grant voice XP to one eligible member and react to any level-up."""
        await ensure_member_initialized(member)

        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(session, member.guild.id)
            role_ids = [r.id for r in member.roles]
            final_xp = compute_final_xp(
                VOICE_XP_PER_TICK, channel.id, role_ids, config
            )
            change = await crud.add_voice_xp(
                session, member.guild.id, member.id, final_xp
            )
            await session.commit()

        log.info(
            "voice xp: guild=%s user=%s channel=%s base=%d final=%d total=%d level=%d->%d",
            member.guild.id,
            member.id,
            channel.id,
            VOICE_XP_PER_TICK,
            final_xp,
            change.user.xp,
            change.old_level,
            change.new_level,
        )

        # Every successful grant invalidates the leaderboard cache. The
        # leaderboard cog debounces these and only edits Discord when the
        # rendered top-N actually differs.
        self.bot.dispatch("xp_grant", member.guild.id)

        if change.leveled_up:
            await self._announce_level_up(
                member, change.new_level, level_up_channel_id
            )
            self.bot.dispatch(
                "level_change", member, change.old_level, change.new_level
            )

    async def _announce_level_up(
        self,
        member: discord.Member,
        new_level: int,
        level_up_channel_id: int | None,
    ) -> None:
        """Post a level-up message that pings the member.

        Target resolution (per spec section 6):

        1. ``guild_config.level_up_channel_id`` if set
        2. else ``guild.system_channel``
        3. else skip silently

        Only the leveled-up user is pinged - role/@everyone pings are disabled.
        """
        guild = member.guild
        target: discord.abc.Messageable | None = None
        if level_up_channel_id is not None:
            ch = guild.get_channel(level_up_channel_id)
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
                target = ch
        if target is None:
            target = guild.system_channel
        if target is None:
            return

        content = level_up_message(new_level, member.mention)
        try:
            await target.send(
                content=content,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.Forbidden:
            log.warning(
                "missing permission to post voice level-up in guild %s",
                guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error posting voice level-up: %s", exc)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(VoiceXP(bot))
