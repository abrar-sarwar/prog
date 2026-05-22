"""Onboarding: progsuvian base role + ensuring a users row exists.

The free function :func:`ensure_member_initialized` is the public API. It is
called from three places (on_member_join, on_message, voice XP loop) so any
event the user generates results in:

1. a ``users`` row existing for ``(guild_id, user_id)`` (idempotent), and
2. the configured ``progsuvian`` role being on the member if one is set.

If no ``progsuvian_role_id`` is configured for the guild, role assignment is
skipped silently; the DB row is still created.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


async def ensure_member_initialized(member: discord.Member) -> None:
    """Idempotently ensure the bot's state for this member is up to date.

    Creates the ``users`` row if missing and, if the guild has a configured
    progsuvian role, assigns it to the member. Bots are skipped entirely.
    """
    if member.bot:
        return

    guild_id = member.guild.id
    progsuvian_role_id: int | None = None
    session_factory = get_session_factory()
    async with session_factory() as session:
        await crud.get_or_create_user(session, guild_id, member.id)
        config = await crud.get_guild_config(session, guild_id)
        if config is not None:
            progsuvian_role_id = config.progsuvian_role_id
        await session.commit()

    if progsuvian_role_id is None:
        return

    role = member.guild.get_role(progsuvian_role_id)
    if role is None:
        log.warning(
            "progsuvian role %s configured for guild %s but not found",
            progsuvian_role_id,
            guild_id,
        )
        return
    if role in member.roles:
        return
    try:
        await member.add_roles(role, reason="prog: initial progsuvian assignment")
    except discord.Forbidden:
        log.warning(
            "missing permission to assign progsuvian role in guild %s "
            "(check that prog's role is above progsuvian in the role list)",
            guild_id,
        )
    except discord.HTTPException as exc:
        log.warning("HTTP error assigning progsuvian role in guild %s: %s", guild_id, exc)


class Onboarding(commands.Cog):
    """Listens for new members and warns about misconfigured guilds at startup."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_warned = False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Initialize a member as soon as they join the guild."""
        await ensure_member_initialized(member)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """On first ready, scan all guilds and warn if any lack a progsuvian
        role. ``on_ready`` can fire multiple times (reconnects); this only runs
        once per process lifetime."""
        if self._startup_warned:
            return
        self._startup_warned = True

        session_factory = get_session_factory()
        async with session_factory() as session:
            for guild in self.bot.guilds:
                config = await crud.get_guild_config(session, guild.id)
                if config is None or config.progsuvian_role_id is None:
                    log.warning(
                        "guild %s (%s) has no progsuvian_role_id configured; "
                        "run /setup-progsuvian to enable the base role",
                        guild.id,
                        guild.name,
                    )


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Onboarding(bot))
