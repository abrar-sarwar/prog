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

from sqlalchemy import BigInteger, DateTime, Integer, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all prog ORM models."""


class User(Base):
    """Per-(guild, user) cumulative XP and level cache.

    ``xp`` is the total cumulative XP earned across all sources. ``level`` is
    a cache of :func:`core.leveling.level_from_xp` for the current ``xp``;
    callers must keep them in sync.
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
        Integer, nullable=False, default=1, server_default=text("1")
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
