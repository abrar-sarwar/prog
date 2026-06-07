"""LeetCode website-solve feature (the /leet command).

A verified user runs ``/leet`` in the configured channel, gets a random real
LeetCode problem in a private thread, solves it on leetcode.com, and the bot
detects their accepted submission by polling their PUBLIC profile — no
screenshots, no pasted code, no OAuth. A confirmed solve pays streak-scaled XP
through the existing XP pipeline (feeding the rank card) and optionally grants a
timed reward role.

Commands:

* ``/leetverify <username>`` - link a LeetCode account by dropping a one-time
  ``progsu-XXXX`` code in the profile bio.
* ``/leet``                  - start a session (gated on setup + verification;
  no daily cap, but one active session at a time).
* ``/setup-leet <channel>``        - admin: set the designated channel.
* ``/setup-leet-reward <role>``    - admin: set the timed reward role.

Detection rule (see :func:`core.leetcode.find_matching_submission`): the
assigned problem's slug must appear in the user's recent accepted submissions
with a timestamp later than the assignment — so a pre-existing solve never
counts. Each assignment pays out at most once (the ``complete_assignment``
status guard).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.onboarding import ensure_member_initialized
from core import leetcode_tags
from core.constants import (
    LEET_EXTEND_MINUTES,
    LEET_EXTEND_PROMPT_BEFORE_SECONDS,
    LEET_GRANT_SWEEP_SECONDS,
    LEET_MAX_TAGS,
    LEET_POLL_MAX_SECONDS,
    LEET_POLL_MIN_SECONDS,
    LEET_POOL_REFRESH_HOURS,
    LEET_RECENT_AC_LIMIT,
    LEET_REWARD_ROLE_DURATION_HOURS,
    LEET_SESSION_MINUTES,
    LEET_TAG_QUERY_PAGE,
)
from core.leetcode import (
    code_present_in_bio,
    find_matching_submission,
    generate_verification_code,
)
from core.leetcode_client import (
    LeetCodeUnavailable,
    fetch_all_problems_payload,
    fetch_profile,
    fetch_questions_by_tags,
    fetch_recent_accepted,
)
from core.leetcode_problems import (
    parse_problem_list,
    parse_question_list,
    parse_question_total,
    pool_size,
    random_problem,
    replace_pool,
)
from core.leveling import LEVEL_CAP
from cogs.checks import (
    ProgsuvianRequired,
    progsuvian_required_message,
    requires_progsuvian,
)
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


async def _tag_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete /leet tag args against the real LeetCode topic list."""
    return [
        app_commands.Choice(name=name, value=slug)
        for name, slug in leetcode_tags.match_tags(current, 25)
    ]


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class _VerifyView(discord.ui.View):
    """Ephemeral 'I've added the code' button for the /leetverify flow."""

    def __init__(self, user_id: int, username: str, code: str) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.username = username
        self.code = code

    @discord.ui.button(label="I've added the code", style=discord.ButtonStyle.green)
    async def check(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "this isn't your verification.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            profile = await fetch_profile(self.username)
        except LeetCodeUnavailable as exc:
            log.warning("leetverify: profile read failed for %s: %s", self.username, exc)
            await interaction.followup.send(
                "leetcode isn't responding right now, give it a sec and hit the "
                "button again.",
                ephemeral=True,
            )
            return
        if profile is None:
            await interaction.followup.send(
                "couldn't read that profile anymore. is the username still right?",
                ephemeral=True,
            )
            return
        if not code_present_in_bio(self.code, profile.about_me):
            await interaction.followup.send(
                f"didn't spot `{self.code}` in your leetcode bio yet. make sure you "
                "saved it on your profile, then hit the button again.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            await crud.upsert_leetcode_link(
                session,
                discord_user_id=self.user_id,
                leetcode_username=profile.username,
                leetcode_username_key=profile.username.casefold(),
                verified_at=now,
            )
            await session.commit()

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            f"linked to **{profile.username}**. you can remove the code from your "
            "bio now. run `/leet` in the leetcode channel whenever you're ready.",
            ephemeral=True,
        )
        self.stop()


@dataclass
class _SessionClock:
    """Mutable deadline shared between a session's poll loop and its extend button.

    ``deadline`` is the live expiry the loop checks each tick; the button pushes
    it out by ``LEET_EXTEND_MINUTES``. ``prompted`` guards against posting more
    than one keep-alive prompt per window — it's reset on each extension so the
    loop arms a fresh prompt before the new deadline.
    """

    deadline: datetime
    prompted: bool = False


