"""Weekly leaderboard channel updater.

A 1-minute polling loop wakes every minute and, if it is Saturday between
12:00 and 12:01 PM ``America/New_York``, runs the weekly update across every
guild that has a leaderboard channel configured. ``zoneinfo`` handles the
EDT/EST switch automatically.

Each update:

1. Edits the message at ``leaderboard_message_id`` (or sends a new one if
   that is null or the message has been deleted).
2. Reassigns the role registered at ``role_rewards.level == 0`` to whoever
   is currently #1, removing it from anyone else who has it.

Freeze logic (``FREEZE_DATE = 2026-08-15``):

* ``today < FREEZE_DATE``  → update with ``"Final standings: August 15, 2026"`` footer
* ``today == FREEZE_DATE`` → run **one final update** with ``"🏆 Final standings — leaderboard frozen"`` footer
* ``today > FREEZE_DATE``  → do nothing

XP itself never freezes - only this public post does.

``/force-leaderboard-update`` bypasses the day/time check but still respects
the freeze date.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.constants import (
    FREEZE_DATE,
    LEADERBOARD_HOUR,
    LEADERBOARD_TIMEZONE,
    LEADERBOARD_WEEKDAY,
)
from db import crud
from db.engine import get_session_factory
from db.models import User

log = logging.getLogger(__name__)

_EASTERN = ZoneInfo(LEADERBOARD_TIMEZONE)
_FOOTER_PRE_FREEZE = "Final standings: August 15, 2026"
_FOOTER_FROZEN = "🏆 Final standings — leaderboard frozen"


def _today_eastern() -> date:
    """Return today's date in the leaderboard timezone."""
    return datetime.now(tz=_EASTERN).date()


def _is_after_freeze(today: date) -> bool:
    """True when today is strictly after the freeze date (do-nothing branch)."""
    return today > FREEZE_DATE


def _build_embed(
    guild: discord.Guild, top_users: Sequence[User], today: date
) -> discord.Embed:
    """Render one guild's leaderboard embed for the given day."""
    lines: list[str] = []
    for i, row in enumerate(top_users):
        rank = i + 1
        member = guild.get_member(row.user_id)
        name = member.display_name if member is not None else f"Unknown ({row.user_id})"
        lines.append(
            f"**#{rank}** · {name} — level {row.level} ({row.xp:,} XP)"
        )
    description = "\n".join(lines) if lines else "_no XP earned yet_"

    embed = discord.Embed(
        title=f"🏆 {guild.name} Leaderboard",
        description=description,
        color=discord.Color.gold(),
        timestamp=datetime.now(tz=timezone.utc),
    )
    if today >= FREEZE_DATE:
        embed.set_footer(text=_FOOTER_FROZEN)
    else:
        embed.set_footer(text=_FOOTER_PRE_FREEZE)
    return embed


