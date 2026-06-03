"""SQLAlchemy ORM models for prog.

Tables:

* ``users``                 - per-(guild, user) XP record (+ /leet stats)
* ``guild_config``          - per-guild configuration (channels, roles, multipliers)
* ``role_rewards``          - level threshold -> role mapping per guild
* ``leetcode_links``        - Discord user <-> verified LeetCode account
* ``leetcode_assignments``  - one /leet session (problem + lifecycle)
* ``leetcode_role_grants``  - active timed reward-role grants from /leet solves

JSONB column keys are always stored as strings (Python int keys are not valid
JSON); callers in :mod:`db.crud` convert to/from int on the boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all prog ORM models."""


class User(Base):
    """Per-(guild, user) cumulative XP and level cache.

    ``xp`` is the total cumulative XP earned across all sources. ``level`` is
    a cache of :func:`core.leveling.level_from_total_xp` for the current
    ``xp``; callers must keep them in sync.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_users_guild_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    xp: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voice_xp_total: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    text_xp_total: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    aura_message_fired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    """One-shot flag for the level-100 Aura announcement. Once set, the
    Aura message will never re-fire for this (guild, user), even if the
    user drops below 100 and climbs back."""
    saved_role_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    """Role IDs the member had at their last departure from the guild, captured
    by ``on_member_remove`` and re-applied on rejoin. Excludes unassignable and
    dangerous-permission roles (see :mod:`core.roles`)."""

    # --- LeetCode website-solve feature (the /leet command) ---------------
    # These feed the rank card and admin engagement reads. ``leetcode_streak``
    # is consecutive UTC days with a confirmed solve; ``leetcode_last_solve_date``
    # is the UTC date of the most recent solve (used to compute the streak).
    leetcode_solved_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    """Total problems solved via /leet (counts confirmed assignments)."""
    leetcode_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    leetcode_last_solve_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )


class GuildConfig(Base):
    """Per-guild configuration.

    JSONB shapes:

    * ``xp_blacklist``        -- ``list[int]`` of channel IDs (stored as ints in
      a JSON array; integers <= 2**53 round-trip safely, but for safety the
      CRUD layer also accepts string IDs and normalises).
    * ``channel_multipliers`` -- ``dict[str, float]`` keyed by channel-ID-as-string.
    * ``role_multipliers``    -- ``dict[str, float]`` keyed by role-ID-as-string.
    """

    __tablename__ = "guild_config"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # XP is opt-in: a freshly-added guild starts with earning OFF until an
    # admin runs /enable-xp (the migration flips existing guilds to True so
    # servers already using the bot keep working).
    xp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    level_up_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    leaderboard_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    leaderboard_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progsuvian_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    xp_blacklist: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    channel_multipliers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    role_multipliers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Channel -> role bindings: ``{channel_id_str: role_id}``. The first
    # qualifying message a member sends in a bound channel grants them the
    # role (stacking + permanent; independent of whether XP is enabled).
    channel_roles: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Welcome-on-join. The feature is opt-in: a welcome message is only posted
    # once ``welcome_channel_id`` is set. ``welcome_intro_channel_id`` is the
    # target of the ``{intro_channel}`` placeholder; ``welcome_message`` is the
    # per-guild override (NULL = use core.welcome.DEFAULT_WELCOME_MESSAGE).
    welcome_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    welcome_intro_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # LeetCode /leet feature, per-guild and runtime-configured (independent of
    # any other channel/role config): ``leetcode_channel_id`` is set by
    # /setup-leet (nothing in the feature runs until it's set), and
    # ``leetcode_reward_role_id`` by /setup-leet-reward (optional timed role).
    leetcode_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    leetcode_reward_role_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )


class RoleReward(Base):
    """Level threshold -> role mapping for a guild.

    ``level == 0`` is reserved for the "top of leaderboard" role assigned by
    the weekly leaderboard updater; ``level >= 1`` entries are role rewards
    granted on level-up.
    """

    __tablename__ = "role_rewards"
    __table_args__ = (
        UniqueConstraint("guild_id", "level", name="uq_role_rewards_guild_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# LeetCode website-solve feature
# ---------------------------------------------------------------------------


class LeetcodeLink(Base):
    """Verified link between a Discord user and their LeetCode account.

    One link per Discord user (globally — a person's LeetCode account is the
    same across guilds). LeetCode's public API exposes no stable numeric id
    (``matchedUser.id`` is null), so ``username_key`` (the lowercased canonical
    username) is the lookup key and ``leetcode_internal_id`` is reserved for
    forward-compat (stays NULL today). A username change breaks the link; the
    user re-runs /leetverify to repair it.
    """

    __tablename__ = "leetcode_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True
    )
    leetcode_username: Mapped[str] = mapped_column(String, nullable=False)
    """Canonical-cased username, for display."""
    leetcode_username_key: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    """Lowercased username, used for lookups/uniqueness of the account."""
    leetcode_internal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class LeetcodeAssignment(Base):
    """One /leet session: the problem handed out and its lifecycle.

    The problem is denormalized here (slug/title/difficulty) so the editable
    code-side pool can change without orphaning history. ``status`` is one of
    ``active`` / ``completed`` / ``expired``. Daily-attempt enforcement queries
    this table for rows on the current UTC day.
    """

    __tablename__ = "leetcode_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    problem_slug: Mapped[str] = mapped_column(String, nullable=False)
    problem_title: Mapped[str] = mapped_column(String, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LeetcodeRoleGrant(Base):
    """An active timed reward-role grant from a /leet solve.

    A background task removes the role once ``expires_at`` passes. Re-earning
    while still active refreshes ``expires_at`` (no stacking) — enforced by the
    unique ``(guild_id, user_id, role_id)`` constraint plus an upsert.
    """

    __tablename__ = "leetcode_role_grants"
    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "user_id",
            "role_id",
            name="uq_leet_role_grants_guild_user_role",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
