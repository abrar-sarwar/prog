"""Persistent top-N leaderboard channel embed.

The leaderboard message in each guild's configured leaderboard channel is a
single embed that prog keeps in sync with the current top N. There is no
schedule - updates are driven by ``xp_grant`` events.

How it stays in sync
--------------------
1. On startup, ``on_ready`` populates an in-memory cache of the current
   top-N per guild (one query per guild that has a leaderboard channel set).
   This prevents a spurious "everything changed" edit on the first event
   after restart.
2. After every successful XP grant, ``cogs.message_xp`` and ``cogs.voice_xp``
   dispatch ``xp_grant(guild_id)``. The listener here re-queries the top N
   and compares it (order-sensitively) to the cache. If they differ, it
   schedules a debounced edit (``LEADERBOARD_DEBOUNCE_SECONDS``); further
   events in the debounce window reset the timer.
3. A safety poll (``LEADERBOARD_SAFETY_POLL_MINUTES``) runs every N minutes
   and force-re-checks every configured guild, in case any dispatch was
   missed (bot restart between dispatch and edit, dropped event, etc.).
4. ``/force-leaderboard-update`` bypasses the cache comparison entirely and
   pushes a fresh edit.

Top-of-leaderboard role (``role_rewards.level == 0``) is reassigned every
time the top-N actually changes.

This module deliberately holds NO Saturday-noon scheduler and no freeze
date - the previous spec called for those, but the current spec is
change-driven.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.constants import (
    LEADERBOARD_DEBOUNCE_SECONDS,
    LEADERBOARD_SAFETY_POLL_MINUTES,
    LEADERBOARD_TOP_N,
)
from db import crud
from db.engine import get_session_factory
from db.models import User

log = logging.getLogger(__name__)


# Cache key: an ordered tuple of (user_id, level) per position. Comparison is
# order-sensitive, so a position swap within the top N counts as a change.
# We deliberately do NOT include XP - if XP changed but level + ordering did
# not, the rendered embed is byte-identical and there's no point editing.
CacheKey = tuple[tuple[int, int], ...]


def _make_cache_key(top_users: Sequence[User]) -> CacheKey:
    """Build the comparable cache key from a list of top-N user rows."""
    return tuple((u.user_id, u.level) for u in top_users)


def _build_embed(guild: discord.Guild, top_users: Sequence[User]) -> discord.Embed:
    """Render the persistent leaderboard embed.

    Format: ``**#N** · <display name> · Level X``. Display name avoids
    pinging members on every edit (a mention would). No XP is shown.
    """
    lines: list[str] = []
    for i, row in enumerate(top_users):
        rank = i + 1
        member = guild.get_member(row.user_id)
        name = member.display_name if member is not None else f"Unknown ({row.user_id})"
        lines.append(f"**#{rank}** · {name} · Level {row.level}")
    description = "\n".join(lines) if lines else "_no XP earned yet_"

    embed = discord.Embed(
        title=f"🏆 {guild.name} Leaderboard",
        description=description,
        color=discord.Color.gold(),
        timestamp=datetime.now(tz=timezone.utc),
    )
    return embed


class LeaderboardChannel(commands.Cog):
    """Persistent leaderboard updater + ``/force-leaderboard-update``."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # guild_id -> last-rendered cache key. Populated on startup, mutated
        # whenever we push an edit.
        self._cache: dict[int, CacheKey] = {}
        # guild_id -> pending debounce task. Cancelled and replaced when a
        # new event arrives within the debounce window.
        self._debounce_tasks: dict[int, asyncio.Task[None]] = {}
        self.safety_poll.start()

    def cog_unload(self) -> None:
        """Cancel the safety poll and any pending debounce timers."""
        self.safety_poll.cancel()
        for task in self._debounce_tasks.values():
            if not task.done():
                task.cancel()
        self._debounce_tasks.clear()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Convert command errors into clean ephemeral replies."""
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You need the **Manage Server** permission for this."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command here."
        else:
            log.exception("leaderboard cog command error: %s", error)
            msg = "Something went wrong - check the bot logs."
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Startup: populate cache so first event after restart isn't a false
    # positive.
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Populate the in-memory cache from the DB on each ready."""
        async with get_session_factory()() as session:
            configs = await crud.list_guild_configs_with_leaderboard_channel(session)
            for cfg in configs:
                top = await crud.get_top_users(
                    session, cfg.guild_id, limit=LEADERBOARD_TOP_N
                )
                self._cache[cfg.guild_id] = _make_cache_key(top)
        log.info(
            "leaderboard cache populated on ready: %d guild(s)", len(self._cache)
        )

    # ------------------------------------------------------------------
    # Event-driven path: xp_grant -> debounce -> compare -> maybe edit
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_xp_grant(self, guild_id: int) -> None:
        """Schedule a debounced cache-comparison for this guild."""
        log.info("leaderboard update triggered for guild %s", guild_id)
        self._schedule_debounced(guild_id)

    def _schedule_debounced(self, guild_id: int) -> None:
        """Cancel any pending debounce for ``guild_id`` and arm a new one."""
        existing = self._debounce_tasks.get(guild_id)
        if existing is not None and not existing.done():
            log.info(
                "leaderboard debounce: cancelling pending timer for guild %s",
                guild_id,
            )
            existing.cancel()
        log.info(
            "leaderboard debounce: arming %.1fs timer for guild %s",
            LEADERBOARD_DEBOUNCE_SECONDS,
            guild_id,
        )
        self._debounce_tasks[guild_id] = asyncio.create_task(
            self._debounce_then_check(guild_id)
        )

    async def _debounce_then_check(self, guild_id: int) -> None:
        """Sleep, then run the cache comparison. Cancellation aborts cleanly."""
        try:
            await asyncio.sleep(LEADERBOARD_DEBOUNCE_SECONDS)
            log.info(
                "leaderboard debounce: timer fired for guild %s, running check",
                guild_id,
            )
            await self._check_and_maybe_update(guild_id, force=False)
        except asyncio.CancelledError:
            # A newer event arrived inside the debounce window; that newer
            # event's task will run the check instead.
            log.info(
                "leaderboard debounce: task for guild %s cancelled (newer event)",
                guild_id,
            )
        except Exception as exc:
            log.exception(
                "leaderboard debounce check failed for guild %s: %s", guild_id, exc
            )

    async def _check_and_maybe_update(
        self, guild_id: int, *, force: bool
    ) -> None:
        """Re-query the top N for this guild and edit Discord if it differs
        from the cached state (or unconditionally if ``force``)."""
        async with get_session_factory()() as session:
            top = list(
                await crud.get_top_users(session, guild_id, limit=LEADERBOARD_TOP_N)
            )
        new_key = _make_cache_key(top)
        cached = self._cache.get(guild_id)
        log.info(
            "leaderboard check guild=%s force=%s cached=%s fresh=%s",
            guild_id,
            force,
            cached,
            new_key,
        )
        if not force and cached == new_key:
            log.info(
                "leaderboard check guild=%s: cached == fresh, no update needed",
                guild_id,
            )
            return  # No visible change - skip the Discord edit entirely.

        log.info(
            "leaderboard check guild=%s: change detected, pushing edit",
            guild_id,
        )
        # Update the cache BEFORE pushing so a concurrent event doesn't
        # double-fire on the same delta if the API call is slow.
        self._cache[guild_id] = new_key
        await self._push_update(guild_id, top)

    async def _push_update(
        self, guild_id: int, top_users: Sequence[User]
    ) -> None:
        """Edit (or send) the leaderboard message and reassign the top-1 role."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            log.warning(
                "leaderboard update: guild %s has cached state but bot is not in it",
                guild_id,
            )
            return

        async with get_session_factory()() as session:
            config = await crud.get_guild_config(session, guild_id)
        if config is None or config.leaderboard_channel_id is None:
            return  # Leaderboard channel got unset between schedule and push.

        channel = guild.get_channel(config.leaderboard_channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning(
                "leaderboard channel %s for guild %s missing or not a text channel",
                config.leaderboard_channel_id,
                guild_id,
            )
            return

        embed = _build_embed(guild, top_users)
        new_message = await self._edit_or_send(
            channel, config.leaderboard_message_id, embed
        )
        if new_message is None:
            return

        if new_message.id != config.leaderboard_message_id:
            async with get_session_factory()() as session:
                await crud.set_leaderboard_message(session, guild_id, new_message.id)
                await session.commit()

        await self._reassign_top_role(guild, top_users)

    async def _edit_or_send(
        self,
        channel: discord.TextChannel,
        message_id: int | None,
        embed: discord.Embed,
    ) -> discord.Message | None:
        """Edit the existing leaderboard message if it still exists; otherwise
        send a fresh one. Returns the resulting message, or None on failure."""
        if message_id is not None:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed)
                return msg
            except discord.NotFound:
                log.info(
                    "stored leaderboard message %s not found in channel %s; "
                    "sending a new one",
                    message_id,
                    channel.id,
                )
            except discord.Forbidden:
                log.warning(
                    "missing permission to edit leaderboard message in channel %s",
                    channel.id,
                )
                return None
            except discord.HTTPException as exc:
                log.warning("HTTP error editing leaderboard message: %s", exc)
                return None
        try:
            return await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning(
                "missing permission to send leaderboard in channel %s", channel.id
            )
            return None
        except discord.HTTPException as exc:
            log.warning("HTTP error sending leaderboard: %s", exc)
            return None

    async def _reassign_top_role(
        self, guild: discord.Guild, top_users: Sequence[User]
    ) -> None:
        """Give the level-0 role to the current #1 and strip it from anyone else."""
        async with get_session_factory()() as session:
            top_role_row = await crud.get_role_reward(session, guild.id, 0)
        if top_role_row is None:
            return  # No top-of-leaderboard role configured.

        role = guild.get_role(top_role_row.role_id)
        if role is None:
            log.warning(
                "top-of-leaderboard role %s configured for guild %s but not found",
                top_role_row.role_id,
                guild.id,
            )
            return

        top_user_id: int | None = top_users[0].user_id if top_users else None

        for member in list(role.members):
            if member.id == top_user_id:
                continue
            try:
                await member.remove_roles(
                    role, reason="prog: leaderboard top-1 reassignment"
                )
            except discord.Forbidden:
                log.warning(
                    "missing permission to remove top-1 role from %s in guild %s",
                    member.id,
                    guild.id,
                )
            except discord.HTTPException as exc:
                log.warning(
                    "HTTP error removing top-1 role from %s: %s", member.id, exc
                )

        if top_user_id is None:
            return
        top_member = guild.get_member(top_user_id)
        if top_member is None:
            log.info(
                "top user %s for guild %s is no longer a member; "
                "leaving the role vacant",
                top_user_id,
                guild.id,
            )
            return
        if role in top_member.roles:
            return
        try:
            await top_member.add_roles(
                role, reason="prog: leaderboard top-1 reassignment"
            )
        except discord.Forbidden:
            log.warning(
                "missing permission to add top-1 role to %s in guild %s",
                top_member.id,
                guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error adding top-1 role: %s", exc)

    # ------------------------------------------------------------------
    # Safety poll: full re-check every N minutes
    # ------------------------------------------------------------------

    @tasks.loop(minutes=LEADERBOARD_SAFETY_POLL_MINUTES)
    async def safety_poll(self) -> None:
        """Periodic full re-check across every guild with a leaderboard channel."""
        async with get_session_factory()() as session:
            configs = await crud.list_guild_configs_with_leaderboard_channel(session)
        for cfg in configs:
            try:
                await self._check_and_maybe_update(cfg.guild_id, force=False)
            except Exception as exc:
                log.exception(
                    "safety_poll: leaderboard check failed for guild %s: %s",
                    cfg.guild_id,
                    exc,
                )

    @safety_poll.before_loop
    async def _before_safety_poll(self) -> None:
        """Hold the loop until the gateway handshake completes."""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # /force-leaderboard-update
    # ------------------------------------------------------------------

    @app_commands.command(
        name="force-leaderboard-update",
        description="Force a leaderboard re-evaluation and push right now.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def force_leaderboard_update(
        self, interaction: discord.Interaction
    ) -> None:
        """Bypass the cache comparison and push a fresh edit."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._check_and_maybe_update(interaction.guild.id, force=True)
        await interaction.followup.send(
            "Leaderboard re-evaluated and pushed.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(LeaderboardChannel(bot))
