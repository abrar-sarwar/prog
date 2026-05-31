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

from datetime import datetime
from typing import NamedTuple, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.leveling import LEVEL_CAP, cumulative_xp_to_level, level_from_total_xp
from db.models import GuildConfig, RoleReward, User


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
