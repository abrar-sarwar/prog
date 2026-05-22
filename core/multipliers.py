"""XP multiplier computation.

A grant of ``base_xp`` is multiplied by:

* the channel's multiplier (or 1.0 if the channel has none configured), and
* the *highest* applicable role multiplier the member has (or 1.0 if none) -
  role multipliers do **not** stack across roles.

The result is floored to ``int``. If the original ``base_xp`` was positive,
the final result is also floored at 1 so that a multiplier of e.g. 0.01 still
grants a token amount instead of silently zeroing the gain.
"""

from __future__ import annotations

from typing import Protocol


class MultiplierConfig(Protocol):
    """Structural type for the bits of GuildConfig that this module needs."""

    channel_multipliers: dict[str, float]
    role_multipliers: dict[str, float]


def compute_final_xp(
    base_xp: int,
    channel_id: int,
    member_role_ids: list[int],
    guild_config: MultiplierConfig,
) -> int:
    """Return the XP to actually award after channel and role multipliers."""
    channel_mult = float(guild_config.channel_multipliers.get(str(channel_id), 1.0))
    role_mult = max(
        (
            float(guild_config.role_multipliers.get(str(rid), 1.0))
            for rid in member_role_ids
        ),
        default=1.0,
    )
    final = int(base_xp * channel_mult * role_mult)
    if base_xp > 0 and final < 1:
        return 1
    return final
