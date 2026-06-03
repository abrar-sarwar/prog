"""Daily LeetCode Challenge cog.

Handles thread creation, question assignment, auto-expiration, OCR screenshot
verification, admin manual approval override, and analytics tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
import io
import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import crud
from db.engine import get_session_factory

log = logging.getLogger(__name__)


def verify_screenshot(image_bytes: bytes) -> bool:
    """Attempt OCR screenshot verification using pytesseract or easyocr."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)
        text_lower = text.lower()
        keywords = ["accepted", "success", "runtime", "memory"]
        log.info("OCR (pytesseract) text extracted: %s", text_lower[:200])
        return any(kw in text_lower for kw in keywords)
    except Exception as e:
        log.debug("pytesseract OCR attempt failed: %s", e)
        try:
            import easyocr
            import numpy as np
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            # Cache the reader in a production environment, but initialize lazily here
            reader = easyocr.Reader(['en'], gpu=False)
            results = reader.readtext(np.array(img))
            text_lower = " ".join([res[1] for res in results]).lower()
            keywords = ["accepted", "success", "runtime", "memory"]
            log.info("OCR (easyocr) text extracted: %s", text_lower[:200])
            return any(kw in text_lower for kw in keywords)
        except Exception as ex:
            log.debug("easyocr OCR attempt failed: %s", ex)
            return False