class _ExtendView(discord.ui.View):
    """'+30 min' keep-alive button posted shortly before a session expires."""

    def __init__(
        self,
        user_id: int,
        clock: "_SessionClock",
        on_extend: "Callable[[datetime], Awaitable[None]]",
    ) -> None:
        super().__init__(timeout=float(LEET_EXTEND_PROMPT_BEFORE_SECONDS))
        self.user_id = user_id
        self.clock = clock
        self.on_extend = on_extend  # persists the new deadline

    @discord.ui.button(label="yep, +30 min", style=discord.ButtonStyle.green)
    async def extend(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "this isn't your session.", ephemeral=True
            )
            return
        self.clock.deadline = self.clock.deadline + timedelta(
            minutes=LEET_EXTEND_MINUTES
        )
        self.clock.prompted = False  # let the loop arm a fresh prompt before the new deadline
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await self.on_extend(self.clock.deadline)
        await interaction.followup.send(
            f"extended — you've got until <t:{int(self.clock.deadline.timestamp())}:t> "
            f"(<t:{int(self.clock.deadline.timestamp())}:R>) now.",
            ephemeral=True,
        )
        self.stop()


class _ReverifyConfirmView(discord.ui.View):
    """Shown when an already-verified user runs /leetverify.

    'yes' keeps the existing link; 'no, reverify' re-runs the full code flow
    (against the username they passed to the command).
    """

    def __init__(
        self,
        cog: "Leet",
        user_id: int,
        stored_username: str,
        requested_username: str,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.stored_username = stored_username
        self.requested_username = requested_username

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "this isn't your verification.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="yes", style=discord.ButtonStyle.green)
    async def yes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"all good, still linked to **{self.stored_username}**.", ephemeral=True
        )
        self.stop()

    @discord.ui.button(label="no, reverify", style=discord.ButtonStyle.secondary)
    async def no(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        await self.cog._start_verify_flow(interaction, self.requested_username)
        self.stop()


class _ConfirmResetView(discord.ui.View):
    """'are you sure?' confirmation for resetting leet stats.

    ``target_user`` is the member to reset, or None to reset the whole guild.
    """

    def __init__(
        self,
        invoker_id: int,
        target_user: discord.Member | None,
    ) -> None:
        super().__init__(timeout=60)
        self.invoker_id = invoker_id
        self.target_user = target_user

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "this isn't your confirmation.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="yes, reset", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)

        guild_id = interaction.guild_id
        assert guild_id is not None
        async with get_session_factory()() as session:
            if self.target_user is not None:
                n = await crud.reset_leet_stats_for_user(
                    session, guild_id, self.target_user.id
                )
            else:
                n = await crud.reset_leet_stats_for_guild(session, guild_id)
            await session.commit()

        who = self.target_user.mention if self.target_user else "the whole server"
        await interaction.followup.send(
            f"reset leetcode stats for {who} ({n} member(s) affected). "
            "xp and levels were left as-is.",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("ok, nothing was reset.", ephemeral=True)
        self.stop()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class Leet(commands.Cog):
    """The /leet website-solve feature."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # assignment_id -> running detection task
        self._sessions: dict[int, asyncio.Task] = {}
        self._resumed = False
        self.grant_sweep.start()
        self.pool_refresh.start()

    def cog_unload(self) -> None:
        """Cancel the sweep, pool refresh, and all running session tasks."""
        self.grant_sweep.cancel()
        self.pool_refresh.cancel()
        for task in self._sessions.values():
            task.cancel()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Clean ephemeral replies for command errors (mirrors the other cogs)."""
        if isinstance(error, ProgsuvianRequired):
            msg = progsuvian_required_message(error.role_id)
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "You need the **Manage Server** permission for this."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command here."
        else:
            log.exception("leet cog command error: %s", error)
            msg = "Something went wrong - check the bot logs."
        none = discord.AllowedMentions.none()
        try:
            await interaction.response.send_message(
                msg, ephemeral=True, allowed_mentions=none
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                msg, ephemeral=True, allowed_mentions=none
            )

    # ------------------------------------------------------------------
    # Admin setup
    # ------------------------------------------------------------------

    @app_commands.command(
        name="setup-leet",
        description="Set the channel where /leet runs (required before the feature works)",
    )
    @app_commands.describe(channel="The channel members run /leet in")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_leet(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Set this guild's designated /leet channel (re-runnable to change it)."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_leetcode_channel(session, interaction.guild.id, channel.id)
            await session.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="leetcode channel set",
                description=(
                    f"`/leet` and `/leetverify` now work in {channel.mention}. "
                    "set a reward role with `/setup-leet-reward` if you want one."
                ),
                color=discord.Color.blurple(),
            )
        )

    @app_commands.command(
        name="setup-leet-reward",
        description="Set the role granted for 24h when someone completes a /leet solve",
    )
    @app_commands.describe(role="Role to hand out on a confirmed solve")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_leet_reward(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        """Set the timed reward role (re-runnable to change it)."""
        assert interaction.guild is not None
        async with get_session_factory()() as session:
            await crud.set_leetcode_reward_role(
                session, interaction.guild.id, role.id
            )
            await session.commit()

        # Warn if the role sits above the bot's top role — it can't be assigned.
        me = interaction.guild.me
        warn = ""
        if me is not None and role >= me.top_role:
            warn = (
                f"\n\nheads up: {role.mention} is above my highest role, so i "
                "can't assign it. move prog's role above it in Server Settings → Roles."
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="leetcode reward role set",
                description=(
                    f"solving a `/leet` problem now grants {role.mention} for "
                    f"{LEET_REWARD_ROLE_DURATION_HOURS}h (refreshes on re-earn).{warn}"
                ),
                color=discord.Color.blurple(),
            )
        )

    # ------------------------------------------------------------------
    # Admin overrides
    # ------------------------------------------------------------------

    @app_commands.command(
        name="approve-leet",
        description="Manually approve a member's active LeetCode session as solved",
    )
    @app_commands.describe(user="Member whose active session to approve")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def approve_leet(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        """Force a confirmed solve for a member's active session (detection override)."""
        assert interaction.guild is not None
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with get_session_factory()() as session:
            active = await crud.get_active_assignment(session, user.id, guild.id)
            await session.commit()
        if active is None:
            await interaction.followup.send(
                f"{user.mention} doesn't have an active leet session to approve.",
                ephemeral=True,
            )
            return

        # Stop the detector first so it can't also fire; the complete_assignment
        # guard inside _award_solve keeps the payout idempotent regardless.
        task = self._sessions.pop(active.id, None)
        if task is not None:
            task.cancel()
        await self._award_solve(user, active.id, active.thread_id or 0)

        await interaction.followup.send(
            f"approved {user.mention}'s solve for **{active.problem_title}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="leet-stats",
        description="List members who've completed /leet problems and their stats",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leet_stats(self, interaction: discord.Interaction) -> None:
        """Admin engagement view: who's done /leet, sorted by total solved."""
        assert interaction.guild is not None
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with get_session_factory()() as session:
            participants = list(
                await crud.list_leet_participants(session, guild.id, limit=25)
            )
            total = await crud.count_leet_participants(session, guild.id)
            await session.commit()

        if not participants:
            await interaction.followup.send(
                "nobody's completed a `/leet` problem yet.", ephemeral=True
            )
            return

        lines = []
        for i, u in enumerate(participants, start=1):
            member = guild.get_member(u.user_id)
            name = member.mention if member is not None else f"`{u.user_id}`"
            last = (
                u.leetcode_last_solve_date.isoformat()
                if u.leetcode_last_solve_date is not None
                else "never"
            )
            lines.append(
                f"{i}. {name}: **{u.leetcode_solved_total}** solved, "
                f"{u.leetcode_streak}d streak, last solve {last}"
            )

        embed = discord.Embed(
            title="leetcode participation",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=(
                f"{total} member(s) total"
                + (f", showing top {len(participants)}" if total > len(participants) else "")
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="reset-leet-stats",
        description="Reset LeetCode solved/streak stats for a member or the whole server",
    )
    @app_commands.describe(
        user="Member to reset (leave empty to reset everyone in this server)"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_leet_stats(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Confirm, then zero leet stats for one member or the whole guild."""
        assert interaction.guild is not None
        scope = user.mention if user is not None else "**everyone in this server**"
        embed = discord.Embed(
            title="reset leetcode stats?",
            description=(
                f"this resets solved count, streak, and last solve date for {scope}. "
                "earned xp and levels are not touched. this can't be undone."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=_ConfirmResetView(interaction.user.id, user),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /leetverify
    # ------------------------------------------------------------------

    @app_commands.command(
        name="leetverify",
        description="Link your LeetCode account so /leet can detect your solves",
    )
    @app_commands.describe(leetcode_username="Your LeetCode username")
    @app_commands.guild_only()
    @requires_progsuvian()
    async def leetverify(
        self,
        interaction: discord.Interaction,
        leetcode_username: str,
    ) -> None:
        """Link a LeetCode account, or confirm/re-link if already verified."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            leet_channel_id = config.leetcode_channel_id
            link = await crud.get_leetcode_link(session, interaction.user.id)
            await session.commit()

        # Config-presence gate: nothing in the feature works until /setup-leet.
        if leet_channel_id is None:
            await interaction.followup.send(
                "leetcode channel isn't set. an admin needs to run `/setup-leet` first.",
                ephemeral=True,
            )
            return
        # Channel-lock: verification only happens in the configured channel.
        if interaction.channel_id != leet_channel_id:
            await interaction.followup.send(
                f"`/leetverify` only works in <#{leet_channel_id}>.", ephemeral=True
            )
            return

        # Verify-once: if they already have a link, don't re-run the flow —
        # confirm it's still their account, with a reverify escape hatch.
        if link is not None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="leetcode verified",
                    description=(
                        f"is your user still **{link.leetcode_username}**?"
                    ),
                    color=discord.Color.blurple(),
                ),
                view=_ReverifyConfirmView(
                    self,
                    interaction.user.id,
                    link.leetcode_username,
                    leetcode_username,
                ),
                ephemeral=True,
            )
            return

        await self._start_verify_flow(interaction, leetcode_username)

    async def _start_verify_flow(
        self, interaction: discord.Interaction, raw_username: str
    ) -> None:
        """Validate the username and post the code + steps + verify button.

        Shared by first-time /leetverify and the reverify button. The caller
        must have already deferred/responded to ``interaction`` so followups send.
        """
        username = raw_username.strip().lstrip("@")
        if not username:
            await interaction.followup.send(
                "give me your leetcode username, e.g. `/leetverify uwi`.",
                ephemeral=True,
            )
            return

        try:
            profile = await fetch_profile(username)
        except LeetCodeUnavailable as exc:
            log.warning("leetverify: profile read failed for %s: %s", username, exc)
            await interaction.followup.send(
                "leetcode isn't responding right now, try again in a bit.",
                ephemeral=True,
            )
            return
        if profile is None:
            await interaction.followup.send(
                f"couldn't find a leetcode user called `{username}`. check the spelling?",
                ephemeral=True,
            )
            return

        key = profile.username.casefold()
        async with get_session_factory()() as session:
            owner = await crud.get_leetcode_link_by_username_key(session, key)
            await session.commit()
        if owner is not None and owner.discord_user_id != interaction.user.id:
            await interaction.followup.send(
                "that leetcode account is already linked to another discord user. "
                "if it's really yours, ask an admin for help.",
                ephemeral=True,
            )
            return

        code = generate_verification_code()
        view = _VerifyView(interaction.user.id, profile.username, code)
        embed = discord.Embed(
            title="link your leetcode",
            description=(
                f"1. open your leetcode profile edit page\n"
                f"2. scroll down til you see readme, click on it and put the "
                f"following code:\n\n"
                f"**`{code}`**\n\n"
                f"3. save it, then hit the button below\n\n"
                f"linking **{profile.username}**. you can delete the code right "
                "after it verifies."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # Shared gate + thread helpers (used by /leet and /daily)
    # ------------------------------------------------------------------

    async def _check_leet_preconditions(
        self, interaction: discord.Interaction
    ) -> tuple[int, "crud.LeetcodeLink"] | None:
        """Shared /leet + /daily gate. Returns (leet_channel_id, link) or None.

        On failure it sends the ephemeral reply and returns None. Caller must
        have already deferred the interaction. Channel-presence, channel-lock,
        and verification are enforced here; the progsuvian gate is the command
        decorator.
        """
        assert interaction.guild is not None
        member = interaction.user
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            leet_channel_id = config.leetcode_channel_id
            link = await crud.get_leetcode_link(session, member.id)
            await session.commit()
        if leet_channel_id is None:
            await interaction.followup.send(
                "leetcode channel isn't set. an admin needs to run `/setup-leet` first.",
                ephemeral=True,
            )
            return None
        if interaction.channel_id != leet_channel_id:
            await interaction.followup.send(
                f"this only works in <#{leet_channel_id}>.", ephemeral=True
            )
            return None
        if link is None:
            await interaction.followup.send(
                "link your leetcode first with `/leetverify <username>`.",
                ephemeral=True,
            )
            return None
        return leet_channel_id, link

    async def _open_session_thread(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        leet_channel_id: int,
        member: discord.Member,
        problem,
        label: str,
    ) -> discord.Thread | None:
        """Open the private thread for a session. Returns the thread or None
        (after sending the ephemeral error). ``label`` prefixes the thread name
        (e.g. 'leet' or 'daily')."""
        channel = guild.get_channel(leet_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "the configured leetcode channel is missing or not a text channel. "
                "an admin should re-run `/setup-leet`.",
                ephemeral=True,
            )
            return None
        try:
            thread = await channel.create_thread(
                name=f"{label} · {member.display_name} · {problem.title}"[:100],
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=1440,
            )
            await thread.add_user(member)
        except discord.Forbidden:
            await interaction.followup.send(
                "i can't create a private thread here. an admin needs to give me "
                "**Create Private Threads** (and **Send Messages in Threads**) in "
                "this channel.",
                ephemeral=True,
            )
            return None
        except discord.HTTPException as exc:
            log.warning(
                "leet: thread creation failed in guild %s: %s", guild.id, exc
            )
            await interaction.followup.send(
                "discord wouldn't let me open your thread, try again in a bit.",
                ephemeral=True,
            )
            return None
        return thread

    async def _pick_tagged_problem(
        self, tag_slugs: list[str], exclude_slugs: set
    ):
        """Pick a random free problem carrying all ``tag_slugs``.

        Queries LeetCode for the filtered total, then a random page, and picks a
        free problem not in ``exclude_slugs``. Returns a LeetProblem, or None if
        nothing matches / LeetCode is unavailable.
        """
        try:
            head = await fetch_questions_by_tags(tag_slugs, limit=1, skip=0)
        except LeetCodeUnavailable as exc:
            log.warning("leet: tag query (head) failed for %s: %s", tag_slugs, exc)
            return None
        total = parse_question_total(head)
        if total <= 0:
            return None
        skip = random.randint(0, max(0, total - 1))
        start = min(skip, max(0, total - LEET_TAG_QUERY_PAGE))
        try:
            page = await fetch_questions_by_tags(
                tag_slugs, limit=LEET_TAG_QUERY_PAGE, skip=start
            )
        except LeetCodeUnavailable as exc:
            log.warning("leet: tag query (page) failed for %s: %s", tag_slugs, exc)
            return None
        problems = parse_question_list(page)
        candidates = [p for p in problems if p.slug not in exclude_slugs]
        if not candidates:
            candidates = problems  # everything recently solved; allow a repeat
        if not candidates:
            return None
        return random.choice(candidates)

    # ------------------------------------------------------------------
    # /leet
    # ------------------------------------------------------------------

    @app_commands.command(
        name="leet",
        description="Practice: a random LeetCode problem, optionally scoped to topics",
    )
    @app_commands.describe(
        tag1="Optional topic to scope the problem",
        tag2="Optional second topic",
        tag3="Optional third topic",
    )
    @app_commands.autocomplete(
        tag1=_tag_autocomplete, tag2=_tag_autocomplete, tag3=_tag_autocomplete
    )
    @app_commands.guild_only()
    @requires_progsuvian()
    async def leet(
        self,
        interaction: discord.Interaction,
        tag1: str | None = None,
        tag2: str | None = None,
        tag3: str | None = None,
    ) -> None:
        """Start a /leet session: gate, pick a problem, open a private thread."""
        assert interaction.guild is not None
        guild = interaction.guild
        member = interaction.user
        assert isinstance(member, discord.Member)
        await interaction.response.defer(ephemeral=True, thinking=True)

        now = datetime.now(timezone.utc)
        pre = await self._check_leet_preconditions(interaction)
        if pre is None:
            return
        leet_channel_id, link = pre

        # One active session at a time (daily or practice).
        async with get_session_factory()() as session:
            active = await crud.get_active_assignment(session, member.id, guild.id)
            await session.commit()
        if active is not None:
            where = f" <#{active.thread_id}>" if active.thread_id else ""
            await interaction.followup.send(
                f"you've already got an active session{where}. finish that one first.",
                ephemeral=True,
            )
            return

        # Pre-check: avoid handing out a problem they recently solved.
        try:
            recent = await fetch_recent_accepted(
                link.leetcode_username, LEET_RECENT_AC_LIMIT
            )
        except LeetCodeUnavailable as exc:
            log.warning("leet: recent-AC pre-check failed for %s: %s", member.id, exc)
            await interaction.followup.send(
                "couldn't reach leetcode to set up your problem, try again in a bit.",
                ephemeral=True,
            )
            return
        solved_slugs = {r.get("titleSlug") for r in recent if r.get("titleSlug")}

        # Resolve requested tags (slug from autocomplete, or typed name/slug).
        requested = [t for t in (tag1, tag2, tag3) if t]
        tag_slugs: list[str] = []
        for raw in requested:
            slug = leetcode_tags.normalize_tag(raw)
            if slug is None:
                await interaction.followup.send(
                    f"`{raw}` isn't a known leetcode topic. start typing to pick "
                    "one from the list.",
                    ephemeral=True,
                )
                return
            if slug not in tag_slugs:
                tag_slugs.append(slug)
        tag_slugs = tag_slugs[:LEET_MAX_TAGS]

        if tag_slugs:
            problem = await self._pick_tagged_problem(tag_slugs, solved_slugs)
            if problem is None:
                pretty = ", ".join(f"`{s}`" for s in tag_slugs)
                await interaction.followup.send(
                    f"no free problems match {pretty} together. try dropping a tag.",
                    ephemeral=True,
                )
                return
        else:
            problem = random_problem(exclude_slugs=solved_slugs) or random_problem()
        if problem is None:
            await interaction.followup.send(
                "the problem pool is empty, tell an admin.", ephemeral=True
            )
            return

        thread = await self._open_session_thread(
            interaction, guild, leet_channel_id, member, problem, "leet"
        )
        if thread is None:
            return

        expires_at = now + timedelta(minutes=LEET_SESSION_MINUTES)
        async with get_session_factory()() as session:
            assignment = await crud.create_assignment(
                session,
                discord_user_id=member.id,
                guild_id=guild.id,
                problem_slug=problem.slug,
                problem_title=problem.title,
                difficulty=problem.difficulty,
                assigned_at=now,
                expires_at=expires_at,
                thread_id=thread.id,
            )
            assignment_id = assignment.id
            await session.commit()

        await self._post_problem(thread, member, problem, expires_at)
        await interaction.followup.send(
            f"your problem's in {thread.mention}. good luck.", ephemeral=True
        )

        self._start_session(
            member, assignment_id, thread.id, problem.slug,
            link.leetcode_username, now, expires_at,
        )

    async def _post_problem(
        self,
        thread: discord.Thread,
        member: discord.Member,
        problem,
        expires_at: datetime,
    ) -> None:
        """Post the assigned problem into the private thread."""
        embed = discord.Embed(
            title=problem.title,
            url=problem.url,
            description=(
                f"difficulty: **{problem.difficulty}**\n\n"
                f"solve it on leetcode and get it **accepted**. i'll detect it "
                f"automatically, no screenshots needed.\n"
                f"you've got until <t:{int(expires_at.timestamp())}:t> "
                f"(<t:{int(expires_at.timestamp())}:R>)."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="DISCLAIMER",
            value=(
                "you won't be monitored if you used AI or not, please keep in mind "
                "that you are responsible of your own education"
            ),
            inline=False,
        )
        try:
            await thread.send(
                content=(
                    f"{member.mention} here's your problem. want a buddy? @mention "
                    "them in here, i won't add anyone myself."
                ),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException as exc:
            log.warning("leet: failed to post problem in thread %s: %s", thread.id, exc)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _start_session(
        self,
        member: discord.Member,
        assignment_id: int,
        thread_id: int,
        slug: str,
        username: str,
        assigned_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Spawn (and track) the detection task for one assignment."""
        task = asyncio.create_task(
            self._run_session(
                member, assignment_id, thread_id, slug, username,
                assigned_at, expires_at,
            )
        )
        self._sessions[assignment_id] = task

    async def _run_session(
        self,
        member: discord.Member,
        assignment_id: int,
        thread_id: int,
        slug: str,
        username: str,
        assigned_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Poll for the accepted submission until solve or the (extendable) expiry.

        Polls the user's recent accepted submissions on a jittered interval and
        stops the instant the session completes or its deadline passes. The base
        window is ``LEET_SESSION_MINUTES``; ``LEET_EXTEND_PROMPT_BEFORE_SECONDS``
        before the current deadline the loop posts a '+30 min' button. Tapping it
        pushes ``clock.deadline`` out by ``LEET_EXTEND_MINUTES`` and re-arms the
        prompt before the new deadline, so a member can keep extending. Ignoring
        it lets the session expire normally.
        """
        assigned_epoch = int(assigned_at.timestamp())
        clock = _SessionClock(deadline=expires_at)
        prompt_lead = timedelta(seconds=LEET_EXTEND_PROMPT_BEFORE_SECONDS)
        try:
            while True:
                now = datetime.now(timezone.utc)
                if now >= clock.deadline:
                    await self._expire_session(assignment_id, thread_id)
                    return
                if not clock.prompted and now >= clock.deadline - prompt_lead:
                    clock.prompted = True
                    await self._post_extend_prompt(
                        thread_id, member, clock, assignment_id
                    )

                try:
                    recent = await fetch_recent_accepted(
                        username, LEET_RECENT_AC_LIMIT
                    )
                    if find_matching_submission(slug, assigned_epoch, recent):
                        await self._award_solve(member, assignment_id, thread_id)
                        return
                except LeetCodeUnavailable as exc:
                    log.warning(
                        "leet poll: transient failure for assignment %s: %s",
                        assignment_id,
                        exc,
                    )

                await asyncio.sleep(
                    random.uniform(LEET_POLL_MIN_SECONDS, LEET_POLL_MAX_SECONDS)
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # never let a session task die silently
            log.exception("leet session %s crashed", assignment_id)
        finally:
            self._sessions.pop(assignment_id, None)

    async def _post_extend_prompt(
        self,
        thread_id: int,
        member: discord.Member,
        clock: _SessionClock,
        assignment_id: int,
    ) -> None:
        """Post the '+30 min' keep-alive button shortly before the deadline."""
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            return

        async def _persist(new_deadline: datetime) -> None:
            # Persist the extension so a restart resumes with the new deadline.
            async with get_session_factory()() as session:
                await crud.extend_assignment_expiry(
                    session, assignment_id, new_deadline
                )
                await session.commit()

        try:
            await thread.send(
                content=(
                    f"{member.mention} still on it? tap to add "
                    f"{LEET_EXTEND_MINUTES} more minutes — otherwise this wraps up "
                    f"<t:{int(clock.deadline.timestamp())}:R>."
                ),
                view=_ExtendView(member.id, clock, _persist),
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException as exc:
            log.warning("leet: extend prompt failed in %s: %s", thread_id, exc)

    async def _expire_session(self, assignment_id: int, thread_id: int) -> None:
        """Mark an assignment expired (deadline passed) and archive its thread."""
        async with get_session_factory()() as session:
            changed = await crud.expire_assignment(session, assignment_id)
            await session.commit()
        if not changed:
            return  # already completed/expired elsewhere
        thread = self.bot.get_channel(thread_id)
        if isinstance(thread, discord.Thread):
            try:
                await thread.send(
                    "time's up on this one — no accepted submission detected. "
                    "you can run `/leet` again whenever."
                )
                await thread.edit(archived=True)
            except discord.HTTPException as exc:
                log.warning("leet: expire/archive failed for %s: %s", thread_id, exc)
        log.info("leet session %s expired (time)", assignment_id)

    # ------------------------------------------------------------------
    # Reward
    # ------------------------------------------------------------------

    async def _award_solve(
        self, member: discord.Member, assignment_id: int, thread_id: int
    ) -> None:
        """Pay out a confirmed solve: XP, role, hype, confirmation, archive.

        ``complete_assignment`` is the idempotency guard — if the row is no
        longer active (already paid or expired) we bail without paying again.
        """
        guild = member.guild
        now = datetime.now(timezone.utc)
        today = now.date()
        await ensure_member_initialized(member)

        async with get_session_factory()() as session:
            if not await crud.complete_assignment(session, assignment_id, now):
                await session.commit()
                return
            result = await crud.record_leet_solve(session, guild.id, member.id, today)
            change = result.change
            aura_already_fired = change.user.aura_message_fired
            if (
                change.leveled_up
                and change.new_level >= LEVEL_CAP
                and not aura_already_fired
            ):
                change.user.aura_message_fired = True
            config = await crud.get_or_create_guild_config(session, guild.id)
            reward_role_id = config.leetcode_reward_role_id
            leet_channel_id = config.leetcode_channel_id
            await session.commit()

        # Feed the leaderboard + ladder roles through the existing events.
        self.bot.dispatch("xp_grant", guild.id)
        if change.leveled_up:
            self.bot.dispatch(
                "level_change", member, change.old_level, change.new_level
            )

        role_note = await self._grant_reward_role(member, reward_role_id, now)
        await self._post_hype(guild, leet_channel_id, member)
        await self._post_solve_confirmation(thread_id, member, result, role_note)
        await self._archive_thread(thread_id)
        log.info(
            "leet solve: guild=%s user=%s reward=%d streak=%d level=%d->%d total_solved=%d",
            guild.id,
            member.id,
            result.reward_xp,
            result.new_streak,
            change.old_level,
            change.new_level,
            change.user.leetcode_solved_total,
        )

    async def _grant_reward_role(
        self, member: discord.Member, role_id: int | None, now: datetime
    ) -> str:
        """Grant + track the timed reward role. Returns a note for the user.

        No reward role configured -> XP still paid, role skipped. A role above
        the bot's top role can't be assigned -> caught, told to fix ordering.
        """
        if role_id is None:
            return "no reward role is set (an admin can add one with `/setup-leet-reward`)."
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning(
                "leet reward role %s configured in guild %s but not found",
                role_id,
                member.guild.id,
            )
            return "the configured reward role is missing, tell an admin."
        try:
            await member.add_roles(role, reason="prog: leet solve reward")
        except discord.Forbidden:
            log.error(
                "leet reward: FORBIDDEN adding %s (id=%s) to %s. move prog's role "
                "above it in Server Settings → Roles.",
                role.name,
                role.id,
                member.id,
            )
            return (
                f"couldn't give you {role.mention}. prog's role needs to sit above "
                "it (admin fix)."
            )
        except discord.HTTPException as exc:
            log.warning("leet reward: HTTP error adding role to %s: %s", member.id, exc)
            return "couldn't add the reward role (discord error)."

        expires_at = now + timedelta(hours=LEET_REWARD_ROLE_DURATION_HOURS)
        async with get_session_factory()() as session:
            await crud.upsert_leet_role_grant(
                session, member.guild.id, member.id, role.id, now, expires_at
            )
            await session.commit()
        return f"you've got {role.mention} for the next {LEET_REWARD_ROLE_DURATION_HOURS}h."

    async def _post_hype(
        self, guild: discord.Guild, channel_id: int | None, member: discord.Member
    ) -> None:
        """Post the short public brag line in the configured channel."""
        if channel_id is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            await channel.send(
                f"{member.mention} did a leetcode problem, lfgg",
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException as exc:
            log.warning("leet: hype post failed in %s: %s", channel_id, exc)

    async def _post_solve_confirmation(
        self, thread_id: int, member: discord.Member, result, role_note: str
    ) -> None:
        """Post the detailed breakdown in the user's own thread."""
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            return
        change = result.change
        if result.first_of_day:
            lines = [
                f"accepted, that's **+{result.reward_xp:,} xp**",
                f"streak: **{result.new_streak}** day(s), total solved: "
                f"**{change.user.leetcode_solved_total}**",
            ]
        else:
            lines = [
                f"accepted — extra solve today, **+{result.reward_xp:,} xp**",
                f"streak still **{result.new_streak}** day(s), total solved: "
                f"**{change.user.leetcode_solved_total}**",
            ]
        if change.leveled_up:
            lines.append(f"you leveled up to **{change.new_level}**")
        lines.append(role_note)
        try:
            await thread.send("\n".join(lines))
        except discord.HTTPException as exc:
            log.warning("leet: confirmation post failed in %s: %s", thread_id, exc)

    async def _archive_thread(self, thread_id: int) -> None:
        """Archive a thread on completion (best-effort)."""
        thread = self.bot.get_channel(thread_id)
        if isinstance(thread, discord.Thread):
            try:
                await thread.edit(archived=True)
            except discord.HTTPException as exc:
                log.warning("leet: archive failed for %s: %s", thread_id, exc)

    # ------------------------------------------------------------------
    # Background tasks: resume sessions + reap expired role grants
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Resume active sessions after a restart (once per process)."""
        if self._resumed:
            return
        self._resumed = True
        now = datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            active = list(await crud.list_active_assignments(session))
            await session.commit()
        for a in active:
            guild = self.bot.get_guild(a.guild_id)
            member = guild.get_member(a.discord_user_id) if guild else None
            if guild is None or member is None or a.thread_id is None:
                # Can't resume — expire it so it doesn't dangle forever.
                await self._expire_session(a.id, a.thread_id or 0)
                continue
            link_username = None
            async with get_session_factory()() as session:
                link = await crud.get_leetcode_link(session, a.discord_user_id)
                link_username = link.leetcode_username if link else None
                await session.commit()
            if link_username is None or a.expires_at <= now:
                await self._expire_session(a.id, a.thread_id)
                continue
            self._start_session(
                member, a.id, a.thread_id, a.problem_slug,
                link_username, a.assigned_at, a.expires_at,
            )
            log.info("leet: resumed active session %s after restart", a.id)

    @tasks.loop(seconds=LEET_GRANT_SWEEP_SECONDS)
    async def grant_sweep(self) -> None:
        """Remove reward roles whose 24h grant has expired.

        Wrapped so a transient DB error (e.g. a pooler hiccup) is logged and the
        loop survives to the next tick — an unhandled exception would otherwise
        stop the loop for the process's lifetime.
        """
        try:
            await self._sweep_expired_grants()
        except Exception:
            log.exception("leet grant_sweep tick failed; will retry next tick")

    async def _sweep_expired_grants(self) -> None:
        now = datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            expired = list(await crud.list_expired_role_grants(session, now))
            await session.commit()
        for grant in expired:
            guild = self.bot.get_guild(grant.guild_id)
            member = guild.get_member(grant.user_id) if guild else None
            role = guild.get_role(grant.role_id) if guild else None
            if guild is not None and member is not None and role is not None:
                try:
                    await member.remove_roles(role, reason="prog: leet reward expired")
                except discord.HTTPException as exc:
                    log.warning(
                        "leet: failed to remove expired reward role %s from %s: %s",
                        grant.role_id,
                        grant.user_id,
                        exc,
                    )
            async with get_session_factory()() as session:
                await crud.delete_role_grant(session, grant.id)
                await session.commit()

    @grant_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Background task: refresh the all-free problem pool from LeetCode
    # ------------------------------------------------------------------

    @tasks.loop(hours=LEET_POOL_REFRESH_HOURS)
    async def pool_refresh(self) -> None:
        """Refresh the /leet problem pool from LeetCode's public list.

        Runs once right after the gateway is ready (the snapshot loaded at import
        covers /leet until then), then every ``LEET_POOL_REFRESH_HOURS``. A failed
        or empty fetch keeps the current pool, so /leet never goes problem-less.
        """
        try:
            payload = await fetch_all_problems_payload()
        except LeetCodeUnavailable as exc:
            log.warning(
                "leet: problem pool refresh failed (%s); keeping %d problems",
                exc,
                pool_size(),
            )
            return
        problems = parse_problem_list(payload)
        if replace_pool(problems):
            log.info("leet: problem pool refreshed — %d free problems", pool_size())
        else:
            log.warning(
                "leet: problem list parsed empty; keeping %d problems", pool_size()
            )

    @pool_refresh.before_loop
    async def _before_pool_refresh(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Leet(bot))
