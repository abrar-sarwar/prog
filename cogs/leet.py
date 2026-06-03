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
* ``/leet``                  - start a session (gated on setup + verification +
  one-attempt-per-UTC-day).
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
from datetime import datetime, time, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.onboarding import ensure_member_initialized
from core.constants import (
    LEET_ARE_YOU_THERE_MINUTES,
    LEET_ARE_YOU_THERE_WAIT_SECONDS,
    LEET_GRANT_SWEEP_SECONDS,
    LEET_POLL_MAX_SECONDS,
    LEET_POLL_MIN_SECONDS,
    LEET_RECENT_AC_LIMIT,
    LEET_REWARD_ROLE_DURATION_HOURS,
    LEET_SESSION_MINUTES,
)
from core.leetcode import (
    code_present_in_bio,
    find_matching_submission,
    generate_verification_code,
)
from core.leetcode_client import (
    LeetCodeUnavailable,
    fetch_profile,
    fetch_recent_accepted,
)
from core.leetcode_problems import random_problem
from core.leveling import LEVEL_CAP
from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


def _utc_day_start(now: datetime) -> datetime:
    """Return midnight UTC for the day of ``now`` (for the one-per-day gate)."""
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


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


class _StillHereView(discord.ui.View):
    """'still here' button for the are-you-there check inside a session."""

    def __init__(self, user_id: int, event: asyncio.Event) -> None:
        super().__init__(timeout=float(LEET_ARE_YOU_THERE_WAIT_SECONDS))
        self.user_id = user_id
        self.event = event

    @discord.ui.button(label="yep, still on it", style=discord.ButtonStyle.green)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "this isn't your session.", ephemeral=True
            )
            return
        self.event.set()
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "nice, keep going. the hour doesn't reset though.", ephemeral=True
        )
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

    def cog_unload(self) -> None:
        """Cancel the sweep and all running session tasks on unload."""
        self.grant_sweep.cancel()
        for task in self._sessions.values():
            task.cancel()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Clean ephemeral replies for command errors (mirrors the other cogs)."""
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You need the **Manage Server** permission for this."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command here."
        else:
            log.exception("leet cog command error: %s", error)
            msg = "Something went wrong - check the bot logs."
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)

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
        name="reset-leet-daily",
        description="Clear a member's LeetCode daily attempt so they can run /leet again today",
    )
    @app_commands.describe(user="Member whose daily attempt to clear")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_leet_daily(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        """Wipe today's attempt(s) for a member (and any active session)."""
        assert interaction.guild is not None
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        now = datetime.now(timezone.utc)
        # Cancel + archive any in-flight session so nothing dangles.
        async with get_session_factory()() as session:
            active = await crud.get_active_assignment(session, user.id, guild.id)
            await session.commit()
        if active is not None:
            task = self._sessions.pop(active.id, None)
            if task is not None:
                task.cancel()
            if active.thread_id is not None:
                await self._archive_thread(active.thread_id)

        async with get_session_factory()() as session:
            removed = await crud.delete_assignments_since(
                session, user.id, guild.id, _utc_day_start(now)
            )
            await session.commit()

        await interaction.followup.send(
            embed=discord.Embed(
                title="leet daily reset",
                description=(
                    f"cleared {removed} attempt(s) today for {user.mention}. "
                    "they can run `/leet` again now."
                ),
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )

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

    # ------------------------------------------------------------------
    # /leetverify
    # ------------------------------------------------------------------

    @app_commands.command(
        name="leetverify",
        description="Link your LeetCode account so /leet can detect your solves",
    )
    @app_commands.describe(leetcode_username="Your LeetCode username")
    @app_commands.guild_only()
    async def leetverify(
        self,
        interaction: discord.Interaction,
        leetcode_username: str,
    ) -> None:
        """Start the bio-code verification flow for a LeetCode account."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        username = leetcode_username.strip().lstrip("@")
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
            title="one step to link your leetcode",
            description=(
                f"1. open your leetcode profile edit page\n"
                f"2. put this code anywhere in your **bio / summary**:\n\n"
                f"**`{code}`**\n\n"
                f"3. save it, then hit the button below\n\n"
                f"linking **{profile.username}**. you can delete the code right "
                "after it verifies."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /leet
    # ------------------------------------------------------------------

    @app_commands.command(
        name="leet",
        description="Get a random LeetCode problem to solve on leetcode.com",
    )
    @app_commands.guild_only()
    async def leet(self, interaction: discord.Interaction) -> None:
        """Start a /leet session: gate, pick a problem, open a private thread."""
        assert interaction.guild is not None
        guild = interaction.guild
        member = interaction.user
        assert isinstance(member, discord.Member)
        await interaction.response.defer(ephemeral=True, thinking=True)

        now = datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(session, guild.id)
            leet_channel_id = config.leetcode_channel_id
            link = await crud.get_leetcode_link(session, member.id)
            await session.commit()

        # Config-presence check.
        if leet_channel_id is None:
            await interaction.followup.send(
                "leetcode channel isn't set. an admin needs to run `/setup-leet` first.",
                ephemeral=True,
            )
            return
        # Channel-lock.
        if interaction.channel_id != leet_channel_id:
            await interaction.followup.send(
                f"`/leet` only works in <#{leet_channel_id}>.", ephemeral=True
            )
            return
        # Verification gate.
        if link is None:
            await interaction.followup.send(
                "link your leetcode first with `/leetverify <username>`.",
                ephemeral=True,
            )
            return

        # One attempt per UTC day + no double active session.
        async with get_session_factory()() as session:
            active = await crud.get_active_assignment(session, member.id, guild.id)
            attempts_today = await crud.count_assignments_since(
                session, member.id, guild.id, _utc_day_start(now)
            )
            await session.commit()
        if active is not None:
            where = f" <#{active.thread_id}>" if active.thread_id else ""
            await interaction.followup.send(
                f"you've already got an active leet session{where}. finish that one first.",
                ephemeral=True,
            )
            return
        if attempts_today >= 1:
            await interaction.followup.send(
                "you've used your leet attempt for today. come back tomorrow.",
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
        problem = random_problem(exclude_slugs=solved_slugs) or random_problem()
        if problem is None:
            await interaction.followup.send(
                "the problem pool is empty, tell an admin.", ephemeral=True
            )
            return

        # Open the private thread off the configured channel.
        channel = guild.get_channel(leet_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "the configured leetcode channel is missing or not a text channel. "
                "an admin should re-run `/setup-leet`.",
                ephemeral=True,
            )
            return
        try:
            thread = await channel.create_thread(
                name=f"leet · {member.display_name} · {problem.title}"[:100],
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
            return
        except discord.HTTPException as exc:
            log.warning("leet: thread creation failed in guild %s: %s", guild.id, exc)
            await interaction.followup.send(
                "discord wouldn't let me open your thread, try again in a bit.",
                ephemeral=True,
            )
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
        """Poll for the accepted submission until solve, expiry, or no-response.

        Polls the user's recent accepted submissions on a jittered interval and
        stops the instant the session completes or expires. At
        ``LEET_ARE_YOU_THERE_MINUTES`` in, pings the user once; if they don't
        confirm within ``LEET_ARE_YOU_THERE_WAIT_SECONDS`` polling stops early.
        Confirming does NOT extend the one-hour clock.
        """
        assigned_epoch = int(assigned_at.timestamp())
        are_you_there_at = assigned_at + timedelta(minutes=LEET_ARE_YOU_THERE_MINUTES)
        still_here = asyncio.Event()
        pinged = False
        ping_deadline: datetime | None = None
        try:
            while True:
                now = datetime.now(timezone.utc)
                if now >= expires_at:
                    await self._expire_session(assignment_id, thread_id, "time")
                    return
                if not pinged and now >= are_you_there_at:
                    pinged = True
                    ping_deadline = now + timedelta(
                        seconds=LEET_ARE_YOU_THERE_WAIT_SECONDS
                    )
                    await self._post_are_you_there(thread_id, member, still_here)
                if (
                    pinged
                    and not still_here.is_set()
                    and ping_deadline is not None
                    and now >= ping_deadline
                ):
                    await self._expire_session(
                        assignment_id, thread_id, "no response"
                    )
                    return

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

    async def _post_are_you_there(
        self, thread_id: int, member: discord.Member, event: asyncio.Event
    ) -> None:
        """Ping the user mid-session to confirm they're still working."""
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            return
        try:
            await thread.send(
                content=f"{member.mention} still working on it? tap the button so i "
                "keep watching.",
                view=_StillHereView(member.id, event),
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException as exc:
            log.warning("leet: are-you-there post failed in %s: %s", thread_id, exc)

    async def _expire_session(
        self, assignment_id: int, thread_id: int, reason: str
    ) -> None:
        """Mark an assignment expired and archive its thread."""
        async with get_session_factory()() as session:
            changed = await crud.expire_assignment(session, assignment_id)
            await session.commit()
        if not changed:
            return  # already completed/expired elsewhere
        thread = self.bot.get_channel(thread_id)
        if isinstance(thread, discord.Thread):
            note = (
                "time's up on this one. "
                if reason == "time"
                else "stopped watching since you went quiet. "
            )
            try:
                await thread.send(
                    f"{note}no accepted submission detected. you can try again tomorrow."
                )
                await thread.edit(archived=True)
            except discord.HTTPException as exc:
                log.warning("leet: expire/archive failed for %s: %s", thread_id, exc)
        log.info("leet session %s expired (%s)", assignment_id, reason)

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
        lines = [
            f"accepted, that's **+{result.reward_xp:,} xp**",
            f"streak: **{result.new_streak}** day(s), total solved: "
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
                await self._expire_session(a.id, a.thread_id or 0, "time")
                continue
            link_username = None
            async with get_session_factory()() as session:
                link = await crud.get_leetcode_link(session, a.discord_user_id)
                link_username = link.leetcode_username if link else None
                await session.commit()
            if link_username is None or a.expires_at <= now:
                await self._expire_session(a.id, a.thread_id, "time")
                continue
            self._start_session(
                member, a.id, a.thread_id, a.problem_slug,
                link_username, a.assigned_at, a.expires_at,
            )
            log.info("leet: resumed active session %s after restart", a.id)

    @tasks.loop(seconds=LEET_GRANT_SWEEP_SECONDS)
    async def grant_sweep(self) -> None:
        """Remove reward roles whose 24h grant has expired."""
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


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(Leet(bot))
