"""All database reads and writes.

Cogs must NEVER use ``session.execute(...)`` directly - all DB access goes
through this module. That gives us one place to optimise, one place to
audit, and keeps cog code focused on Discord logic.

Transaction discipline
----------------------
None of these functions commit. Callers wrap their work in either
``async with session_factory() as session:  ... ; await session.commit()``
or ``async with session_factory.begin() as session: ...`` (which commits on
clean exit). Functions that mutate a row first take an exclusive row lock
via ``SELECT ... FOR UPDATE`` so that concurrent XP grants don't stomp on
each other.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.leetcode import compute_daily_payout, compute_practice_payout
from core.leveling import LEVEL_CAP, cumulative_xp_to_level, level_from_total_xp
from db.models import (
    GuildConfig,
    LeetcodeAssignment,
    LeetcodeLink,
    LeetcodeRoleGrant,
    RoleReward,
    User,
)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


class LevelChange(NamedTuple):
    """Result of any XP mutation.

    Callers check ``leveled_up`` to decide whether to fire role rewards and
    a level-up notification.
    """

    user: User
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        """True if the new level is strictly greater than the old one."""
        return self.new_level > self.old_level


# ---------------------------------------------------------------------------
# guild_config
# ---------------------------------------------------------------------------


async def get_guild_config(
    session: AsyncSession, guild_id: int
) -> GuildConfig | None:
    """Return the GuildConfig row for ``guild_id`` or None if absent."""
    stmt = select(GuildConfig).where(GuildConfig.guild_id == guild_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_or_create_guild_config(
    session: AsyncSession, guild_id: int
) -> GuildConfig:
    """Return the GuildConfig row, creating an empty one if needed.

    Uses a SAVEPOINT so a race-losing INSERT does not poison the outer
    transaction.
    """
    existing = await get_guild_config(session, guild_id)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            session.add(GuildConfig(guild_id=guild_id))
    except IntegrityError:
        # Concurrent task won the race; fall through to the re-select.
        pass
    fresh = await get_guild_config(session, guild_id)
    assert fresh is not None  # we either just inserted it or someone else did
    return fresh


async def list_guild_configs_with_leaderboard_channel(
    session: AsyncSession,
) -> Sequence[GuildConfig]:
    """Return every guild that has a leaderboard channel configured."""
    stmt = select(GuildConfig).where(GuildConfig.leaderboard_channel_id.is_not(None))
    return (await session.execute(stmt)).scalars().all()


async def set_progsuvian_role(
    session: AsyncSession, guild_id: int, role_id: int | None
) -> GuildConfig:
    """Set (or clear, with None) the progsuvian base-role ID for a guild."""
    config = await get_or_create_guild_config(session, guild_id)
    config.progsuvian_role_id = role_id
    return config


async def set_xp_enabled(
    session: AsyncSession, guild_id: int, enabled: bool
) -> GuildConfig:
    """Turn XP earning on or off for a guild (the opt-in master switch)."""
    config = await get_or_create_guild_config(session, guild_id)
    config.xp_enabled = enabled
    return config


async def set_level_up_announcements_enabled(
    session: AsyncSession, guild_id: int, enabled: bool
) -> GuildConfig:
    """Turn level-up announcement messages on or off for a guild.

    Gates only the announcement post; XP, levels, and ladder-role rewards keep
    working regardless."""
    config = await get_or_create_guild_config(session, guild_id)
    config.level_up_announcements_enabled = enabled
    return config


async def set_level_up_channel(
    session: AsyncSession, guild_id: int, channel_id: int | None
) -> GuildConfig:
    """Set (or clear) the channel that level-up announcements are posted to.

    Also turns XP on for the guild: configuring where announcements go is one
    of the ways an admin opts the server in (the dedicated toggle is
    ``/enable-xp``)."""
    config = await get_or_create_guild_config(session, guild_id)
    config.level_up_channel_id = channel_id
    if channel_id is not None:
        config.xp_enabled = True
    return config


async def set_welcome_channel(
    session: AsyncSession, guild_id: int, channel_id: int | None
) -> GuildConfig:
    """Set (or clear, with None) the channel welcome messages are posted to.

    Setting a channel opts the guild into welcome-on-join; clearing it (None)
    turns the feature off while keeping any saved welcome message text."""
    config = await get_or_create_guild_config(session, guild_id)
    config.welcome_channel_id = channel_id
    return config


async def set_welcome_intro_channel(
    session: AsyncSession, guild_id: int, channel_id: int | None
) -> GuildConfig:
    """Set (or clear) the channel the ``{intro_channel}`` placeholder links to."""
    config = await get_or_create_guild_config(session, guild_id)
    config.welcome_intro_channel_id = channel_id
    return config


async def set_welcome_message(
    session: AsyncSession, guild_id: int, message: str | None
) -> GuildConfig:
    """Set (or clear) the per-guild welcome message override.

    ``None`` clears the override so the default template is used again."""
    config = await get_or_create_guild_config(session, guild_id)
    config.welcome_message = message
    return config


async def set_leaderboard_channel(
    session: AsyncSession, guild_id: int, channel_id: int | None
) -> GuildConfig:
    """Set (or clear) the channel the weekly leaderboard is posted in."""
    config = await get_or_create_guild_config(session, guild_id)
    config.leaderboard_channel_id = channel_id
    return config


async def set_leaderboard_message(
    session: AsyncSession, guild_id: int, message_id: int | None
) -> GuildConfig:
    """Record the message ID of the auto-updating leaderboard post."""
    config = await get_or_create_guild_config(session, guild_id)
    config.leaderboard_message_id = message_id
    return config


async def set_channel_role(
    session: AsyncSession, guild_id: int, channel_id: int, role_id: int
) -> None:
    """Bind ``role_id`` to ``channel_id`` (overwrites any existing binding).

    A member who sends a qualifying message in the channel is granted the
    role on their first message there. Stored as ``{channel_id_str: role_id}``.
    """
    config = await get_or_create_guild_config(session, guild_id)
    # Reassign (not mutate) so SQLAlchemy detects the JSONB change.
    bindings = dict(config.channel_roles or {})
    bindings[str(channel_id)] = role_id
    config.channel_roles = bindings


async def remove_channel_role(
    session: AsyncSession, guild_id: int, channel_id: int
) -> bool:
    """Remove a channel's role binding. Returns True iff a binding existed.

    Does NOT strip the role from members who already earned it.
    """
    config = await get_or_create_guild_config(session, guild_id)
    bindings = dict(config.channel_roles or {})
    existed = bindings.pop(str(channel_id), None) is not None
    if existed:
        config.channel_roles = bindings
    return existed


async def toggle_channel_blacklist(
    session: AsyncSession, guild_id: int, channel_id: int
) -> bool:
    """Toggle a channel's XP-blacklist status. Returns the new state (True =
    now blacklisted, False = no longer blacklisted)."""
    config = await get_or_create_guild_config(session, guild_id)
    # Normalise existing entries to int for safe comparison; the column is
    # JSONB so values can come back as int or numeric-string.
    current = [int(c) for c in config.xp_blacklist]
    if channel_id in current:
        config.xp_blacklist = [c for c in current if c != channel_id]
        return False
    config.xp_blacklist = current + [channel_id]
    return True


async def set_channel_multiplier(
    session: AsyncSession,
    guild_id: int,
    channel_id: int,
    multiplier: float,
) -> None:
    """Set or remove the channel multiplier. ``multiplier == 1.0`` removes it."""
    config = await get_or_create_guild_config(session, guild_id)
    updated = dict(config.channel_multipliers)
    if multiplier == 1.0:
        updated.pop(str(channel_id), None)
    else:
        updated[str(channel_id)] = float(multiplier)
    config.channel_multipliers = updated


async def set_role_multiplier(
    session: AsyncSession,
    guild_id: int,
    role_id: int,
    multiplier: float,
) -> None:
    """Set or remove the role multiplier. ``multiplier == 1.0`` removes it."""
    config = await get_or_create_guild_config(session, guild_id)
    updated = dict(config.role_multipliers)
    if multiplier == 1.0:
        updated.pop(str(role_id), None)
    else:
        updated[str(role_id)] = float(multiplier)
    config.role_multipliers = updated


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


async def get_user(
    session: AsyncSession, guild_id: int, user_id: int
) -> User | None:
    """Return the User row for (guild, user) or None."""
    stmt = select(User).where(
        User.guild_id == guild_id, User.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession, guild_id: int, user_id: int
) -> User:
    """Return the User row, creating it (xp=0, level=1) if needed."""
    existing = await get_user(session, guild_id, user_id)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            session.add(User(guild_id=guild_id, user_id=user_id))
    except IntegrityError:
        pass
    fresh = await get_user(session, guild_id, user_id)
    assert fresh is not None
    return fresh


async def set_saved_roles(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    role_ids: list[int],
) -> User:
    """Store the role IDs a member had at departure, for restore on rejoin.

    Get-or-creates the user row (a member can leave before ever earning XP) and
    reassigns ``saved_role_ids`` so SQLAlchemy detects the JSONB change. Caller
    commits.
    """
    user = await get_or_create_user(session, guild_id, user_id)
    user.saved_role_ids = list(role_ids)
    return user


async def _lock_user(
    session: AsyncSession, guild_id: int, user_id: int
) -> User:
    """``SELECT ... FOR UPDATE`` the user row. Raises if it does not exist -
    XP mutators rely on ``ensure_member_initialized`` having created the row
    first."""
    stmt = (
        select(User)
        .where(User.guild_id == guild_id, User.user_id == user_id)
        .with_for_update()
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise LookupError(
            f"User {user_id} in guild {guild_id} has no row; "
            "ensure_member_initialized must be called before granting XP"
        )
    return user


async def add_text_xp(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    amount: int,
    now: datetime,
) -> LevelChange:
    """Grant text XP, bump ``text_xp_total`` and ``last_message_at``, recompute level."""
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    user.xp = user.xp + amount
    user.text_xp_total = user.text_xp_total + amount
    user.last_message_at = now
    user.level = level_from_total_xp(user.xp)
    return LevelChange(user=user, old_level=old_level, new_level=user.level)


async def add_voice_xp(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    amount: int,
) -> LevelChange:
    """Grant voice XP, bump ``voice_xp_total``, recompute level."""
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    user.xp = user.xp + amount
    user.voice_xp_total = user.voice_xp_total + amount
    user.level = level_from_total_xp(user.xp)
    return LevelChange(user=user, old_level=old_level, new_level=user.level)


async def add_admin_xp(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    delta: int,
) -> LevelChange:
    """Admin XP adjustment (signed). Floors total XP at 0; does NOT touch
    ``text_xp_total`` or ``voice_xp_total`` - those are source attribution
    only and shouldn't be polluted by admin overrides."""
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    user.xp = max(0, user.xp + delta)
    user.level = level_from_total_xp(user.xp)
    return LevelChange(user=user, old_level=old_level, new_level=user.level)


