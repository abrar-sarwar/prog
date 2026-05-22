"""Admin slash commands.

All commands require the ``Manage Server`` permission and are guild-only. The
``default_permissions`` decorator hides them from non-mods in the Discord UI;
the ``has_permissions`` check enforces it server-side as defence in depth.

XP/level mutators dispatch the ``level_change`` event after commit so the
rewards cog can re-evaluate ladder roles for the affected member.
"""

from __future__ import annotations

import logging
from typing import Union

import discord
from discord import app_commands
from discord.ext import commands

from cogs.onboarding import ensure_member_initialized
from core.constants import LEVEL_ROLE_NAMES
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)

# Channel types accepted by commands that touch "any XP-bearing channel".
GuildTextish = Union[discord.TextChannel, discord.VoiceChannel]


def _format_role(guild: discord.Guild, role_id: int | None) -> str:
    """Render a role ID as a mention, or 'not set' if None/missing."""
    if role_id is None:
        return "_not set_"
    role = guild.get_role(role_id)
    return role.mention if role is not None else f"_unknown role ({role_id})_"


def _format_channel(guild: discord.Guild, channel_id: int | None) -> str:
    """Render a channel ID as a mention, or 'not set' if None/missing."""
    if channel_id is None:
        return "_not set_"
    channel = guild.get_channel(channel_id)
    return channel.mention if channel is not None else f"_unknown channel ({channel_id})_"


