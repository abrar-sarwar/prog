"""Pure leveling-curve functions.

Curve: the XP required to *clear* level ``n`` is ``n^2 + 30n + 50``.

``cumulative_xp_to_level(n)`` returns the total XP needed to *reach* level
``n`` from 0 (so reaching level 1 costs 0 XP; you start there).

``level_from_xp(total_xp)`` is the inverse: the highest level reachable with
the given cumulative XP. Members start at level 1, so ``level_from_xp(0) == 1``.
"""

from __future__ import annotations


def xp_for_level(n: int) -> int:
    """Return the XP required to clear level ``n``.

    Defined for ``n >= 1``. The curve is intentionally polynomial so the
    cost-per-level grows smoothly rather than exponentially.
    """
    if n < 1:
        raise ValueError(f"level must be >= 1, got {n}")
    return n * n + 30 * n + 50


def cumulative_xp_to_level(n: int) -> int:
    """Return the total cumulative XP needed to reach level ``n`` from 0.

    ``cumulative_xp_to_level(1) == 0`` (everyone starts at level 1).
    ``cumulative_xp_to_level(n)`` for ``n > 1`` is the sum of ``xp_for_level(i)``
    for ``i`` in ``[1, n-1]``.
    """
    if n < 1:
        raise ValueError(f"level must be >= 1, got {n}")
    return sum(xp_for_level(i) for i in range(1, n))


def level_from_xp(total_xp: int) -> int:
    """Return the current level for a given cumulative XP total.

    The minimum level is 1; negative inputs are clamped to 0 defensively so
    a bad caller never crashes the bot, but callers should keep XP >= 0.
    """
    if total_xp < 0:
        total_xp = 0
    level = 1
    cumulative = 0
    while True:
        next_cumulative = cumulative + xp_for_level(level)
        if next_cumulative > total_xp:
            return level
        cumulative = next_cumulative
        level += 1