async def set_user_xp(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    total: int,
) -> LevelChange:
    """Set the user's total XP directly and recompute level."""
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    user.xp = max(0, total)
    user.level = level_from_total_xp(user.xp)
    return LevelChange(user=user, old_level=old_level, new_level=user.level)


async def set_user_level(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    target_level: int,
) -> LevelChange:
    """Set the user to the minimum XP for ``target_level``.

    ``target_level`` is clamped to ``[0, LEVEL_CAP]`` so an admin can't
    overshoot the curve.
    """
    if target_level < 0:
        raise ValueError(f"target_level must be >= 0, got {target_level}")
    target_level = min(target_level, LEVEL_CAP)
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    user.xp = cumulative_xp_to_level(target_level)
    user.level = target_level
    return LevelChange(user=user, old_level=old_level, new_level=target_level)


async def mark_aura_message_fired(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
) -> None:
    """Set the one-shot Aura announcement flag for this user.

    Idempotent - flipping an already-true flag is a no-op. Callers must
    hold the user row lock (e.g. via a prior :func:`_lock_user` in the
    same transaction) so this stays race-free with concurrent XP grants.
    """
    user = await get_user(session, guild_id, user_id)
    if user is None:
        return
    user.aura_message_fired = True


async def get_top_users(
    session: AsyncSession,
    guild_id: int,
    limit: int,
    offset: int = 0,
) -> Sequence[User]:
    """Return the top users in a guild, ordered by xp desc, id asc (stable tiebreak)."""
    stmt = (
        select(User)
        .where(User.guild_id == guild_id)
        .order_by(User.xp.desc(), User.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).scalars().all()


async def count_users_in_guild(session: AsyncSession, guild_id: int) -> int:
    """Return how many user rows exist for this guild."""
    stmt = select(func.count(User.id)).where(User.guild_id == guild_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_users_in_guild(
    session: AsyncSession, guild_id: int
) -> Sequence[User]:
    """Return every user row for this guild, unordered. Used by /resync-roles."""
    stmt = select(User).where(User.guild_id == guild_id)
    return (await session.execute(stmt)).scalars().all()


async def get_users_around_rank(
    session: AsyncSession,
    guild_id: int,
    target_rank: int,
    window: int = 5,
) -> tuple[int, Sequence[User]]:
    """Return ``(start_rank, users)`` for a window of size ``window`` centered
    on ``target_rank``, with edge clamping at both ends.

    ``start_rank`` is the 1-indexed absolute rank of the first returned row
    so the caller can render rank numbers correctly without recomputing the
    offset.

    Examples (window=5):

    * 10 users, target=3   -> (1, ranks 1-5)
    * 10 users, target=8   -> (6, ranks 6-10)
    * 10 users, target=10  -> (6, ranks 6-10)   # top-edge clamp
    * 3 users, target=2    -> (1, ranks 1-3)    # fewer rows than window

    Uses the standard leaderboard ordering (xp DESC, id ASC). One COUNT
    plus one SELECT per call.
    """
    total = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.guild_id == guild_id)
            )
        ).scalar_one()
    )
    if total == 0:
        return (1, [])

    half = (window - 1) // 2
    desired_offset = (target_rank - 1) - half
    max_offset = max(0, total - window)
    offset = max(0, min(desired_offset, max_offset))

    stmt = (
        select(User)
        .where(User.guild_id == guild_id)
        .order_by(User.xp.desc(), User.id.asc())
        .limit(window)
        .offset(offset)
    )
    users = (await session.execute(stmt)).scalars().all()
    return (offset + 1, users)


