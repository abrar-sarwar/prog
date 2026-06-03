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

from core.roles import has_dangerous_permission, role_is_restorable
from core.welcome import (
    DEFAULT_WELCOME_MESSAGE,
    INTRO_CHANNEL_FALLBACK,
    render_welcome,
)
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


async def post_welcome_message(member: discord.Member) -> None:
    """Post the configured welcome message for a newly-joined member.

    Opt-in: does nothing unless the guild has a ``welcome_channel_id`` set.
    Resolves the intro-channel mention for the ``{intro_channel}`` placeholder
    (falling back to a plain label when none is configured), renders the
    per-guild message (or the default template), and posts it pinging only the
    joining member -- never ``@everyone``/``@here``. Bots are skipped.
    """
    if member.bot:
        return

    guild = member.guild
    session_factory = get_session_factory()
    async with session_factory() as session:
        config = await crud.get_guild_config(session, guild.id)
        await session.commit()

    if config is None or config.welcome_channel_id is None:
        return

    channel = guild.get_channel(config.welcome_channel_id)
    if not isinstance(
        channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)
    ):
        log.warning(
            "welcome channel %s configured for guild %s but not found or not "
            "text-capable; skipping welcome",
            config.welcome_channel_id,
            guild.id,
        )
        return

    intro_channel = INTRO_CHANNEL_FALLBACK
    if config.welcome_intro_channel_id is not None:
        intro = guild.get_channel(config.welcome_intro_channel_id)
        if intro is not None:
            intro_channel = intro.mention

    template = config.welcome_message or DEFAULT_WELCOME_MESSAGE
    content = render_welcome(
        template,
        user=member.mention,
        server=guild.name,
        intro_channel=intro_channel,
    )
    try:
        await channel.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False
            ),
        )
    except discord.Forbidden:
        log.warning(
            "missing permission to post welcome message in guild %s", guild.id
        )
    except discord.HTTPException as exc:
        log.warning("HTTP error posting welcome message in guild %s: %s", guild.id, exc)


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


def _granted_permission_names(permissions: discord.Permissions) -> list[str]:
    """Return the names of permissions that are granted (True) on ``permissions``."""
    return [name for name, value in permissions if value]


def _is_restorable(role: discord.Role, *, is_above_bot: bool) -> bool:
    """Apply the role-persistence policy to a concrete Discord role."""
    return role_is_restorable(
        is_default=role.is_default(),
        is_managed=role.managed,
        has_dangerous_perm=has_dangerous_permission(
            _granted_permission_names(role.permissions)
        ),
        is_above_bot=is_above_bot,
    )


async def snapshot_member_roles(member: discord.Member) -> None:
    """Record the roles a departing member had, for restore on rejoin.

    Skips bots. Excludes ``@everyone``, managed/integration/booster roles, and
    roles carrying dangerous permissions (see :mod:`core.roles`). Position
    relative to the bot is irrelevant on the way out, so it is not considered
    here -- only at restore time.
    """
    if member.bot:
        return
    guild = member.guild
    ids = [
        role.id
        for role in member.roles
        if _is_restorable(role, is_above_bot=False)
    ]
    async with get_session_factory()() as session:
        await crud.set_saved_roles(session, guild.id, member.id, ids)
        await session.commit()
    log.info(
        "snapshotted %d role(s) for member %s leaving guild %s",
        len(ids),
        member.id,
        guild.id,
    )


async def restore_member_roles(member: discord.Member) -> None:
    """Re-apply a rejoining member's saved roles.

    Skips bots and no-ops when nothing was saved. Re-filters every saved role
    against the current policy (a role may have been deleted, become managed,
    had its permissions elevated, or moved above prog's role while the member
    was away) and adds the survivors in a single API call. The saved snapshot
    is left intact -- it is overwritten on the next departure.
    """
    if member.bot:
        return
    guild = member.guild
    async with get_session_factory()() as session:
        user = await crud.get_user(session, guild.id, member.id)
        saved = list(user.saved_role_ids) if user is not None else []
        await session.commit()
    if not saved:
        return

    me = guild.me
    bot_top = me.top_role.position if me is not None else 0
    to_add: list[discord.Role] = []
    for rid in saved:
        role = guild.get_role(int(rid))
        if role is None or role in member.roles:
            continue
        if _is_restorable(role, is_above_bot=role.position >= bot_top):
            to_add.append(role)
    if not to_add:
        return

    try:
        await member.add_roles(*to_add, reason="prog: restore roles on rejoin")
    except discord.Forbidden:
        log.warning(
            "missing permission to restore roles in guild %s (check prog's "
            "role is above the roles being restored)",
            guild.id,
        )
    except discord.HTTPException as exc:
        log.warning("HTTP error restoring roles in guild %s: %s", guild.id, exc)
    else:
        log.info(
            "restored %d role(s) for member %s rejoining guild %s",
            len(to_add),
            member.id,
            guild.id,
        )


class Onboarding(commands.Cog):
    """Listens for new members and warns about misconfigured guilds at startup."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_warned = False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Initialize a member as soon as they join the guild, then welcome them.

        Initialization (progsuvian role + level-0 users row) runs first and is
        independent of the welcome message, which is opt-in per guild. Finally,
        any roles the member had on a previous departure are restored.
        """
        await ensure_member_initialized(member)
        await post_welcome_message(member)
        await restore_member_roles(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Snapshot a departing member's roles so they can be restored on rejoin."""
        await snapshot_member_roles(member)

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