class LeetcodeVerificationView(discord.ui.View):
    """View with button controls for LeetCode screenshot verification."""

    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.guild_id = guild_id

    @discord.ui.button(
        label="Verify (Honor System)",
        style=discord.ButtonStyle.green,
        custom_id="leet_verify_user",
    )
    async def verify_user(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Allow the challenge author to verify themselves under the honor system."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who initiated this challenge can self-verify.",
                ephemeral=True,
            )
            return

        await self._process_completion(interaction)

    @discord.ui.button(
        label="Approve (Admin Only)",
        style=discord.ButtonStyle.blurple,
        custom_id="leet_approve_admin",
    )
    async def approve_admin(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Allow an administrator/moderator to override/approve verification."""
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            # Fallback check: check if user has a role with "admin", "moderator", or "mod" in its name
            for role in interaction.user.roles:
                name_lower = role.name.lower()
                if "admin" in name_lower or "moderator" in name_lower or "mod" in name_lower:
                    is_admin = True
                    break

        if not is_admin:
            await interaction.response.send_message(
                "Only administrators/moderators can approve this override.",
                ephemeral=True,
            )
            return

        await self._process_completion(interaction, approved_by=interaction.user)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.red,
        custom_id="leet_reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Allow user or admin to reject the submission / cancel the view."""
        if (
            not interaction.user.guild_permissions.manage_guild
            and interaction.user.id != self.user_id
        ):
            await interaction.response.send_message(
                "You cannot reject this.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Submission cancelled by {interaction.user.mention}."
        )
        self.stop()

    async def _process_completion(
        self, interaction: discord.Interaction, approved_by: discord.Member | None = None
    ) -> None:
        """Execute the completion transactions in DB and close the thread."""
        now = datetime.now(timezone.utc)
        session_factory = get_session_factory()
        async with session_factory() as session:
            # Complete the challenge
            user = await crud.complete_leetcode_session(
                session, self.guild_id, self.user_id, now
            )
            streak = user.leetcode_streak
            total = user.leetcode_total
            await session.commit()

        msg = "🎉 **LeetCode challenge completed!**"
        if approved_by:
            msg += f" (Manually approved by {approved_by.mention})"
        msg += (
            f"\n🔥 Streak: **{streak} days** | Total: **{total} problems**"
            f"\n⚡ XP Multiplier active: **1.5x for the next 24 hours!**"
        )

        await interaction.response.send_message(msg)

        # Archive/Lock the thread
        if isinstance(interaction.channel, discord.Thread):
            try:
                await interaction.channel.edit(archived=True, locked=True)
            except discord.Forbidden:
                log.warning("Missing permissions to archive thread %s", interaction.channel.id)
            except discord.HTTPException as exc:
                log.warning("Failed to archive thread %s: %s", interaction.channel.id, exc)

        self.stop()


class LeetcodeCog(commands.Cog):
    """Daily LeetCode Challenge cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.check_expirations.start()

    def cog_unload(self) -> None:
        """Stop background tasks on cog unload."""
        self.check_expirations.cancel()

    @tasks.loop(minutes=10)
    async def check_expirations(self) -> None:
        """Scan active sessions and expire threads older than 24 hours."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)

        session_factory = get_session_factory()
        async with session_factory() as session:
            expired_users = await crud.list_expired_leetcode_sessions(session, cutoff)
            for user in expired_users:
                guild = self.bot.get_guild(user.guild_id)
                if guild is None:
                    continue
                thread = guild.get_thread(user.leetcode_thread_id)
                if thread is None:
                    try:
                        thread = await guild.fetch_channel(user.leetcode_thread_id)
                    except discord.HTTPException:
                        pass

                if thread is not None:
                    try:
                        await thread.send(
                            "⏰ **Time is up!** This challenge has expired. "
                            "Run `/leet` in `#programming` to get today's question."
                        )
                        await thread.edit(archived=True, locked=True)
                    except Exception as exc:
                        log.warning(
                            "Failed to auto-expire thread %s: %s",
                            user.leetcode_thread_id,
                            exc,
                        )

                await crud.expire_leetcode_session(
                    session, user.guild_id, user.user_id, now
                )
            await session.commit()

    @check_expirations.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="leet",
        description="Receive a daily LeetCode challenge question in a new thread",
    )
    async def leet(self, interaction: discord.Interaction) -> None:
        """Initiate daily LeetCode challenge for the user."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        # Restrict to #programming channel
        channel_name = getattr(interaction.channel, "name", "")
        if channel_name != "programming":
            await interaction.response.send_message(
                "This command can only be used in the `#programming` channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        session_factory = get_session_factory()
        async with session_factory() as session:
            user = await crud.get_or_create_user(session, guild_id, user_id)

            # Check rolling 24h completion cooldown
            if user.leetcode_completed_at is not None:
                delta = now - user.leetcode_completed_at
                if delta.total_seconds() < 86400:
                    remaining = timedelta(seconds=86400 - delta.total_seconds())
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    await interaction.followup.send(
                        f"⏳ You have already completed today's LeetCode challenge. "
                        f"Try again in **{hours}h {minutes}m**.",
                        ephemeral=True,
                    )
                    await session.commit()
                    return

            # Check if there is an active session
            if user.leetcode_active_question_id is not None:
                # Check if it has expired (older than 24 hours)
                thread_age = (
                    now - user.leetcode_thread_created_at
                    if user.leetcode_thread_created_at
                    else timedelta(days=99)
                )
                if thread_age.total_seconds() >= 86400:
                    # Clean up expired session
                    await crud.expire_leetcode_session(session, guild_id, user_id, now)
                else:
                    await interaction.followup.send(
                        f"You already have an active question! Go to "
                        f"<#{user.leetcode_thread_id}> to submit your solution.",
                        ephemeral=True,
                    )
                    await session.commit()
                    return

            # Pick a random question
            question = await crud.get_random_leetcode_question(session)
            if question is None:
                await interaction.followup.send(
                    "❌ Error: No questions found in the database. "
                    "Ask an admin to run the seeding script.",
                    ephemeral=True,
                )
                await session.commit()
                return

            # Create thread off interaction channel
            assert isinstance(interaction.channel, discord.TextChannel)
            thread_name = f"LeetCode: {interaction.user.display_name} - {question.title}"
            try:
                # Explicitly set type to public_thread so it is unlocked and visible to everyone
                thread = await interaction.channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=1440,
                    reason=f"LeetCode daily challenge for {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Error: Missing permissions to create threads in this channel.",
                    ephemeral=True,
                )
                await session.commit()
                return

            # Assign question in DB
            await crud.assign_active_leetcode(
                session, guild_id, user_id, question.id, thread.id, now
            )
            await session.commit()

        # Welcome the user in the thread
        embed = discord.Embed(
            title=f"Daily LeetCode Challenge: {question.title}",
            url=question.url,
            color=discord.Color.green()
            if question.difficulty == "Easy"
            else (
                discord.Color.orange()
                if question.difficulty == "Medium"
                else discord.Color.red()
            ),
        )
        embed.add_field(name="Difficulty", value=question.difficulty, inline=True)
        embed.description = (
            f"Solve the problem on LeetCode: [Click Here]({question.url})\n\n"
            f"**To complete this challenge:**\n"
            f"1. Upload a screenshot of your accepted submission here.\n"
            f"2. Include a brief summary of your approach/solution.\n\n"
            f"⏳ You have **24 hours** to complete this task!"
        )
        await thread.send(content=interaction.user.mention, embed=embed)

        await interaction.followup.send(
            f"🎯 Created your daily challenge thread: <#{thread.id}>",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Scan messages in active LeetCode threads to verify screenshot submissions."""
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, discord.Thread):
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            user = await crud.get_active_leetcode_user_by_thread(
                session, message.channel.id
            )
            if user is None:
                await session.commit()
                return

            # Ensure it is the owner of the thread submitting
            if message.author.id != user.user_id:
                await session.commit()
                return

            # Check if there is an image attachment
            image_attachments = [
                a
                for a in message.attachments
                if a.content_type and a.content_type.startswith("image/")
            ]
            if not image_attachments:
                await session.commit()
                return

            attachment = image_attachments[0]
            try:
                img_bytes = await attachment.read()
            except discord.HTTPException as exc:
                log.warning("Failed to read image attachment: %s", exc)
                await session.commit()
                return

            # Auto-verify via OCR in background
            is_verified = verify_screenshot(img_bytes)

            if is_verified:
                # Complete the challenge immediately
                now = datetime.now(timezone.utc)
                completed_user = await crud.complete_leetcode_session(
                    session, message.guild.id, user.user_id, now
                )
                streak = completed_user.leetcode_streak
                total = completed_user.leetcode_total
                await session.commit()

                await message.channel.send(
                    f"✅ **Submission verified via OCR!**\n"
                    f"🎉 Daily challenge completed successfully!\n"
                    f"🔥 Streak: **{streak} days** | Total: **{total} problems**\n"
                    f"⚡ XP Multiplier active: **1.5x for the next 24 hours!**"
                )
                try:
                    await message.channel.edit(archived=True, locked=True)
                except Exception:
                    pass
            else:
                # Post the interactive buttons as a fallback / manual confirmation
                view = LeetcodeVerificationView(user.user_id, message.guild.id)
                await message.channel.send(
                    "🔍 **I detected a screenshot!** I was unable to automatically "
                    "verify if this shows an 'Accepted' submission. "
                    "Please verify or approve manually below:",
                    view=view,
                )
                await session.commit()

    @app_commands.command(
        name="leet-status",
        description="Show LeetCode statistics and multiplier status",
    )
    @app_commands.describe(user="The user whose stats to show (defaults to yourself)")
    async def leet_status(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Display streak, total completions, and daily boost status."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message(
                "Bots do not earn XP or solve LeetCode.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with get_session_factory()() as session:
            db_user = await crud.get_user(session, interaction.guild.id, target.id)
            question = None
            if db_user is not None and db_user.leetcode_active_question_id is not None:
                question = await crud.get_leetcode_question(
                    session, db_user.leetcode_active_question_id
                )

        if db_user is None:
            await interaction.followup.send(
                f"{target.display_name} has not initialized yet.", ephemeral=True
            )
            return

        now = datetime.now(timezone.utc)
        boost_active = "No"
        if db_user.leetcode_completed_at is not None:
            delta = now - db_user.leetcode_completed_at
            if delta.total_seconds() < 86400:
                remaining = timedelta(seconds=86400 - delta.total_seconds())
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                boost_active = f"✅ Yes (Expires in {hours}h {minutes}m)"

        embed = discord.Embed(
            title=f"LeetCode Status: {target.display_name}",
            color=discord.Color.green() if boost_active != "No" else discord.Color.orange(),
            timestamp=now,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Current Streak", value=f"🔥 {db_user.leetcode_streak} days", inline=True
        )
        embed.add_field(
            name="Total Solved",
            value=f"🏆 {db_user.leetcode_total} problems",
            inline=True,
        )
        embed.add_field(
            name="1.5x XP Boost Active", value=boost_active, inline=False
        )

        if question is not None:
            embed.add_field(
                name="Active Question",
                value=f"[{question.title}]({question.url}) ({question.difficulty})",
                inline=False,
            )
            if db_user.leetcode_thread_id is not None:
                embed.add_field(
                    name="Active Thread",
                    value=f"<#{db_user.leetcode_thread_id}>",
                    inline=False,
                )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leet-approve",
        description="Force-approve a user's active LeetCode daily challenge (Admin only)",
    )
    @app_commands.describe(user="The user whose challenge to approve")
    async def leet_approve(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Admin override command to immediately complete a user's active LeetCode challenge."""
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You don't have permission to run this command.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        now = datetime.now(timezone.utc)

        session_factory = get_session_factory()
        async with session_factory() as session:
            db_user = await crud.get_user(session, interaction.guild.id, user.id)
            if db_user is None or db_user.leetcode_active_question_id is None:
                await interaction.followup.send(
                    f"{user.display_name} does not have an active LeetCode challenge.",
                    ephemeral=True,
                )
                await session.commit()
                return

            thread_id = db_user.leetcode_thread_id
            await crud.complete_leetcode_session(
                session, interaction.guild.id, user.id, now
            )
            await session.commit()

        if thread_id is not None:
            try:
                thread = interaction.guild.get_thread(thread_id)
                if thread is None:
                    thread = await interaction.guild.fetch_channel(thread_id)
                if thread is not None:
                    await thread.send(
                        f"🎉 **Challenge manually approved by admin {interaction.user.mention}!**\n"
                        f"⚡ XP Multiplier active: **1.5x for the next 24 hours!**"
                    )
                    await thread.edit(archived=True, locked=True)
            except Exception as exc:
                log.warning("Failed to notify/archive thread %s on manual approval: %s", thread_id, exc)

        await interaction.followup.send(
            f"Successfully approved daily LeetCode challenge for {user.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="leet-analytics",
        description="Show analytics for LeetCode threads opened/closed/expired (Admin only)",
    )
    @app_commands.describe(period="Timeframe (day, week, month)")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Day", value="day"),
            app_commands.Choice(name="Week", value="week"),
            app_commands.Choice(name="Month", value="month"),
        ]
    )
    async def leet_analytics(
        self,
        interaction: discord.Interaction,
        period: str = "day",
    ) -> None:
        """Display stats on thread performance and conversion rates."""
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        if period == "day":
            since = now - timedelta(days=1)
        elif period == "week":
            since = now - timedelta(days=7)
        else:
            since = now - timedelta(days=30)

        async with get_session_factory()() as session:
            stats = await crud.get_leetcode_analytics(
                session, interaction.guild.id, since
            )

        rate = 0.0
        if stats["total"] > 0:
            rate = (stats["completed"] / stats["total"]) * 100

        embed = discord.Embed(
            title=f"LeetCode Analytics (Past {period.capitalize()})",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.add_field(name="Opened Threads", value=str(stats["opened"]), inline=True)
        embed.add_field(
            name="Completed Challenges",
            value=str(stats["completed"]),
            inline=True,
        )
        embed.add_field(
            name="Expired Challenges", value=str(stats["expired"]), inline=True
        )
        embed.add_field(
            name="Completion Rate", value=f"{rate:.1f}%", inline=True
        )
        embed.add_field(
            name="Total Sessions", value=str(stats["total"]), inline=True
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leet-modify",
        description="Modify a user's LeetCode statistics or reset their daily progress (Admin only)",
    )
    @app_commands.describe(
        user="The member to update",
        streak="New streak value (optional)",
        total="New total solved value (optional)",
        reset_daily="Reset daily completion status so they can get a new question today (optional)",
    )
    async def leet_modify(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        streak: int | None = None,
        total: int | None = None,
        reset_daily: bool | None = None,
    ) -> None:
        """Allow admins to update leetcode progress and streaks, or reset daily status."""
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You don't have permission to run this command.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        session_factory = get_session_factory()
        async with session_factory() as session:
            # Check if user exists
            db_user = await crud.get_user(session, interaction.guild.id, user.id)
            if db_user is None:
                await interaction.followup.send(
                    f"{user.display_name} has not initialized yet.", ephemeral=True
                )
                await session.commit()
                return

            # Keep track of old thread ID to archive if reset_daily is true
            old_thread_id = db_user.leetcode_thread_id

            # Apply modifications
            updated_user = await crud.modify_user_leetcode(
                session,
                interaction.guild.id,
                user.id,
                streak=streak,
                total=total,
                reset_daily=bool(reset_daily),
            )
            new_streak = updated_user.leetcode_streak
            new_total = updated_user.leetcode_total
            await session.commit()

        # Archive the old thread if reset_daily was true
        if reset_daily and old_thread_id is not None:
            try:
                thread = interaction.guild.get_thread(old_thread_id)
                if thread is None:
                    thread = await interaction.guild.fetch_channel(old_thread_id)
                if thread is not None:
                    await thread.send(
                        "🔒 **Thread closed**: Daily progress has been reset by an administrator. "
                        "You can now get a new question by running `/leet` in `#programming`."
                    )
                    await thread.edit(archived=True, locked=True)
            except Exception as exc:
                log.warning("Failed to close thread %s on admin reset: %s", old_thread_id, exc)

        embed = discord.Embed(
            title=f"LeetCode Progress Updated: {user.display_name}",
            color=discord.Color.green(),
        )
        embed.description = f"Successfully updated LeetCode statistics for {user.mention}."
        embed.add_field(name="New Streak", value=f"🔥 {new_streak} days", inline=True)
        embed.add_field(name="New Total Solved", value=f"🏆 {new_total} problems", inline=True)
        if reset_daily:
            embed.add_field(name="Daily Completion Reset", value="✅ Yes (Ready for a new question)", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(LeetcodeCog(bot))
