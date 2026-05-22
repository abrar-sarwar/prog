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

LEADERBOARD_PAGE_SIZE: int = 10
"""Number of users per page in ``/leaderboard``."""

FREEZE_DATE: date = date(2026, 8, 15)
"""After this date (strictly), the public leaderboard channel stops updating
and the top-of-leaderboard role assignment stops being recomputed. XP itself
keeps accruing forever."""