class Admin(commands.Cog):
    """All server-admin slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Error handling for the whole cog
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Convert command errors into clean ephemeral replies."""
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You need the **Manage Server** permission to use this command."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command here."
        else:
            log.exception("admin command error: %s", error)
            msg = "Something went wrong - check the bot logs."
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)

    async def _dispatch_if_changed(
        self,
        member: discord.Member,
        old_level: int,
        new_level: int,
    ) -> None:
        """Fire ``level_change`` so the rewards cog picks up the transition."""
        if old_level != new_level:
            self.bot.dispatch("level_change", member, old_level, new_level)

    # ------------------------------------------------------------------
    # XP manipulation
    # ------------------------------------------------------------------

    @app_commands.command(name="xp-give", description="Add XP to a member")
    @app_commands.describe(user="Member to grant XP to", amount="XP to add (> 0)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_give(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000_000],
    ) -> None:
        """Add XP to a member."""
        assert interaction.guild is not None
        if user.bot:
            await interaction.response.send_message(
                "Bots don't earn XP.", ephemeral=True
            )
            return

        await ensure_member_initialized(user)
        async with get_session_factory()() as session:
            change = await crud.add_admin_xp(
                session, interaction.guild.id, user.id, amount
            )
            await session.commit()
        await self._dispatch_if_changed(user, change.old_level, change.new_level)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="XP granted",
                description=(
                    f"Gave **{amount:,}** XP to {user.mention}.\n"
                    f"Now level **{change.new_level}** "
                    f"({change.user.xp:,} XP total)."
                ),
                color=discord.Color.green(),
            )
        )

    @app_commands.command(name="xp-remove", description="Remove XP from a member")
    @app_commands.describe(user="Member to remove XP from", amount="XP to remove (> 0)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000_000],
    ) -> None:
        """Remove XP from a member. Total XP is floored at 0."""
        assert interaction.guild is not None
        if user.bot:
            await interaction.response.send_message(
                "Bots don't earn XP.", ephemeral=True
            )
            return

        await ensure_member_initialized(user)
        async with get_session_factory()() as session:
            change = await crud.add_admin_xp(
                session, interaction.guild.id, user.id, -amount
            )
            await session.commit()
        await self._dispatch_if_changed(user, change.old_level, change.new_level)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="XP removed",
                description=(
                    f"Removed **{amount:,}** XP from {user.mention}.\n"
                    f"Now level **{change.new_level}** "
                    f"({change.user.xp:,} XP total)."
                ),
                color=discord.Color.orange(),
            )
        )

    @app_commands.command(name="xp-set", description="Set a member's total XP")
    @app_commands.describe(user="Member to update", amount="New total XP (>= 0)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Range[int, 0, 1_000_000_000_000],
    ) -> None:
        """Overwrite the member's cumulative XP and recompute level."""
        assert interaction.guild is not None
        if user.bot:
            await interaction.response.send_message(
                "Bots don't earn XP.", ephemeral=True
            )
            return

        await ensure_member_initialized(user)
        async with get_session_factory()() as session:
            change = await crud.set_user_xp(
                session, interaction.guild.id, user.id, amount
            )
            await session.commit()
        await self._dispatch_if_changed(user, change.old_level, change.new_level)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="XP set",
                description=(
                    f"Set {user.mention} to **{amount:,}** XP "
                    f"(level **{change.new_level}**)."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="level-set",
        description="Set a member's level (snaps XP to that level's minimum)",
    )
    @app_commands.describe(user="Member to update", level="Target level (>= 1)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        level: app_commands.Range[int, 1, 10_000],
    ) -> None:
        """Set the member to the minimum XP for ``level``."""
        assert interaction.guild is not None
        if user.bot:
            await interaction.response.send_message(
                "Bots don't earn XP.", ephemeral=True
            )
            return

        await ensure_member_initialized(user)
        async with get_session_factory()() as session:
            change = await crud.set_user_level(
                session, interaction.guild.id, user.id, level
            )
            await session.commit()
        await self._dispatch_if_changed(user, change.old_level, change.new_level)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Level set",
                description=(
                    f"Set {user.mention} to level **{level}** "
                    f"({change.user.xp:,} XP total)."
                ),
                color=discord.Color.blurple(),
            )
        )

    # ------------------------------------------------------------------
    # Setup / configuration
    # ------------------------------------------------------------------

    @app_commands.command(
        name="setup-roles",
        description="Assign a role to a level (level 0 = top-of-leaderboard role)",
    )
    @app_commands.describe(
        level="Level threshold (0 for top-of-leaderboard; 1, 11, 21, 31, 41 are ladder)",
        role="Role to grant at this level",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_roles(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 1000],
        role: discord.Role,
    ) -> None:
        """Configure the role granted at the given level threshold."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.upsert_role_reward(
                session, interaction.guild.id, level, role.id
            )
            await session.commit()

        suggested = LEVEL_ROLE_NAMES.get(level)
        label = f"Level {level}"
        if suggested is not None:
            label += f" (`{suggested}` slot)"
        elif level == 0:
            label += " (top-of-leaderboard slot)"
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Role reward set",
                description=f"{label} → {role.mention}",
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-progsuvian",
        description="Set the base role assigned to every member on first interaction",
    )
    @app_commands.describe(role="The progsuvian base role")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_progsuvian(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        """Set the progsuvian base role for this guild."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_progsuvian_role(
                session, interaction.guild.id, role.id
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="progsuvian role set",
                description=(
                    f"Base role set to {role.mention}. New members get it on join; "
                    "existing members get it on their next message or VC join."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-leaderboard-channel",
        description="Where the weekly leaderboard is posted/updated",
    )
    @app_commands.describe(channel="Text channel for the weekly leaderboard")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_leaderboard_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel the weekly leaderboard post lives in."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_leaderboard_channel(
                session, interaction.guild.id, channel.id
            )
            # Channel change invalidates the existing message ID; clear it so
            # the next weekly tick creates a fresh post in the new channel.
            await crud.set_leaderboard_message(
                session, interaction.guild.id, None
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Leaderboard channel set",
                description=(
                    f"Weekly leaderboard will post in {channel.mention}. "
                    "A new message will be created on the next Saturday-noon update."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-levelup-channel",
        description="Where level-up announcements are posted",
    )
    @app_commands.describe(channel="Channel for level-up announcements")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_levelup_channel(
        self,
        interaction: discord.Interaction,
        channel: GuildTextish,
    ) -> None:
        """Set the channel level-up announcements are posted in."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_level_up_channel(
                session, interaction.guild.id, channel.id
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Level-up channel set",
                description=f"Level-up announcements will post in {channel.mention}.",
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="blacklist-channel",
        description="Toggle a channel's XP-blacklist status",
    )
    @app_commands.describe(channel="Channel to toggle")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blacklist_channel(
        self,
        interaction: discord.Interaction,
        channel: GuildTextish,
    ) -> None:
        """Add or remove a channel from the XP blacklist."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            now_blacklisted = await crud.toggle_channel_blacklist(
                session, interaction.guild.id, channel.id
            )
            await session.commit()
        verb = "blacklisted" if now_blacklisted else "un-blacklisted"
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"Channel {verb}",
                description=(
                    f"{channel.mention} is now "
                    f"{'**not** earning XP' if now_blacklisted else 'earning XP again'}."
                ),
                color=discord.Color.orange() if now_blacklisted else discord.Color.green(),
            )
        )

    @app_commands.command(
        name="set-channel-multiplier",
        description="Set XP multiplier for a channel (1.0 to remove)",
    )
    @app_commands.describe(
        channel="Channel to set multiplier on",
        multiplier="Multiplier value (e.g. 1.5; set 1.0 to remove)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel_multiplier(
        self,
        interaction: discord.Interaction,
        channel: GuildTextish,
        multiplier: app_commands.Range[float, 0.0, 100.0],
    ) -> None:
        """Set or clear the channel's XP multiplier."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_channel_multiplier(
                session, interaction.guild.id, channel.id, float(multiplier)
            )
            await session.commit()
        if multiplier == 1.0:
            desc = f"Removed multiplier from {channel.mention}."
        else:
            desc = f"{channel.mention} now grants XP at **{multiplier}×**."
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Channel multiplier updated",
                description=desc,
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="set-role-multiplier",
        description="Set XP multiplier for a role (1.0 to remove)",
    )
    @app_commands.describe(
        role="Role to set multiplier on",
        multiplier="Multiplier value (e.g. 2.0; set 1.0 to remove)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_role_multiplier(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        multiplier: app_commands.Range[float, 0.0, 100.0],
    ) -> None:
        """Set or clear the role's XP multiplier.

        Reminder: role multipliers do NOT stack; the highest applicable role
        multiplier on the member is used.
        """
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_role_multiplier(
                session, interaction.guild.id, role.id, float(multiplier)
            )
            await session.commit()
        if multiplier == 1.0:
            desc = f"Removed multiplier from {role.mention}."
        else:
            desc = (
                f"Members with {role.mention} now earn at **{multiplier}×** "
                "(highest applicable role wins; role multipliers don't stack)."
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Role multiplier updated",
                description=desc,
                color=discord.Color.blurple(),
            )
        )

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @app_commands.command(
        name="show-config", description="Show the current prog configuration"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        """Print the per-guild configuration as an embed."""
        assert interaction.guild is not None
        guild = interaction.guild
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(session, guild.id)
            rewards = await crud.list_role_rewards(session, guild.id)
            await session.commit()

        embed = discord.Embed(
            title=f"prog configuration — {guild.name}",
            color=discord.Color.blurple(),
        )

        # Channels
        ch_lines = [
            f"Level-up: {_format_channel(guild, config.level_up_channel_id)}",
            f"Leaderboard: {_format_channel(guild, config.leaderboard_channel_id)}",
        ]
        if config.leaderboard_message_id is not None:
            ch_lines.append(f"_(message id: `{config.leaderboard_message_id}`)_")
        embed.add_field(name="Channels", value="\n".join(ch_lines), inline=False)

        # Progsuvian base role
        embed.add_field(
            name="Progsuvian role",
            value=_format_role(guild, config.progsuvian_role_id),
            inline=False,
        )

        # Ladder rewards
        if rewards:
            lines = []
            for r in rewards:
                hint = LEVEL_ROLE_NAMES.get(r.level)
                tag = f" (`{hint}` slot)" if hint else (
                    " (top-of-leaderboard slot)" if r.level == 0 else ""
                )
                lines.append(
                    f"Level {r.level}{tag}: {_format_role(guild, r.role_id)}"
                )
            embed.add_field(
                name="Ladder rewards", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(
                name="Ladder rewards",
                value="_(none configured — run `/setup-roles`)_",
                inline=False,
            )

        # Channel multipliers
        if config.channel_multipliers:
            lines = [
                f"{_format_channel(guild, int(cid))}: **{mult}×**"
                for cid, mult in config.channel_multipliers.items()
            ]
            embed.add_field(
                name="Channel multipliers", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(
                name="Channel multipliers", value="_(none)_", inline=False
            )

        # Role multipliers
        if config.role_multipliers:
            lines = [
                f"{_format_role(guild, int(rid))}: **{mult}×**"
                for rid, mult in config.role_multipliers.items()
            ]
            embed.add_field(
                name="Role multipliers", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(
                name="Role multipliers", value="_(none)_", inline=False
            )

        # Blacklist
        if config.xp_blacklist:
            lines = [
                _format_channel(guild, int(cid))
                for cid in config.xp_blacklist
            ]
            embed.add_field(
                name="Blacklisted channels", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(
                name="Blacklisted channels", value="_(none)_", inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Admin(bot))
