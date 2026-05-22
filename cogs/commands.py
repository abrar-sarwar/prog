"""User-facing slash commands: ``/rank`` and ``/leaderboard``.

``/rank`` shows a single member's level, total XP, in-level progress bar,
and server rank.

``/leaderboard`` shows a paginated top-N (10 per page) view with prev/next
buttons. The invoker's own rank is always shown in the footer, even if they
are off-page.
"""

from __future__ import annotations

import logging
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from core.constants import LEADERBOARD_PAGE_SIZE
from core.leveling import cumulative_xp_to_level, xp_for_level
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
    """Render one leaderboard line."""
    name = member.display_name if member is not None else f"Unknown ({user_id})"
    return f"**#{rank}** · {name} — level {row.level} ({row.xp:,} XP)"


async def _build_leaderboard_embed(
    guild: discord.Guild,
    invoker_id: int,
    page: int,
    page_size: int,
) -> discord.Embed:
    """Build the embed for one page of the leaderboard."""
    offset = page * page_size
    async with get_session_factory()() as session:
        rows: Sequence[User] = await crud.get_top_users(
            session, guild.id, limit=page_size, offset=offset
        )
        total = await crud.count_users_in_guild(session, guild.id)
        invoker_rank = await crud.get_user_rank(session, guild.id, invoker_id)

    total_pages = max(1, (total + page_size - 1) // page_size)

    lines: list[str] = []
    for i, row in enumerate(rows):
        global_rank = offset + i + 1
        member = guild.get_member(row.user_id)
        lines.append(_format_leaderboard_line(global_rank, member, row.user_id, row))

    embed = discord.Embed(
        title=f"Leaderboard — {guild.name}",
        description="\n".join(lines) if lines else "_(this page is empty)_",
        color=discord.Color.gold(),
    )
    footer = f"Page {page + 1}/{total_pages}"
    if invoker_rank is not None:
        footer += f"  ·  You: #{invoker_rank}"
    embed.set_footer(text=footer)
    return embed


class LeaderboardView(discord.ui.View):
    """Pagination view bound to a single invoker.

    Disables itself on timeout. Other users get an ephemeral 'not yours'
    response if they click.
    """

    def __init__(
        self,
        *,
        invoker_id: int,
        guild_id: int,
        total_count: int,
        page_size: int = LEADERBOARD_PAGE_SIZE,
    ) -> None:
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.guild_id = guild_id
        self.page = 0
        self.page_size = page_size
        self.total_pages = max(1, (total_count + page_size - 1) // page_size)
        self.message: discord.Message | None = None
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """Enable/disable prev and next buttons based on the current page."""
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Restrict button presses to the original invoker."""
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "This isn't your leaderboard view - run `/leaderboard` yourself.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        """Move to the previous page and re-render the embed."""
        self.page -= 1
        self._refresh_buttons()
        assert interaction.guild is not None
        embed = await _build_leaderboard_embed(
            interaction.guild, self.invoker_id, self.page, self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        """Move to the next page and re-render the embed."""
        self.page += 1
        self._refresh_buttons()
        assert interaction.guild is not None
        embed = await _build_leaderboard_embed(
            interaction.guild, self.invoker_id, self.page, self.page_size
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        """Disable all buttons in place when the view times out."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


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
        name="leaderboard", description="Show the server XP leaderboard"
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Paginated guild XP leaderboard."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        guild = interaction.guild
        invoker_id = interaction.user.id
        async with get_session_factory()() as session:
            total = await crud.count_users_in_guild(session, guild.id)

        if total == 0:
            await interaction.response.send_message(
                "No XP earned in this server yet.", ephemeral=True
            )
            return

        view = LeaderboardView(
            invoker_id=invoker_id, guild_id=guild.id, total_count=total
        )
        embed = await _build_leaderboard_embed(
            guild, invoker_id, page=0, page_size=LEADERBOARD_PAGE_SIZE
        )
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(UserCommands(bot))
