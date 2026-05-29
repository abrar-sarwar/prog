"""User-facing slash commands: ``/rank`` and ``/leaderboard``.

Both commands respond ephemerally — only the invoker sees the result —
and both render PNG cards via :mod:`core.rank_image` and
:mod:`core.leaderboard_image`. There are no text embeds; the image
*is* the response.

* ``/rank`` shows a single member's trophy card: avatar, tier-coloured
  rank chip, big LEVEL numeral, and a progress bar to the next level.
  No XP is shown.
* ``/leaderboard`` shows the same top-10 image used in the persistent
  leaderboard channel, attached straight to the slash reply.

Avatars and the guild icon are fetched via :meth:`discord.Asset.read`.
That's a single HTTP round-trip per asset; for slash invocations a
per-call fetch is fine without an LRU.
"""

from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.constants import LEADERBOARD_IMAGE_TOP_N
from core.leaderboard_image import (
    LeaderboardEntry,
    render_png as render_leaderboard_png,
)
from core.rank_image import RankCardData, render_rank_png
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)

_RANK_FILENAME = "rank.png"
_LEADERBOARD_FILENAME = "leaderboard.png"


async def _read_avatar(member: discord.Member) -> bytes | None:
    """Fetch ``member``'s display avatar as raw bytes (256px)."""
    try:
        return await member.display_avatar.with_size(256).read()
    except discord.HTTPException as exc:
        log.warning("avatar fetch failed for %s: %s", member.id, exc)
        return None


async def _read_guild_icon(guild: discord.Guild) -> bytes | None:
    """Fetch the guild icon as raw bytes (256px), or None if unset."""
    if guild.icon is None:
        return None
    try:
        return await guild.icon.with_size(256).read()
    except discord.HTTPException as exc:
        log.warning("guild icon fetch failed for %s: %s", guild.id, exc)
        return None


class UserCommands(commands.Cog):
    """``/rank`` and ``/leaderboard`` — both ephemeral, both image-based."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /rank
    # ------------------------------------------------------------------

    @app_commands.command(name="rank", description="Show your trophy card")
    @app_commands.describe(user="Whose rank to show (defaults to yourself)")
    async def rank(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Display the rank card for a member as an ephemeral image."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        target = user if user is not None else interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "That user isn't in this server.", ephemeral=True
            )
            return
        if target.bot:
            await interaction.response.send_message(
                "Bots don't earn XP.", ephemeral=True
            )
            return

        # Image rendering + asset fetches take a beat; defer so we
        # don't trip the 3 s slash-command deadline.
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        async with get_session_factory()() as session:
            row = await crud.get_user(session, guild.id, target.id)
            if row is None:
                await interaction.followup.send(
                    f"{target.display_name} hasn't earned any XP yet.",
                    ephemeral=True,
                )
                return
            rank_pos = await crud.get_user_rank(session, guild.id, target.id)
            total = await crud.count_users_in_guild(session, guild.id)

        avatar_bytes, guild_icon_bytes = await asyncio.gather(
            _read_avatar(target),
            _read_guild_icon(guild),
        )

        data = RankCardData(
            display_name=target.display_name,
            avatar_bytes=avatar_bytes,
            guild_name=guild.name,
            guild_icon_bytes=guild_icon_bytes,
            rank=rank_pos or 0,
            total_members=total,
            level=row.level,
            total_xp=row.xp,
        )

        loop = asyncio.get_running_loop()
        png_bytes = await loop.run_in_executor(
            None, lambda: render_rank_png(data)
        )

        await interaction.followup.send(
            file=discord.File(io.BytesIO(png_bytes), filename=_RANK_FILENAME),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /leaderboard
    # ------------------------------------------------------------------

    @app_commands.command(
        name="leaderboard",
        description="Show the top 10 leaderboard",
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Render and send the top-10 image as an ephemeral reply."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        async with get_session_factory()() as session:
            top = list(
                await crud.get_top_users(
                    session, guild.id, limit=LEADERBOARD_IMAGE_TOP_N
                )
            )

        if not top:
            await interaction.followup.send(
                "No one has earned XP yet — be the first.",
                ephemeral=True,
            )
            return

        # Fetch the guild icon once + every avatar in parallel.
        async def _entry(i: int, row) -> LeaderboardEntry:
            member = guild.get_member(row.user_id)
            display_name = (
                member.display_name
                if member is not None
                else f"Unknown ({row.user_id})"
            )
            avatar_bytes = (
                await _read_avatar(member) if member is not None else None
            )
            return LeaderboardEntry(
                rank=i + 1,
                display_name=display_name,
                level=row.level,
                xp=row.xp,
                avatar_bytes=avatar_bytes,
            )

        guild_icon_bytes, *entries = await asyncio.gather(
            _read_guild_icon(guild),
            *(_entry(i, row) for i, row in enumerate(top)),
        )

        from datetime import datetime, timezone

        loop = asyncio.get_running_loop()
        png_bytes = await loop.run_in_executor(
            None,
            lambda: render_leaderboard_png(
                guild_name=guild.name,
                guild_icon_bytes=guild_icon_bytes,
                entries=entries,
                rendered_at=datetime.now(tz=timezone.utc),
            ),
        )

        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(png_bytes), filename=_LEADERBOARD_FILENAME
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(UserCommands(bot))
