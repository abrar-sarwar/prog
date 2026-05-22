"""Project-wide constants.

Everything here is plain data so it can be imported from any module without
side effects. Per-guild values (which channels are blacklisted, which roles
map to which level) live in the database, not here.
"""

from __future__ import annotations

from datetime import date

# --- Text XP ----------------------------------------------------------------

TEXT_XP_MIN: int = 15
"""Minimum base XP awarded for a qualifying message."""

TEXT_XP_MAX: int = 25
"""Maximum base XP awarded for a qualifying message."""

MESSAGE_XP_COOLDOWN_SECONDS: int = 60
"""Per-user message XP cooldown."""

MIN_MESSAGE_LENGTH: int = 3
"""Messages shorter than this earn no XP."""

# --- Voice XP ---------------------------------------------------------------

VOICE_XP_PER_TICK: int = 10
"""Base XP awarded to each eligible member per voice tick."""

VOICE_XP_TICK_SECONDS: int = 60
"""Voice scanner interval, in seconds."""

# --- Leveling ladder (display names; IDs live in the role_rewards table) ---

LEVEL_ROLE_NAMES: dict[int, str] = {
    1: "prognoob",
    11: "progknight",
    21: "gitsworn",
    31: "progmaster",
    41: "gitalife",
}
"""Level threshold -> role name. Used by /show-config and admin hints."""

PROGSUVIAN_ROLE_NAME: str = "progsuvian"
"""Display name of the base role assigned on first interaction."""

# --- Leaderboard scheduler --------------------------------------------------

LEADERBOARD_TIMEZONE: str = "America/New_York"
"""IANA timezone driving the weekly leaderboard schedule (handles EST/EDT)."""

LEADERBOARD_WEEKDAY: int = 5
"""Saturday (Python's ``datetime.weekday()`` is 0=Mon ... 6=Sun)."""

LEADERBOARD_HOUR: int = 12
"""Hour-of-day (24h) when the weekly update fires."""

LEADERBOARD_TOP_N: int = 5
"""How many users are shown in ``/leaderboard`` and the weekly channel post."""

FREEZE_DATE: date = date(2026, 8, 15)
"""After this date (strictly), the public leaderboard channel stops updating
and the top-of-leaderboard role assignment stops being recomputed. XP itself
keeps accruing forever."""


# --- Level-up announcement messages -----------------------------------------

_LEVELUP_NOOB = "{mention} congrats you lvl up, keep grinding your way noob"
_LEVELUP_KNIGHT = "{mention} congrats you lvl up, knight your way up to get swore"
_LEVELUP_PROGMASTER = (
    "{mention} congrats you lvl up, now keep progging to become a progmaster"
)
_LEVELUP_9_TO_5 = "{mention} congrats you lvl up, get back to your 9-5 already"
_LEVELUP_GRASS = (
    "{mention} congrats you lvl up, can you see green? you're close to grasses"
)
_LEVELUP_FINAL = "{mention} yay you reached the final lvl, do you got a j*b yet or no"


def levelup_message(new_level: int) -> str:
    """Return the level-up message template for the given new level.

    The returned string contains a ``{mention}`` placeholder which the caller
    must format with ``member.mention``.

    Band mapping:

    * 1-10  -> noob message
    * 11-20 -> knight message
    * 21-30 -> progmaster message
    * 31-40 -> 9-to-5 message
    * 41-49 -> grass message
    * 50    -> final-level message

    Levels outside 1-50 (e.g. 51+, or any defensive 0/negative input) fall
    back to the 41-49 grass message, so the helper is total over int.
    """
    if 1 <= new_level <= 10:
        return _LEVELUP_NOOB
    if 11 <= new_level <= 20:
        return _LEVELUP_KNIGHT
    if 21 <= new_level <= 30:
        return _LEVELUP_PROGMASTER
    if 31 <= new_level <= 40:
        return _LEVELUP_9_TO_5
    if 41 <= new_level <= 49:
        return _LEVELUP_GRASS
    if new_level == 50:
        return _LEVELUP_FINAL
    return _LEVELUP_GRASS
