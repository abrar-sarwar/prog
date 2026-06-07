"""Admin slash commands.

All commands require the ``Manage Server`` permission and are guild-only. The
``default_permissions`` decorator hides them from non-mods in the Discord UI;
the ``has_permissions`` check enforces it server-side as defence in depth.

XP/level mutators always dispatch ``xp_grant`` (so the leaderboard cog
invalidates its cache and updates the persistent embed) and additionally
dispatch ``level_change`` when the level actually moved (so the rewards
cog can re-evaluate ladder roles).

Rank-change announcements
-------------------------
After every admin XP/level mutation, the cog calls
:func:`core.leveling.admin_rank_change_action` to decide whether to post
a level-up announcement and which template to use. Only one message
fires - for the band the user ended up in - and only on a net positive
level change. Demotions are silent. Level 100 (Aura) respects the
one-shot ``aura_message_fired`` flag.

``/migrate-rank-roles`` is a one-shot helper that renames legacy ladder
roles (prognoob/progknight/gitsworn/progmaster/gitalife) to the new
tier names and remaps their ``role_rewards`` thresholds (11->10, 21->20,
31->30, 41->40) so existing role bindings keep working under the new
band layout.
"""

from __future__ import annotations

import logging
from typing import Union

import discord
from discord import app_commands
from discord.ext import commands

from cogs.onboarding import ensure_member_initialized
from core.leveling import (
    LEVEL_CAP,
    TIER_BANDS,
    admin_rank_change_action,
    get_level_message,
)
from core.welcome import DEFAULT_WELCOME_MESSAGE
from db import crud
from db.engine import get_session_factory


# Threshold-level -> tier role name, derived from TIER_BANDS so the source
# of truth stays in one place. Used for /setup-roles hints and /show-config.
LEVEL_ROLE_HINTS: dict[int, str] = {lo: role for lo, _hi, _name, role in TIER_BANDS}

# Legacy ladder roles from the previous tier system. (old_name, old_threshold,
# new_name, new_threshold). The migration helper renames Discord roles found
# by ``old_name`` to ``new_name`` and updates role_rewards rows so the
# corresponding ``old_threshold`` becomes ``new_threshold``.
_LEGACY_ROLE_MIGRATIONS: list[tuple[str, int, str, int]] = [
    ("prognoob", 1, "Freshie", 1),
    ("progknight", 11, "Viber", 10),
    ("gitsworn", 21, "Innovator", 20),
    ("progmaster", 31, "Hustler", 30),
    ("gitalife", 41, "Yapper", 40),
]

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


