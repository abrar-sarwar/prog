"""Leveling curve, tier bands, and level-up message templates.

The leveling system is built around a polynomial XP curve, an 11-tier
band ladder (Freshie -> Aura), and one announcement template per tier.

Units used in this module:

* ``level``     - integer 0..100; users start at 0 and progress upward
* ``xp``        - cumulative XP earned across all sources (text + voice + admin)
* ``msgs``      - messages, only used as a sanity-check unit at 20 XP/msg base

Public API (callers should import from here, not constants.py):

* :func:`xp_for_level`        - XP cost of crossing into level ``n``
* :func:`cumulative_xp_to_level` - total XP needed to reach level ``L``
* :func:`level_from_total_xp` - inverse, capped at :data:`LEVEL_CAP`
* :func:`get_tier_for_level`  - tier name for a level (None below lvl 1)
* :func:`get_tier_role_name`  - Discord role name for a level
* :func:`get_level_message`   - announcement template for a level

The :data:`TIER_BANDS` list and the :data:`LEVEL_MESSAGES` dict live at the
top of the module so future copy edits and tier tweaks happen in exactly
one place.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEVEL_CAP: int = 100
"""Maximum level a user can reach. The cumulative XP at the cap is the
design anchor (~10,000 messages at 20 XP/msg)."""


# ---------------------------------------------------------------------------
# Tier ladder
# ---------------------------------------------------------------------------

# (min_level, max_level, tier_name, role_name). The role name is the exact
# Discord role label the bot assigns when a user enters the band. Order is
# ascending and exhaustive over levels 1..LEVEL_CAP; level 0 has no tier
# (a new user has not yet earned enough XP to be a "Freshie").
TIER_BANDS: list[tuple[int, int, str, str]] = [
    (1, 9, "Freshie", "Freshie"),
    (10, 19, "Viber", "Viber"),
    (20, 29, "Innovator", "Innovator"),
    (30, 39, "Hustler", "Hustler"),
    (40, 49, "Yapper", "Yapper"),
    (50, 59, "Chud", "Chud"),
    (60, 69, "Yappatron", "Yappatron"),
    (70, 79, "Challenger", "Challenger"),
    (80, 99, "Ascendant", "Ascendant"),
    (100, 100, "Aura", "Aura"),
]


# ---------------------------------------------------------------------------
# Announcement templates
# ---------------------------------------------------------------------------

# One template per tier. ``{user}`` resolves to a Discord mention,
# ``{level}`` resolves to the actual level reached. Edit copy here only.
#
# The Aura template fires exactly once per (user, guild) - see
# :class:`db.models.User.aura_message_fired`. It is sent as a normal
# message (no @everyone ping) per project decision.
LEVEL_MESSAGES: dict[str, str] = {
    "Freshie": "{user} just hit level {level}! welcome to the grind, freshie",
    "Viber": "{user} leveled up to {level}! the vibes are immaculate",
    "Innovator": "{user} reached level {level}! cooking up something different",
    "Hustler": "{user} hit level {level}! grindset activated, no days off",
    "Yapper": "{user} just yapped their way to level {level}! the rizz is undeniable",
    "Chud": "{user} ascended to level {level}! certified yappatron",
    "Yappatron": "{user} reached level {level}! challenger arc activated",
    "Challenger": "{user} hit level {level}! you ascending now, get your lvls up",
    "Ascendant": "{user} ascended to level {level}! Aura is coming up soon",
    "Aura": (
        "HOLY BALLS. {user} JUST HIT LEVEL 100. 10,000+ messages. "
        "countless hours in vc. the absolute peak of progsu. good sh*t"
    ),
}


# ---------------------------------------------------------------------------
# Curve
# ---------------------------------------------------------------------------


def xp_for_level(n: int) -> int:
    """Return the XP cost to cross into level ``n`` (1 <= n <= LEVEL_CAP).

    Curve: ``0.4 * n^2 + 12 * n + 20``, rounded to the nearest integer.
    The result is the XP earned *while* progressing into level ``n`` from
    level ``n - 1``.
    """
    if n < 1 or n > LEVEL_CAP:
        raise ValueError(f"level must be in [1, {LEVEL_CAP}], got {n}")
    return round(0.4 * n * n + 12 * n + 20)


def _build_cumulative_table() -> list[int]:
    """Build ``[c(0), c(1), ..., c(LEVEL_CAP)]`` where ``c(L)`` is the
    cumulative XP needed to reach level ``L``."""
    table: list[int] = [0]
    running = 0
    for n in range(1, LEVEL_CAP + 1):
        running += xp_for_level(n)
        table.append(running)
    return table


# Precomputed once at import time so the hot path (level lookup on every
# XP grant) is a binary search over a 101-entry list.
_CUMULATIVE_XP: list[int] = _build_cumulative_table()


def cumulative_xp_to_level(level: int) -> int:
    """Return total XP needed to reach ``level`` (0 <= level <= LEVEL_CAP).

    ``cumulative_xp_to_level(0)`` is 0 (everyone starts here).
    ``cumulative_xp_to_level(L)`` is the threshold at which the user is
    promoted to level ``L``.
    """
    if level < 0 or level > LEVEL_CAP:
        raise ValueError(f"level must be in [0, {LEVEL_CAP}], got {level}")
    return _CUMULATIVE_XP[level]


def level_from_total_xp(total_xp: int) -> int:
    """Return the level for ``total_xp`` cumulative XP, clamped to ``[0, LEVEL_CAP]``.

    A user is at level ``L`` when ``cumulative_xp_to_level(L) <= total_xp <
    cumulative_xp_to_level(L + 1)`` (or ``L == LEVEL_CAP`` past the top).
    Negative inputs are clamped to 0 defensively so a bad caller never
    crashes the bot.
    """
    if total_xp <= 0:
        return 0
    # Linear scan is plenty for a 101-entry table; clarity over micro-opt.
    for level in range(LEVEL_CAP, -1, -1):
        if total_xp >= _CUMULATIVE_XP[level]:
            return level
    return 0


# ---------------------------------------------------------------------------
# Tier lookups
# ---------------------------------------------------------------------------


def get_tier_for_level(level: int) -> Optional[str]:
    """Return the tier name for ``level``, or ``None`` for level 0.

    Levels above ``LEVEL_CAP`` clamp to Aura so a defensive caller (e.g.
    an admin /xp-give that overshoots) still resolves to a sensible band.
    """
    if level <= 0:
        return None
    if level >= LEVEL_CAP:
        return TIER_BANDS[-1][2]
    for lo, hi, name, _role in TIER_BANDS:
        if lo <= level <= hi:
            return name
    return None  # unreachable given TIER_BANDS is exhaustive over 1..100


def get_tier_role_name(level: int) -> Optional[str]:
    """Return the Discord role name for ``level``, or ``None`` for level 0."""
    if level <= 0:
        return None
    if level >= LEVEL_CAP:
        return TIER_BANDS[-1][3]
    for lo, hi, _name, role in TIER_BANDS:
        if lo <= level <= hi:
            return role
    return None


def get_level_message(level: int) -> str:
    """Return the announcement template for ``level`` (raw, unformatted).

    The returned string contains ``{user}`` and ``{level}`` placeholders;
    callers ``str.format`` them at send time. Raises ``ValueError`` for
    levels with no template (level 0).
    """
    tier = get_tier_for_level(level)
    if tier is None:
        raise ValueError(f"no level-up message for level {level} (no tier)")
    return LEVEL_MESSAGES[tier]


# ---------------------------------------------------------------------------
# Admin rank-change helper (pure logic, no Discord access)
# ---------------------------------------------------------------------------


class AdminRankAction:
    """What an admin XP/level change implies for messaging + the Aura flag.

    ``fire_message`` is True iff a level-up announcement should be posted.
    ``message_level`` is the level to format the announcement at (the
    *final* level the user landed at). ``set_aura_flag`` is True iff this
    transition is the first-ever crossing to level 100 for this user and
    the Aura one-time flag should now be persisted.
    """

    __slots__ = ("fire_message", "message_level", "set_aura_flag")

    def __init__(
        self,
        *,
        fire_message: bool,
        message_level: int,
        set_aura_flag: bool,
    ) -> None:
        self.fire_message = fire_message
        self.message_level = message_level
        self.set_aura_flag = set_aura_flag

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return (
            f"AdminRankAction(fire_message={self.fire_message}, "
            f"message_level={self.message_level}, "
            f"set_aura_flag={self.set_aura_flag})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AdminRankAction):
            return NotImplemented
        return (
            self.fire_message == other.fire_message
            and self.message_level == other.message_level
            and self.set_aura_flag == other.set_aura_flag
        )


def admin_rank_change_action(
    old_level: int,
    new_level: int,
    aura_already_fired: bool,
) -> AdminRankAction:
    """Decide messaging behavior for an admin-driven rank change.

    Rules:

    * Only fire on a strictly positive level change.
    * Fire exactly one message, for the band the user *ended up in*.
    * If the new level is :data:`LEVEL_CAP` (Aura) and the one-time flag
      is already set, suppress the message entirely (per spec - admins
      can't re-fire the Aura ping).
    * If the new level is :data:`LEVEL_CAP` and the flag is not yet set,
      fire the Aura message and mark the flag.
    """
    if new_level <= old_level:
        return AdminRankAction(
            fire_message=False, message_level=new_level, set_aura_flag=False
        )
    if new_level >= LEVEL_CAP:
        if aura_already_fired:
            return AdminRankAction(
                fire_message=False,
                message_level=LEVEL_CAP,
                set_aura_flag=False,
            )
        return AdminRankAction(
            fire_message=True, message_level=LEVEL_CAP, set_aura_flag=True
        )
    return AdminRankAction(
        fire_message=True, message_level=new_level, set_aura_flag=False
    )


