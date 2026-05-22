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

If a configured role is missing or the bot lacks permission, the failure is
logged and the rest of the transition proceeds.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


def _resolve_ladder_role(
    rewards: list, level: int
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


class Rewards(commands.Cog):
    """Assigns and removes ladder roles on level transitions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_level_change(
        self,
        member: discord.Member,
        old_level: int,
        new_level: int,
    ) -> None:
        """Run the ladder-role transition for this member."""
        if old_level == new_level:
            return

        guild = member.guild
        async with get_session_factory()() as session:
            all_rewards = await crud.list_role_rewards(session, guild.id)
        ladder = sorted(
            (r for r in all_rewards if r.level >= 1),
            key=lambda r: r.level,
        )
        if not ladder:
            return

        old_role_id = _resolve_ladder_role(ladder, old_level)
        new_role_id = _resolve_ladder_role(ladder, new_level)
        if old_role_id == new_role_id:
            return

        if old_role_id is not None:
            await self._remove_role(member, old_role_id)
        if new_role_id is not None:
            await self._add_role(member, new_role_id)

    async def _remove_role(self, member: discord.Member, role_id: int) -> None:
        """Remove ``role_id`` from ``member`` if present, logging any failure."""
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning(
                "ladder role %s configured but not found in guild %s",
                role_id,
                member.guild.id,
            )
            return
        if role not in member.roles:
            return
        try:
            await member.remove_roles(role, reason="prog: ladder transition")
        except discord.Forbidden:
            log.warning(
                "missing permission to remove role %s in guild %s",
                role_id,
                member.guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error removing role %s: %s", role_id, exc)

    async def _add_role(self, member: discord.Member, role_id: int) -> None:
        """Add ``role_id`` to ``member`` if absent, logging any failure."""
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning(
                "ladder role %s configured but not found in guild %s",
                role_id,
                member.guild.id,
            )
            return
        if role in member.roles:
            return
        try:
            await member.add_roles(role, reason="prog: ladder reward")
        except discord.Forbidden:
            log.warning(
                "missing permission to add role %s in guild %s "
                "(check that prog's role is above the reward roles)",
                role_id,
                member.guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error adding role %s: %s", role_id, exc)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Rewards(bot))
