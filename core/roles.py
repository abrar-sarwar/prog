"""Role-persistence policy helpers.

Pure logic deciding which roles survive a leave/rejoin round-trip. Kept free of
Discord imports so it can be unit-tested directly; the cog supplies the
Discord-specific booleans (``role.is_default()``, ``role.managed``, the
permission check, and a position comparison against the bot's top role).

A role is **not** restored when it is any of:

* the ``@everyone`` default role (not assignable),
* a managed role -- Nitro booster, bot, or integration roles, which Discord
  forbids any bot from assigning manually,
* a role carrying a dangerous permission (see :data:`DANGEROUS_PERMISSIONS`) --
  so leaving and rejoining can't silently restore mod/admin power, or
* a role positioned above the bot's own top role (the bot can't assign it).
"""

from __future__ import annotations

from typing import Iterable

DANGEROUS_PERMISSIONS: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
)
"""Permission names that disqualify a role from automatic restoration."""


def has_dangerous_permission(perm_names: Iterable[str]) -> bool:
    """Return True if any granted permission name is in :data:`DANGEROUS_PERMISSIONS`.

    ``perm_names`` is the iterable of permission names that are **granted** on a
    role (e.g. the names yielded by ``discord.Permissions`` for which the value
    is True).
    """
    granted = set(perm_names)
    return any(p in granted for p in DANGEROUS_PERMISSIONS)


def role_is_restorable(
    *,
    is_default: bool,
    is_managed: bool,
    has_dangerous_perm: bool,
    is_above_bot: bool,
) -> bool:
    """Return True iff a role should be saved/restored on leave/rejoin.

    Restorable means none of the disqualifying conditions hold. ``is_above_bot``
    is only meaningful at restore time (pass ``False`` when snapshotting on
    leave, where role position relative to the bot doesn't matter yet).
    """
    return not (is_default or is_managed or has_dangerous_perm or is_above_bot)