async def get_user_rank(
    session: AsyncSession, guild_id: int, user_id: int
) -> int | None:
    """Return the user's 1-indexed rank in their guild, or None if no row.

    Tiebreaker matches ``get_top_users`` (xp desc, then id asc)."""
    user = await get_user(session, guild_id, user_id)
    if user is None:
        return None
    stmt = (
        select(func.count(User.id))
        .where(User.guild_id == guild_id)
        .where(
            or_(
                User.xp > user.xp,
                and_(User.xp == user.xp, User.id < user.id),
            )
        )
    )
    ahead = int((await session.execute(stmt)).scalar_one())
    return ahead + 1


# ---------------------------------------------------------------------------
# role_rewards
# ---------------------------------------------------------------------------


async def upsert_role_reward(
    session: AsyncSession,
    guild_id: int,
    level: int,
    role_id: int,
) -> None:
    """Set the role granted at ``level`` for this guild. ``level == 0`` is the
    top-of-leaderboard role assigned by the weekly updater; ``level >= 1`` are
    level-up rewards."""
    stmt = (
        pg_insert(RoleReward)
        .values(guild_id=guild_id, level=level, role_id=role_id)
        .on_conflict_do_update(
            constraint="uq_role_rewards_guild_level",
            set_={"role_id": role_id},
        )
    )
    await session.execute(stmt)


