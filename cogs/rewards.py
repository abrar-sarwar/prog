"""Role-reward management.

Listens for the custom ``level_change`` event (dispatched by ``cogs.message_xp``,
``cogs.voice_xp``, and the admin XP commands) and updates the member's ladder
role.

Algorithm
---------
Given the ``role_rewards`` rows for the guild with ``level >= 1``:

* Old ladder role = the row with the highest ``level <= old_level`` (or None).
* New ladder role = the row with the highest ``level <= new_level`` (or None).

If they differ, remove the old one (if present) and add the new one. This
handles level-ups, admin-driven level-downs, and big jumps correctly.

The ``progsuvian`` base role is **never** removed by this cog; that is
managed by ``cogs.onboarding`` only.

``/resync-roles`` is an admin command that scans every member with a users
row and forces them onto the correct ladder role for their current level.
It strips any other ladder roles they might be holding (e.g. left over from
a bug or a manual admin assignment), so the guild can self-heal without
needing /level-set tricks.

Diagnostic logging is intentionally verbose - every level_change event prints
the member, both levels, and the configured rewards, and every role API call
is bracketed by "attempting" / "succeeded" lines so a missing "succeeded" log
pinpoints a Discord-side rejection.
"""

from __future__ import annotations

import logging
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from db import crud
from db.engine import get_session_factory
from db.models import RoleReward

log = logging.getLogger(__name__)


def _resolve_ladder_role(
    rewards: Sequence[RoleReward], level: int
) -> int | None:
    """Return the highest-level ``role_id`` whose threshold is <= ``level``.

    ``rewards`` is the list of RoleReward rows with ``level >= 1``, sorted ascending.
    """
    chosen: int | None = None
    for r in rewards:
        if r.level <= level:
            chosen = r.role_id
        else:
            break
    return chosen


def _ladder_summary(rewards: Sequence[RoleReward]) -> str:
    """Render a compact one-line summary of configured rewards, for logging."""
    if not rewards:
        return "(none)"
    return ", ".join(f"L{r.level}->{r.role_id}" for r in rewards)


