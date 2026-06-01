"""SQLAlchemy ORM models for prog.

Three tables:

* ``users``         - per-(guild, user) XP record
* ``guild_config``  - per-guild configuration (channels, roles, multipliers)
* ``role_rewards``  - level threshold -> role mapping per guild

JSONB column keys are always stored as strings (Python int keys are not valid
JSON); callers in :mod:`db.crud` convert to/from int on the boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
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