async def get_role_reward(
    session: AsyncSession, guild_id: int, level: int
) -> RoleReward | None:
    """Return the RoleReward row for ``(guild_id, level)``, or None."""
    stmt = select(RoleReward).where(
        RoleReward.guild_id == guild_id, RoleReward.level == level
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_role_rewards(
    session: AsyncSession, guild_id: int
) -> Sequence[RoleReward]:
    """Return every role-reward row for this guild, ordered by level."""
    stmt = (
        select(RoleReward)
        .where(RoleReward.guild_id == guild_id)
        .order_by(RoleReward.level.asc())
    )
    return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# LeetCode feature: per-guild config
# ---------------------------------------------------------------------------


async def set_leetcode_channel(
    session: AsyncSession, guild_id: int, channel_id: int | None
) -> GuildConfig:
    """Set (or clear, with None) the /leet designated channel for a guild.

    Nothing in the LeetCode feature runs until this is set via /setup-leet."""
    config = await get_or_create_guild_config(session, guild_id)
    config.leetcode_channel_id = channel_id
    return config


async def set_leetcode_reward_role(
    session: AsyncSession, guild_id: int, role_id: int | None
) -> GuildConfig:
    """Set (or clear, with None) the timed reward role granted on a /leet solve."""
    config = await get_or_create_guild_config(session, guild_id)
    config.leetcode_reward_role_id = role_id
    return config


# ---------------------------------------------------------------------------
# LeetCode feature: account links
# ---------------------------------------------------------------------------


async def get_leetcode_link(
    session: AsyncSession, discord_user_id: int
) -> LeetcodeLink | None:
    """Return the verified LeetCode link for a Discord user, or None."""
    stmt = select(LeetcodeLink).where(
        LeetcodeLink.discord_user_id == discord_user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_leetcode_link_by_username_key(
    session: AsyncSession, username_key: str
) -> LeetcodeLink | None:
    """Return the link owning ``username_key`` (lowercased username), or None.

    Used to stop two Discord users from claiming the same LeetCode account."""
    stmt = select(LeetcodeLink).where(
        LeetcodeLink.leetcode_username_key == username_key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_leetcode_link(
    session: AsyncSession,
    discord_user_id: int,
    leetcode_username: str,
    leetcode_username_key: str,
    verified_at: datetime,
    leetcode_internal_id: str | None = None,
) -> LeetcodeLink:
    """Create or update a Discord user's verified LeetCode link.

    Re-verifying overwrites username/key (handles a LeetCode username change).
    """
    link = await get_leetcode_link(session, discord_user_id)
    if link is None:
        link = LeetcodeLink(discord_user_id=discord_user_id)
        session.add(link)
    link.leetcode_username = leetcode_username
    link.leetcode_username_key = leetcode_username_key
    link.leetcode_internal_id = leetcode_internal_id
    link.verified_at = verified_at
    return link


# ---------------------------------------------------------------------------
# LeetCode feature: assignments (sessions)
# ---------------------------------------------------------------------------


async def get_active_assignment(
    session: AsyncSession, discord_user_id: int, guild_id: int
) -> LeetcodeAssignment | None:
    """Return the user's currently-active assignment in this guild, or None."""
    stmt = select(LeetcodeAssignment).where(
        LeetcodeAssignment.discord_user_id == discord_user_id,
        LeetcodeAssignment.guild_id == guild_id,
        LeetcodeAssignment.status == "active",
    )
    return (await session.execute(stmt)).scalars().first()


async def get_completed_daily_for_date(
    session: AsyncSession, discord_user_id: int, guild_id: int, daily_date: date
) -> LeetcodeAssignment | None:
    """Return the user's completed daily-challenge assignment for ``daily_date``
    in this guild, or None. Used to block a second /daily on the same UTC day."""
    stmt = select(LeetcodeAssignment).where(
        LeetcodeAssignment.discord_user_id == discord_user_id,
        LeetcodeAssignment.guild_id == guild_id,
        LeetcodeAssignment.kind == "daily",
        LeetcodeAssignment.daily_date == daily_date,
        LeetcodeAssignment.status == "completed",
    )
    return (await session.execute(stmt)).scalars().first()


async def count_practice_solves_today(
    session: AsyncSession, discord_user_id: int, guild_id: int, today: date
) -> int:
    """Count completed practice (/leet) solves for this user/guild on ``today``
    (UTC). Drives the practice daily cap in :func:`compute_practice_payout`."""
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    stmt = select(func.count(LeetcodeAssignment.id)).where(
        LeetcodeAssignment.discord_user_id == discord_user_id,
        LeetcodeAssignment.guild_id == guild_id,
        LeetcodeAssignment.kind == "practice",
        LeetcodeAssignment.status == "completed",
        LeetcodeAssignment.completed_at >= start,
        LeetcodeAssignment.completed_at < end,
    )
    return int((await session.execute(stmt)).scalar_one())


async def create_assignment(
    session: AsyncSession,
    *,
    discord_user_id: int,
    guild_id: int,
    problem_slug: str,
    problem_title: str,
    difficulty: str,
    assigned_at: datetime,
    expires_at: datetime,
    thread_id: int | None = None,
    kind: str = "practice",
    daily_date: date | None = None,
) -> LeetcodeAssignment:
    """Insert a new active assignment row and return it."""
    assignment = LeetcodeAssignment(
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        problem_slug=problem_slug,
        problem_title=problem_title,
        difficulty=difficulty,
        assigned_at=assigned_at,
        expires_at=expires_at,
        thread_id=thread_id,
        status="active",
        kind=kind,
        daily_date=daily_date,
    )
    session.add(assignment)
    await session.flush()  # populate assignment.id for the caller
    return assignment


async def get_assignment(
    session: AsyncSession, assignment_id: int
) -> LeetcodeAssignment | None:
    """Return an assignment by id, or None."""
    return await session.get(LeetcodeAssignment, assignment_id)


async def set_assignment_thread(
    session: AsyncSession, assignment_id: int, thread_id: int
) -> None:
    """Record the private-thread id for an assignment."""
    assignment = await session.get(LeetcodeAssignment, assignment_id)
    if assignment is not None:
        assignment.thread_id = thread_id


async def list_active_assignments(
    session: AsyncSession,
) -> Sequence[LeetcodeAssignment]:
    """Return every active assignment across all guilds (for the reaper/resume)."""
    stmt = select(LeetcodeAssignment).where(
        LeetcodeAssignment.status == "active"
    )
    return (await session.execute(stmt)).scalars().all()


async def complete_assignment(
    session: AsyncSession, assignment_id: int, completed_at: datetime
) -> bool:
    """Mark an assignment completed. Returns False if it wasn't active anymore.

    The active-status guard makes the payout idempotent: a second concurrent
    detection that races in finds the row already non-active and bails, so the
    same assignment never pays out twice."""
    assignment = await session.get(LeetcodeAssignment, assignment_id)
    if assignment is None or assignment.status != "active":
        return False
    assignment.status = "completed"
    assignment.completed_at = completed_at
    return True


async def expire_assignment(
    session: AsyncSession, assignment_id: int
) -> bool:
    """Mark an assignment expired. Returns False if it wasn't active."""
    assignment = await session.get(LeetcodeAssignment, assignment_id)
    if assignment is None or assignment.status != "active":
        return False
    assignment.status = "expired"
    return True


async def extend_assignment_expiry(
    session: AsyncSession, assignment_id: int, new_expires_at: datetime
) -> bool:
    """Push an active assignment's deadline out to ``new_expires_at``.

    Persists a keep-alive extension so a bot restart resumes the session with the
    extended deadline rather than the original one. No-op (returns False) if the
    row is no longer active."""
    assignment = await session.get(LeetcodeAssignment, assignment_id)
    if assignment is None or assignment.status != "active":
        return False
    assignment.expires_at = new_expires_at
    return True


# ---------------------------------------------------------------------------
# LeetCode feature: solve recording (XP + stats, race-safe)
# ---------------------------------------------------------------------------


class LeetSolveResult(NamedTuple):
    """Outcome of recording a confirmed solve."""

    change: LevelChange
    reward_xp: int
    new_streak: int
    kind: str
    capped: bool


async def record_leet_solve(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    today: date,
    *,
    kind: str = "practice",
    practice_solves_today: int = 0,
) -> LeetSolveResult:
    """Apply a confirmed solve (XP + streak + total), atomically.

    Locks the user row and derives the payout by kind:
    ``daily`` pays the streak-scaled multiplier (:func:`compute_daily_payout`);
    ``practice`` pays the reduced, capped rate (:func:`compute_practice_payout`,
    using ``practice_solves_today``). Either way the solved total is bumped, the
    streak is kept alive, XP flows through the same ``xp``/level pipeline as
    messages and voice, and the level is recomputed.
    """
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    if kind == "daily":
        payout = compute_daily_payout(
            user.leetcode_last_solve_date, today, user.leetcode_streak, old_level
        )
    else:
        payout = compute_practice_payout(
            user.leetcode_last_solve_date,
            today,
            user.leetcode_streak,
            old_level,
            practice_solves_today,
        )
    user.xp = user.xp + payout.reward_xp
    user.leetcode_solved_total = user.leetcode_solved_total + 1
    user.leetcode_streak = payout.new_streak
    user.leetcode_last_solve_date = today
    user.level = level_from_total_xp(user.xp)
    return LeetSolveResult(
        change=LevelChange(user=user, old_level=old_level, new_level=user.level),
        reward_xp=payout.reward_xp,
        new_streak=payout.new_streak,
        kind=payout.kind,
        capped=payout.capped,
    )


# ---------------------------------------------------------------------------
# LeetCode feature: timed reward-role grants
# ---------------------------------------------------------------------------


async def upsert_leet_role_grant(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    role_id: int,
    granted_at: datetime,
    expires_at: datetime,
) -> None:
    """Record (or refresh) a timed reward-role grant.

    Re-earning while a grant is still active refreshes ``granted_at``/
    ``expires_at`` instead of stacking, via the unique (guild, user, role) key.
    """
    stmt = (
        pg_insert(LeetcodeRoleGrant)
        .values(
            guild_id=guild_id,
            user_id=user_id,
            role_id=role_id,
            granted_at=granted_at,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            constraint="uq_leet_role_grants_guild_user_role",
            set_={"granted_at": granted_at, "expires_at": expires_at},
        )
    )
    await session.execute(stmt)


async def list_expired_role_grants(
    session: AsyncSession, now: datetime
) -> Sequence[LeetcodeRoleGrant]:
    """Return all grants whose ``expires_at`` is at/before ``now``."""
    stmt = select(LeetcodeRoleGrant).where(
        LeetcodeRoleGrant.expires_at <= now
    )
    return (await session.execute(stmt)).scalars().all()


async def delete_role_grant(session: AsyncSession, grant_id: int) -> None:
    """Delete a role-grant row (after the role has been removed)."""
    grant = await session.get(LeetcodeRoleGrant, grant_id)
    if grant is not None:
        await session.delete(grant)


# ---------------------------------------------------------------------------
# LeetCode feature: engagement reads (admin)
# ---------------------------------------------------------------------------


async def list_leet_participants(
    session: AsyncSession, guild_id: int, limit: int = 25
) -> Sequence[User]:
    """Return users in a guild who've solved at least one /leet problem.

    Ordered by total solved desc, then current streak desc, then id (stable).
    """
    stmt = (
        select(User)
        .where(User.guild_id == guild_id, User.leetcode_solved_total > 0)
        .order_by(
            User.leetcode_solved_total.desc(),
            User.leetcode_streak.desc(),
            User.id.asc(),
        )
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def count_leet_participants(session: AsyncSession, guild_id: int) -> int:
    """Count users in a guild who've solved at least one /leet problem."""
    stmt = select(func.count(User.id)).where(
        User.guild_id == guild_id, User.leetcode_solved_total > 0
    )
    return int((await session.execute(stmt)).scalar_one())


_LEET_STATS_RESET = {
    "leetcode_solved_total": 0,
    "leetcode_streak": 0,
    "leetcode_last_solve_date": None,
}
"""Default values the /leet stats reset writes. XP/level are deliberately
untouched — only the leet engagement counters are cleared."""


async def reset_leet_stats_for_user(
    session: AsyncSession, guild_id: int, user_id: int
) -> int:
    """Zero a single member's leet stats. Returns rows affected (0 or 1)."""
    stmt = (
        update(User)
        .where(User.guild_id == guild_id, User.user_id == user_id)
        .values(**_LEET_STATS_RESET)
    )
    return (await session.execute(stmt)).rowcount or 0


async def reset_leet_stats_for_guild(
    session: AsyncSession, guild_id: int
) -> int:
    """Zero leet stats for every member in a guild who has any.

    Returns the number of members actually reset (only rows with non-default
    stats are touched, so the count reflects real participants)."""
    stmt = (
        update(User)
        .where(
            User.guild_id == guild_id,
            or_(
                User.leetcode_solved_total != 0,
                User.leetcode_streak != 0,
                User.leetcode_last_solve_date.is_not(None),
            ),
        )
        .values(**_LEET_STATS_RESET)
    )
    return (await session.execute(stmt)).rowcount or 0