class Rewards(commands.Cog):
    """Assigns and removes ladder roles on level transitions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Slash command error handler
    # ------------------------------------------------------------------

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
            log.exception("rewards cog command error: %s", error)
            msg = "Something went wrong - check the bot logs."
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Event listener (algorithm UNCHANGED; only logging added)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_level_change(
        self,
        member: discord.Member,
        old_level: int,
        new_level: int,
    ) -> None:
        """Run the ladder-role transition for this member."""
        guild = member.guild
        async with get_session_factory()() as session:
            all_rewards = await crud.list_role_rewards(session, guild.id)
        ladder = sorted(
            (r for r in all_rewards if r.level >= 1),
            key=lambda r: r.level,
        )

        # Diagnostic header: was the event even fired? what rewards exist?
        log.info(
            "on_level_change fired: member=%s (id=%s) old=%s new=%s ladder=[%s]",
            member.name,
            member.id,
            old_level,
            new_level,
            _ladder_summary(ladder),
        )

        if old_level == new_level:
            log.info("on_level_change: no level delta, returning")
            return
        if not ladder:
            log.info(
                "on_level_change: no ladder rewards configured for guild %s, returning",
                guild.id,
            )
            return

        old_role_id = _resolve_ladder_role(ladder, old_level)
        new_role_id = _resolve_ladder_role(ladder, new_level)
        log.info(
            "on_level_change: resolved old_role=%s new_role=%s",
            old_role_id,
            new_role_id,
        )

        if old_role_id == new_role_id:
            log.info(
                "on_level_change: old_role == new_role (%s), no transition needed",
                old_role_id,
            )
            return

        if old_role_id is not None:
            await self._remove_role(member, old_role_id)
        if new_role_id is not None:
            await self._add_role(member, new_role_id)

    # ------------------------------------------------------------------
    # Role-mutation helpers (algorithm UNCHANGED; logging upgraded)
    # ------------------------------------------------------------------

    async def _remove_role(self, member: discord.Member, role_id: int) -> bool:
        """Remove ``role_id`` from ``member`` if present. Returns True iff the
        Discord API call ran and succeeded."""
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning(
                "ladder role id=%s configured but not found in guild %s; "
                "either the role was deleted or /setup-roles points at a stale id",
                role_id,
                member.guild.id,
            )
            return False
        if role not in member.roles:
            log.info(
                "rewards: skipping remove of %s (id=%s) from %s - member doesn't have it",
                role.name,
                role.id,
                member.name,
            )
            return False
        log.info(
            "rewards: attempting to REMOVE role %s (id=%s) from %s (id=%s)",
            role.name,
            role.id,
            member.name,
            member.id,
        )
        try:
            await member.remove_roles(role, reason="prog: ladder transition")
        except discord.Forbidden:
            log.error(
                "rewards: FORBIDDEN removing role %s (id=%s) from %s. "
                "Likely cause: the bot's top role is BELOW %s in the role list, "
                "or the role is managed by an integration. Fix: move prog's role "
                "above all ladder reward roles in Server Settings -> Roles.",
                role.name,
                role.id,
                member.name,
                role.name,
            )
            return False
        except discord.HTTPException as exc:
            log.error(
                "rewards: HTTP error removing role %s (id=%s) from %s: %s",
                role.name,
                role.id,
                member.name,
                exc,
            )
            return False
        log.info(
            "rewards: succeeded REMOVE of %s (id=%s) from %s",
            role.name,
            role.id,
            member.name,
        )
        return True

    async def _add_role(self, member: discord.Member, role_id: int) -> bool:
        """Add ``role_id`` to ``member`` if absent. Returns True iff the
        Discord API call ran and succeeded."""
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning(
                "ladder role id=%s configured but not found in guild %s; "
                "either the role was deleted or /setup-roles points at a stale id",
                role_id,
                member.guild.id,
            )
            return False
        if role in member.roles:
            log.info(
                "rewards: skipping add of %s (id=%s) to %s - already has it",
                role.name,
                role.id,
                member.name,
            )
            return False
        log.info(
            "rewards: attempting to ADD role %s (id=%s) to %s (id=%s)",
            role.name,
            role.id,
            member.name,
            member.id,
        )
        try:
            await member.add_roles(role, reason="prog: ladder reward")
        except discord.Forbidden:
            log.error(
                "rewards: FORBIDDEN adding role %s (id=%s) to %s. "
                "Likely cause: the bot's top role is BELOW %s in the role list, "
                "or the role is managed by an integration. Fix: move prog's role "
                "above all ladder reward roles in Server Settings -> Roles.",
                role.name,
                role.id,
                member.name,
                role.name,
            )
            return False
        except discord.HTTPException as exc:
            log.error(
                "rewards: HTTP error adding role %s (id=%s) to %s: %s",
                role.name,
                role.id,
                member.name,
                exc,
            )
            return False
        log.info(
            "rewards: succeeded ADD of %s (id=%s) to %s",
            role.name,
            role.id,
            member.name,
        )
        return True

    # ------------------------------------------------------------------
    # /resync-roles - one-shot self-heal across all members in the guild
    # ------------------------------------------------------------------

    @app_commands.command(
        name="resync-roles",
        description="Re-apply ladder roles to every member based on their current level.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resync_roles(self, interaction: discord.Interaction) -> None:
        """Iterate every user row in this guild and force their ladder role
        into agreement with their current level. Strips any other ladder
        roles they may be holding."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        async with get_session_factory()() as session:
            rewards = await crud.list_role_rewards(session, guild.id)
            users = await crud.list_users_in_guild(session, guild.id)

        ladder = sorted(
            (r for r in rewards if r.level >= 1),
            key=lambda r: r.level,
        )
        if not ladder:
            await interaction.followup.send(
                "No ladder rewards configured (`/setup-roles` first). "
                "Nothing to sync.",
                ephemeral=True,
            )
            return

        ladder_role_ids = {r.role_id for r in ladder}
        log.info(
            "/resync-roles: starting in guild %s; %d user rows, %d ladder rewards",
            guild.id,
            len(users),
            len(ladder),
        )

        synced = 0
        skipped_missing_member = 0
        skipped_bot = 0
        for u in users:
            member = guild.get_member(u.user_id)
            if member is None:
                skipped_missing_member += 1
                continue
            if member.bot:
                skipped_bot += 1
                continue
            await self._sync_member_ladder(member, u.level, ladder, ladder_role_ids)
            synced += 1

        log.info(
            "/resync-roles: done. synced=%d skipped_missing=%d skipped_bot=%d",
            synced,
            skipped_missing_member,
            skipped_bot,
        )
        await interaction.followup.send(
            embed=discord.Embed(
                title="Role resync complete",
                description=(
                    f"Synced **{synced}** member(s).\n"
                    f"Skipped **{skipped_missing_member}** (no longer in guild) "
                    f"and **{skipped_bot}** bot(s)."
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    async def _sync_member_ladder(
        self,
        member: discord.Member,
        current_level: int,
        ladder: Sequence[RoleReward],
        ladder_role_ids: set[int],
    ) -> None:
        """Force the member onto the correct ladder role for their level.

        Removes any *other* ladder role they may hold (a defensive sweep -
        a member should never have more than one ladder role).
        """
        correct_id = _resolve_ladder_role(ladder, current_level)

        # Strip any ladder role that isn't the correct one.
        for m_role in list(member.roles):
            if m_role.id in ladder_role_ids and m_role.id != correct_id:
                await self._remove_role(member, m_role.id)

        # Add the correct role if it's missing.
        if correct_id is not None:
            await self._add_role(member, correct_id)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Rewards(bot))