class WelcomeMessageModal(discord.ui.Modal, title="Edit welcome message"):
    """Multi-line editor for a guild's welcome message.

    Prefilled with the guild's current message (or the default). Submitting an
    empty field clears the override so the default template is used again.
    Available placeholders: ``{user}``, ``{server}``, ``{intro_channel}``.
    """

    def __init__(self, guild_id: int, current: str) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.message_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Welcome message",
            style=discord.TextStyle.paragraph,
            default=current,
            required=False,
            max_length=1500,
            placeholder="Use {user}, {server} and {intro_channel} placeholders…",
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Persist the edited message (or clear it when left empty)."""
        new_value = self.message_input.value.strip()
        message = new_value or None
        async with get_session_factory()() as session:
            await crud.set_welcome_message(session, self.guild_id, message)
            await session.commit()
        desc = (
            "Welcome message reset to the default."
            if message is None
            else "Welcome message updated."
        )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome message",
                description=desc,
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )


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

    async def _dispatch_after_xp_change(
        self,
        member: discord.Member,
        old_level: int,
        new_level: int,
    ) -> None:
        """Run all post-mutation side effects for an admin XP/level change.

        Steps:

        1. Dispatch ``xp_grant`` so the leaderboard cog re-evaluates the
           cached top-N within the debounce window.
        2. Dispatch ``level_change`` (only on a level delta) so the
           rewards cog swaps the member's tier role.
        3. Resolve and post the level-up announcement per spec - one
           message at the *new* level only, silent on demotion, silent on
           Aura re-fires (the one-shot flag wins even for admins).
        4. Persist the Aura flag on the first-ever crossing to LEVEL_CAP.

        The Aura flag and the level-up channel are read in a single
        post-mutation snapshot. This is safe because the admin mutation
        we just committed never touches ``aura_message_fired``, so the
        flag still reflects its pre-mutation state.
        """
        self.bot.dispatch("xp_grant", member.guild.id)
        if old_level != new_level:
            self.bot.dispatch("level_change", member, old_level, new_level)

        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, member.guild.id
            )
            user_row = await crud.get_user(session, member.guild.id, member.id)
            await session.commit()
        aura_already_fired = (
            user_row.aura_message_fired if user_row is not None else False
        )
        level_up_channel_id = config.level_up_channel_id

        action = admin_rank_change_action(
            old_level=old_level,
            new_level=new_level,
            aura_already_fired=aura_already_fired,
        )
        if not action.fire_message:
            return

        if action.set_aura_flag:
            # Persist the flag *before* posting so a crash between the
            # message and the DB write can't cause a double-fire on the
            # next admin grant.
            async with get_session_factory()() as session:
                await crud.mark_aura_message_fired(
                    session, member.guild.id, member.id
                )
                await session.commit()

        # Respect the per-guild level-up-message switch (XP/roles still applied).
        if not config.level_up_announcements_enabled:
            return
        await self._post_announcement(
            member, action.message_level, level_up_channel_id
        )

    async def _post_announcement(
        self,
        member: discord.Member,
        level: int,
        announce_channel_id: int | None,
    ) -> None:
        """Send the tier template for ``level`` to the configured level-up
        channel (or the guild's system channel as a fallback)."""
        guild = member.guild
        target: discord.abc.Messageable | None = None
        if announce_channel_id is not None:
            ch = guild.get_channel(announce_channel_id)
            if isinstance(
                ch,
                (discord.TextChannel, discord.VoiceChannel, discord.Thread),
            ):
                target = ch
        if target is None:
            target = guild.system_channel
        if target is None:
            log.info(
                "admin rank-change for %s: no level-up channel configured "
                "and no system channel - skipping announcement",
                member.id,
            )
            return

        template = get_level_message(level)
        content = template.format(user=member.mention, level=level)
        try:
            await target.send(
                content=content,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.Forbidden:
            log.warning(
                "missing permission to post admin level-up in guild %s",
                guild.id,
            )
        except discord.HTTPException as exc:
            log.warning("HTTP error posting admin level-up: %s", exc)

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
        await self._dispatch_after_xp_change(user, change.old_level, change.new_level)

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
        await self._dispatch_after_xp_change(user, change.old_level, change.new_level)

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
        await self._dispatch_after_xp_change(user, change.old_level, change.new_level)

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
    @app_commands.describe(
        user="Member to update",
        level=f"Target level (0..{LEVEL_CAP}; clamped to {LEVEL_CAP})",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        level: app_commands.Range[int, 0, LEVEL_CAP],
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
        await self._dispatch_after_xp_change(user, change.old_level, change.new_level)

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
        name="enable-xp",
        description="Turn XP earning ON for this server",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def enable_xp(self, interaction: discord.Interaction) -> None:
        """Enable XP earning (message + voice) for this guild."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            already = config.xp_enabled
            await crud.set_xp_enabled(session, interaction.guild.id, True)
            level_up_channel_id = config.level_up_channel_id
            await session.commit()

        note = (
            ""
            if level_up_channel_id is not None
            else "\n\nTip: set where level-up announcements post with "
            "`/setup-levelup-channel`."
        )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="XP enabled" if not already else "XP already on",
                description=(
                    "Members now earn XP from messages and voice in this "
                    f"server.{note}"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="disable-xp",
        description="Turn XP earning OFF for this server",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable_xp(self, interaction: discord.Interaction) -> None:
        """Disable XP earning (message + voice) for this guild."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_xp_enabled(session, interaction.guild.id, False)
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="XP disabled",
                description=(
                    "Members no longer earn XP here. Existing XP and levels "
                    "are kept; run `/enable-xp` to resume."
                ),
                color=discord.Color.orange(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-roles",
        description="Assign a role to a level (level 0 = top-of-leaderboard role)",
    )
    @app_commands.describe(
        level=(
            "Level threshold (0 = top-of-leaderboard; "
            "1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100 are tier roles)"
        ),
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

        suggested = LEVEL_ROLE_HINTS.get(level)
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
            # the forced push below creates a fresh post in the new channel.
            await crud.set_leaderboard_message(
                session, interaction.guild.id, None
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Leaderboard channel set",
                description=(
                    f"Weekly leaderboard will post in {channel.mention}. "
                    "Posting the current standings now."
                ),
                color=discord.Color.blurple(),
            )
        )
        # Push the leaderboard immediately so the channel isn't empty until the
        # next update. force=True bypasses the cache comparison; the cleared
        # message ID above means this sends a fresh post in the new channel.
        leaderboard_cog = self.bot.get_cog("LeaderboardChannel")
        if leaderboard_cog is not None:
            await leaderboard_cog._check_and_maybe_update(
                interaction.guild.id, force=True
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
        name="disable-levelup-message",
        description="Stop the bot from posting level-up announcement messages",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable_levelup_message(
        self, interaction: discord.Interaction
    ) -> None:
        """Turn off level-up announcements (XP and roles keep working)."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_level_up_announcements_enabled(
                session, interaction.guild.id, False
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Level-up messages disabled",
                description=(
                    "The bot will no longer post level-up announcements. Members "
                    "still earn XP, level up, and get their ladder roles. Turn "
                    "them back on with `/enable-levelup-message`."
                ),
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="enable-levelup-message",
        description="Resume posting level-up announcement messages",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def enable_levelup_message(
        self, interaction: discord.Interaction
    ) -> None:
        """Turn level-up announcements back on."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_level_up_announcements_enabled(
                session, interaction.guild.id, True
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Level-up messages enabled",
                description="The bot will post level-up announcements again.",
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-welcome-channel",
        description="Set where welcome messages post when members join (opts in)",
    )
    @app_commands.describe(channel="Channel for welcome-on-join messages")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_welcome_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Set the welcome channel; presence of one enables welcome-on-join."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            has_custom = config.welcome_message is not None
            await crud.set_welcome_channel(
                session, interaction.guild.id, channel.id
            )
            await session.commit()
        note = (
            ""
            if has_custom
            else "\n\nUsing the default message — customise it with "
            "`/setup-welcome-message`, and set the intro link with "
            "`/setup-intro-channel`."
        )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome channel set",
                description=(
                    f"New members will be welcomed in {channel.mention}.{note}"
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-intro-channel",
        description="Set the channel the {intro_channel} placeholder links to",
    )
    @app_commands.describe(channel="The 'introduce yourself' channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_intro_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel referenced by ``{intro_channel}`` in the welcome."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_welcome_intro_channel(
                session, interaction.guild.id, channel.id
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Intro channel set",
                description=(
                    f"The `{{intro_channel}}` placeholder now links to "
                    f"{channel.mention}."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-welcome-message",
        description="Edit the welcome message shown when members join",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_welcome_message(
        self, interaction: discord.Interaction
    ) -> None:
        """Open a modal to edit the welcome message (prefilled with current)."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            current = config.welcome_message or DEFAULT_WELCOME_MESSAGE
            await session.commit()
        await interaction.response.send_modal(
            WelcomeMessageModal(interaction.guild.id, current)
        )

    @app_commands.command(
        name="disable-welcome",
        description="Stop posting welcome messages (keeps your saved text)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable_welcome(self, interaction: discord.Interaction) -> None:
        """Clear the welcome channel, turning the feature off."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_welcome_channel(session, interaction.guild.id, None)
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome disabled",
                description=(
                    "New members will no longer be welcomed. Your saved "
                    "welcome message is kept — run `/setup-welcome-channel` "
                    "to turn it back on."
                ),
                color=discord.Color.orange(),
            ),
            ephemeral=True,
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
        name="setup-channel-role",
        description="Give a role to anyone who talks in a channel",
    )
    @app_commands.describe(
        channel="Channel that grants the role",
        role="Role to grant on the member's first message there",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_channel_role(
        self,
        interaction: discord.Interaction,
        channel: GuildTextish,
        role: discord.Role,
    ) -> None:
        """Bind a role to a channel; members earn it by speaking there."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_channel_role(
                session, interaction.guild.id, channel.id, role.id
            )
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Channel role set",
                description=(
                    f"Anyone who sends a message in {channel.mention} will be "
                    f"given {role.mention}. The role stacks and is permanent.\n\n"
                    "Make sure prog's role is **above** "
                    f"{role.mention} in Server Settings → Roles."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="remove-channel-role",
        description="Stop a channel from granting its role",
    )
    @app_commands.describe(channel="Channel to unbind")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_channel_role(
        self,
        interaction: discord.Interaction,
        channel: GuildTextish,
    ) -> None:
        """Remove a channel's role binding (doesn't strip earned roles)."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            existed = await crud.remove_channel_role(
                session, interaction.guild.id, channel.id
            )
            await session.commit()
        if existed:
            desc = (
                f"{channel.mention} no longer grants a role. Members who "
                "already earned it keep it."
            )
            color = discord.Color.orange()
        else:
            desc = f"{channel.mention} had no role binding."
            color = discord.Color.greyple()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Channel role removed", description=desc, color=color
            ),
            ephemeral=True,
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

        # XP on/off — the master switch.
        embed.add_field(
            name="XP earning",
            value="✅ **Enabled**" if config.xp_enabled else "⛔ **Disabled** (run `/enable-xp`)",
            inline=False,
        )

        # Channels
        levelup_msgs = (
            "on" if config.level_up_announcements_enabled else "**off**"
        )
        ch_lines = [
            f"Level-up: {_format_channel(guild, config.level_up_channel_id)} "
            f"(messages: {levelup_msgs})",
            f"Leaderboard: {_format_channel(guild, config.leaderboard_channel_id)}",
        ]
        if config.leaderboard_message_id is not None:
            ch_lines.append(f"_(message id: `{config.leaderboard_message_id}`)_")
        embed.add_field(name="Channels", value="\n".join(ch_lines), inline=False)

        # Welcome-on-join
        is_custom = config.welcome_message is not None
        welcome_text = config.welcome_message or DEFAULT_WELCOME_MESSAGE
        # Embed field values cap at 1024 chars; leave room for the code fence.
        if len(welcome_text) > 950:
            welcome_text = welcome_text[:950] + "…"
        welcome_lines = [
            f"Channel: {_format_channel(guild, config.welcome_channel_id)}"
            + (
                ""
                if config.welcome_channel_id is not None
                else " _(disabled — run `/setup-welcome-channel`)_"
            ),
            f"Intro channel: {_format_channel(guild, config.welcome_intro_channel_id)}",
            "Message " + ("(**custom**):" if is_custom else "(_default_):"),
            f"```\n{welcome_text}\n```",
        ]
        embed.add_field(
            name="Welcome on join", value="\n".join(welcome_lines), inline=False
        )

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
                hint = LEVEL_ROLE_HINTS.get(r.level)
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

        # Channel roles (talk-here-get-this-role)
        if config.channel_roles:
            lines = [
                f"{_format_channel(guild, int(cid))} → {_format_role(guild, int(rid))}"
                for cid, rid in config.channel_roles.items()
            ]
            embed.add_field(
                name="Channel roles", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(
                name="Channel roles", value="_(none)_", inline=False
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

        # LeetCode (/leet feature)
        leet_lines = [
            f"Channel: {_format_channel(guild, config.leetcode_channel_id)}"
            + (
                ""
                if config.leetcode_channel_id is not None
                else " _(not set — run `/setup-leet`)_"
            ),
            f"Reward role: {_format_role(guild, config.leetcode_reward_role_id)}",
        ]
        embed.add_field(name="LeetCode (/leet)", value="\n".join(leet_lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /migrate-rank-roles - one-shot legacy-role rename + threshold remap
    # ------------------------------------------------------------------

    @app_commands.command(
        name="migrate-rank-roles",
        description=(
            "Rename legacy ladder roles to the new tier names and remap "
            "their thresholds. Safe to run repeatedly."
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def migrate_rank_roles(
        self, interaction: discord.Interaction
    ) -> None:
        """Rename old-name Discord roles in place and remap role_rewards
        thresholds. Idempotent: a second run is a no-op once everything
        is on the new naming/threshold scheme."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        renamed: list[str] = []
        rename_skipped: list[str] = []
        remapped: list[str] = []
        remap_skipped: list[str] = []

        for old_name, old_thresh, new_name, new_thresh in _LEGACY_ROLE_MIGRATIONS:
            await self._migrate_one_role(
                guild,
                old_name,
                new_name,
                renamed=renamed,
                skipped=rename_skipped,
            )
            await self._migrate_one_threshold(
                guild.id,
                old_thresh,
                new_thresh,
                remapped=remapped,
                skipped=remap_skipped,
            )

        def _bullet(items: list[str]) -> str:
            return "\n".join(f"• {it}" for it in items) if items else "_(none)_"

        embed = discord.Embed(
            title="Rank-role migration",
            description=(
                "Renamed legacy ladder roles to the new tier names and "
                f"remapped their role_rewards thresholds. The 6 new tiers "
                f"(Chud, Chad, Wiredin, Sigma, Ascendant, Aura) still need "
                f"to be created and bound via `/setup-roles`."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Roles renamed", value=_bullet(renamed), inline=False)
        embed.add_field(
            name="Roles skipped",
            value=_bullet(rename_skipped),
            inline=False,
        )
        embed.add_field(
            name="Thresholds remapped", value=_bullet(remapped), inline=False
        )
        embed.add_field(
            name="Thresholds skipped",
            value=_bullet(remap_skipped),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _migrate_one_role(
        self,
        guild: discord.Guild,
        old_name: str,
        new_name: str,
        *,
        renamed: list[str],
        skipped: list[str],
    ) -> None:
        """Rename a legacy ladder role to its new tier name.

        Skips silently when the legacy role doesn't exist, when a role
        with the new name already exists (so we don't create dupes), or
        when the bot lacks permission to rename it.
        """
        old_role = discord.utils.get(guild.roles, name=old_name)
        if old_role is None:
            return  # nothing to migrate; not a "skip" worth reporting
        existing_new = discord.utils.get(guild.roles, name=new_name)
        if existing_new is not None and existing_new.id != old_role.id:
            skipped.append(
                f"`{old_name}` -> `{new_name}` "
                f"(a role named `{new_name}` already exists)"
            )
            return
        try:
            await old_role.edit(name=new_name, reason="prog: rank-role migration")
        except discord.Forbidden:
            skipped.append(f"`{old_name}` (missing Manage Roles or role above bot)")
            return
        except discord.HTTPException as exc:
            skipped.append(f"`{old_name}` (HTTP error: {exc})")
            return
        renamed.append(f"`{old_name}` -> `{new_name}`")

    async def _migrate_one_threshold(
        self,
        guild_id: int,
        old_threshold: int,
        new_threshold: int,
        *,
        remapped: list[str],
        skipped: list[str],
    ) -> None:
        """Update a role_rewards row's ``level`` from old to new.

        Skips when there's nothing at the old level, when the new level
        is already occupied (an explicit conflict the admin should
        resolve), or when old == new (Freshie at level 1 stays at 1).
        """
        if old_threshold == new_threshold:
            return  # nothing to do
        async with get_session_factory()() as session:
            old_row = await crud.get_role_reward(session, guild_id, old_threshold)
            if old_row is None:
                await session.commit()
                return
            new_row = await crud.get_role_reward(session, guild_id, new_threshold)
            if new_row is not None:
                skipped.append(
                    f"L{old_threshold} -> L{new_threshold} "
                    f"(L{new_threshold} already configured; "
                    f"resolve manually with `/setup-roles`)"
                )
                await session.commit()
                return
            old_row.level = new_threshold
            await session.commit()
        remapped.append(f"L{old_threshold} -> L{new_threshold}")


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Admin(bot))