class LeaderboardChannel(commands.Cog):
    """Background updater + ``/force-leaderboard-update`` command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.saturday_tick.start()

    def cog_unload(self) -> None:
        """Cancel the background loop when the cog is unloaded."""
        self.saturday_tick.cancel()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Per-cog error handler for the force-update slash command."""
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
    # Scheduling
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def saturday_tick(self) -> None:
        """Once-a-minute schedule check. Fires update at Saturday 12:00 ET."""
        now = datetime.now(tz=_EASTERN)
        if (
            now.weekday() != LEADERBOARD_WEEKDAY
            or now.hour != LEADERBOARD_HOUR
            or now.minute != 0
        ):
            return
        log.info(
            "Saturday-noon trigger fired (%s); running weekly leaderboard update",
            now.isoformat(),
        )
        await self._run_update(force=False)

    @saturday_tick.before_loop
    async def _before_loop(self) -> None:
        """Hold the loop until the gateway handshake completes."""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Update logic (used by both scheduler and /force-leaderboard-update)
    # ------------------------------------------------------------------

    async def _run_update(self, *, force: bool) -> tuple[bool, int]:
        """Run the update for every configured guild.

        Returns ``(updated, guild_count)`` - ``updated`` is False if the
        freeze date has passed (nothing to do), ``guild_count`` is how many
        guilds were processed (only meaningful when ``updated`` is True).
        """
        today = _today_eastern()
        if _is_after_freeze(today):
            log.info(
                "leaderboard %supdate skipped: today %s > freeze %s (frozen)",
                "force-" if force else "",
                today,
                FREEZE_DATE,
            )
            return (False, 0)

        async with get_session_factory()() as session:
            configs = await crud.list_guild_configs_with_leaderboard_channel(session)
            snapshots = [
                (c.guild_id, c.leaderboard_channel_id, c.leaderboard_message_id)
                for c in configs
            ]
            await session.commit()

        processed = 0
        for guild_id, channel_id, message_id in snapshots:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                log.warning("guild %s has leaderboard config but bot is not in it", guild_id)
                continue
            if channel_id is None:
                continue  # safety; the query already filters this out
            try:
                await self._update_guild(guild, channel_id, message_id, today)
                processed += 1
            except Exception as exc:
                log.exception(
                    "leaderboard update failed for guild %s: %s",
                    guild_id,
                    exc,
                )
        return (True, processed)

    async def _update_guild(
        self,
        guild: discord.Guild,
        channel_id: int,
        message_id: int | None,
        today: date,
    ) -> None:
        """Update one guild's leaderboard message and top-1 role."""
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning(
                "leaderboard channel %s for guild %s is missing or not a text channel",
                channel_id,
                guild.id,
            )
            return

        # Fetch top 10 and total count
        async with get_session_factory()() as session:
            top_users = list(await crud.get_top_users(session, guild.id, limit=10))
            await session.commit()

        embed = _build_embed(guild, top_users, today)
        new_message = await self._edit_or_send(channel, message_id, embed)
        if new_message is None:
            return

        # Persist a new message ID if we created the message instead of editing.
        if new_message.id != message_id:
            async with get_session_factory()() as session:
                await crud.set_leaderboard_message(session, guild.id, new_message.id)
                await session.commit()

        # Reassign the top-of-leaderboard role.
        await self._reassign_top_role(guild, top_users)

    async def _edit_or_send(
        self,
        channel: discord.TextChannel,
        message_id: int | None,
        embed: discord.Embed,
    ) -> discord.Message | None:
        """Edit the existing leaderboard message in ``channel`` if it still
        exists; otherwise send a fresh one. Returns the resulting message, or
        None on failure."""
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
            await session.commit()
        if top_role_row is None:
            return  # No top-of-leaderboard role configured for this guild.

        role = guild.get_role(top_role_row.role_id)
        if role is None:
            log.warning(
                "top-of-leaderboard role %s configured for guild %s but not found",
                top_role_row.role_id,
                guild.id,
            )
            return

        top_user_id: int | None = top_users[0].user_id if top_users else None

        # Strip the role from anyone who has it but isn't the current #1.
        for member in list(role.members):
            if member.id == top_user_id:
                continue
            try:
                await member.remove_roles(
                    role, reason="prog: weekly leaderboard reassignment"
                )
            except discord.Forbidden:
                log.warning(
                    "missing permission to remove top-1 role from %s in guild %s",
                    member.id,
                    guild.id,
                )
            except discord.HTTPException as exc:
                log.warning(
                    "HTTP error removing top-1 role from %s: %s",
                    member.id,
                    exc,
                )

        # Add the role to the current #1 if they're still in the guild.
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
                role, reason="prog: weekly leaderboard reassignment"
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
    # /force-leaderboard-update
    # ------------------------------------------------------------------

    @app_commands.command(
        name="force-leaderboard-update",
        description="Run the weekly leaderboard update right now (testing).",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def force_leaderboard_update(
        self, interaction: discord.Interaction
    ) -> None:
        """Force a leaderboard refresh. Still respects ``FREEZE_DATE``."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        updated, processed = await self._run_update(force=True)
        if not updated:
            await interaction.followup.send(
                f"Leaderboard is frozen (today > {FREEZE_DATE.isoformat()}); "
                "no update was run.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Leaderboard updated for {processed} guild(s).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(LeaderboardChannel(bot))
