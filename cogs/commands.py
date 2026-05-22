"""User-facing slash commands: ``/rank`` and ``/leaderboard``.

``/rank`` shows a single member's level, total XP, in-level progress bar,
and server rank.

``/leaderboard`` shows a single static embed with the top 5 members. The
invoker's own rank is shown in the footer.
"""

from __future__ import annotations

import logging
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from core.constants import LEADERBOARD_TOP_N
from core.leveling import cumulative_xp_to_level
from db import crud
from db.engine import get_session_factory
from db.models import User

log = logging.getLogger(__name__)

_PROGRESS_BAR_WIDTH: int = 10


def _progress_bar(filled: int, width: int = _PROGRESS_BAR_WIDTH) -> str:
    """Return a unicode block-progress bar of the given width."""
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _format_leaderboard_line(
    rank: int, member: discord.Member | None, user_id: int, row: User
) -> str:
    """Render one leaderboard line. XP is intentionally not shown - the
    leaderboard surfaces ranks and levels only; the precise XP number is
    private and lives behind ``/rank``."""
    name = member.display_name if member is not None else f"Unknown ({user_id})"
    return f"**#{rank}** · {name} · Level {row.level}"


async def _build_leaderboard_embed(
    guild: discord.Guild, invoker_id: int
) -> discord.Embed:
    """Build the static top-N leaderboard embed for a guild."""
    async with get_session_factory()() as session:
        rows: Sequence[User] = await crud.get_top_users(
            session, guild.id, limit=LEADERBOARD_TOP_N
        )
        invoker_rank = await crud.get_user_rank(session, guild.id, invoker_id)

    lines: list[str] = []
    for i, row in enumerate(rows):
        rank = i + 1
        member = guild.get_member(row.user_id)
        lines.append(_format_leaderboard_line(rank, member, row.user_id, row))

    embed = discord.Embed(
        title=f"Leaderboard — {guild.name}",
        description="\n".join(lines) if lines else "_(no entries yet)_",
        color=discord.Color.gold(),
    )
    if invoker_rank is not None:
        embed.set_footer(text=f"You: #{invoker_rank}")
    return embed


class UserCommands(commands.Cog):
    """``/rank`` and ``/leaderboard``."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="rank", description="Show level, XP, and server rank")
    @app_commands.describe(user="Whose rank to show (defaults to yourself)")
    async def rank(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Display the level/XP/rank embed for a member."""
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

        guild = interaction.guild
        async with get_session_factory()() as session:
            row = await crud.get_user(session, guild.id, target.id)
            if row is None:
                await interaction.response.send_message(
                    f"{target.display_name} hasn't earned any XP yet.",
                    ephemeral=True,
                )
                return
            rank = await crud.get_user_rank(session, guild.id, target.id)
            total_users = await crud.count_users_in_guild(session, guild.id)

        level = row.level
        total_xp = row.xp
        level_floor = cumulative_xp_to_level(level)
        next_floor = cumulative_xp_to_level(level + 1)
        xp_in_level = total_xp - level_floor
        xp_needed = next_floor - level_floor  # equals xp_for_level(level)
        xp_to_next = next_floor - total_xp
        percent = (xp_in_level / xp_needed) * 100 if xp_needed > 0 else 0
        filled_blocks = int(percent // 10)
        bar = _progress_bar(filled_blocks)

        embed = discord.Embed(
            title=f"{target.display_name}'s rank",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(
            name="Server rank", value=f"#{rank} / {total_users}", inline=True
        )
        embed.add_field(name="Total XP", value=f"{total_xp:,}", inline=True)
        embed.add_field(
            name="Progress to next level",
            value=(
                f"`{bar}` {percent:.0f}%\n"
                f"{xp_in_level:,} / {xp_needed:,} XP in level {level}\n"
                f"{xp_to_next:,} XP to level {level + 1}"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="leaderboard", description="Show the top of the server"
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Static top-5 leaderboard with no pagination."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        embed = await _build_leaderboard_embed(
            interaction.guild, interaction.user.id
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(UserCommands(bot))
