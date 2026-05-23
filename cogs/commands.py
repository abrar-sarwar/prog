"""User-facing slash commands: ``/rank`` and ``/leaderboard``.

``/rank`` shows a single member's level, total XP, in-level progress bar,
and server rank.

``/leaderboard`` shows a sliding window of 5 entries centered on the invoker:

* invoker ranked #1, #2, or #3 -> ranks 1-5
* invoker ranked middle -> invoker's rank +/- 2 (invoker in the middle row)
* invoker in the bottom 2 -> last 5 ranks
* fewer than 5 users with XP -> all of them
* invoker has no XP row -> top 5, with a "not on the board yet" footer

The invoker's own row is visually emphasised with a leading arrow and bold.
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
_NOT_ON_BOARD_FOOTER: str = (
    "You're not on the board yet — send a message to earn XP."
)


def _progress_bar(filled: int, width: int = _PROGRESS_BAR_WIDTH) -> str:
    """Return a unicode block-progress bar of the given width."""
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _format_row(
    rank: int,
    member: discord.Member | None,
    row: User,
    *,
    is_invoker: bool,
) -> str:
    """Render one leaderboard row. The invoker's own row is prefixed with
    ``▸`` and the whole line is bolded."""
    name = member.display_name if member is not None else f"Unknown ({row.user_id})"
    if is_invoker:
        return f"▸ **#{rank} · {name} · Level {row.level}**"
    return f"**#{rank}** · {name} · Level {row.level}"


async def _build_leaderboard_embed(
    guild: discord.Guild, invoker_id: int
) -> discord.Embed:
    """Build the sliding-window leaderboard embed for ``invoker_id``."""
    async with get_session_factory()() as session:
        invoker_rank = await crud.get_user_rank(session, guild.id, invoker_id)
        if invoker_rank is None:
            start_rank = 1
            users: Sequence[User] = await crud.get_top_users(
                session, guild.id, limit=LEADERBOARD_TOP_N
            )
            footer = _NOT_ON_BOARD_FOOTER
        else:
            start_rank, users = await crud.get_users_around_rank(
                session, guild.id, invoker_rank, window=LEADERBOARD_TOP_N
            )
            footer = f"You: #{invoker_rank}"

    lines: list[str] = []
    for i, row in enumerate(users):
        rank = start_rank + i
        member = guild.get_member(row.user_id)
        lines.append(
            _format_row(rank, member, row, is_invoker=(row.user_id == invoker_id))
        )

    embed = discord.Embed(
        title=f"🏆 {guild.name} Leaderboard",
        description="\n".join(lines) if lines else "_(no entries yet)_",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=footer)
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
        name="leaderboard",
        description="Show a 5-entry leaderboard centered on you.",
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Sliding-window top-5 leaderboard centered on the invoker."""
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
