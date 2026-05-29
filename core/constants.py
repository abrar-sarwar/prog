"""Project-wide constants.

Everything here is plain data so it can be imported from any module without
side effects. Per-guild values (which channels are blacklisted, which roles
map to which level) live in the database, not here.

Tier-band names, level-up message templates, and the leveling curve all
live in :mod:`core.leveling`.
"""

from __future__ import annotations

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

# --- Leaderboard channel embed (change-driven, no schedule) ----------------

LEADERBOARD_TOP_N: int = 5
"""How many users are shown in ``/leaderboard`` (the sliding-window
embed centred on the invoker)."""

LEADERBOARD_IMAGE_TOP_N: int = 10
"""How many users render onto the persistent channel image."""

LEADERBOARD_AVATAR_CACHE_MAX: int = 256
"""LRU bound for the in-memory avatar cache used by the image renderer."""

LEADERBOARD_DEBOUNCE_SECONDS: float = 3.0
"""After an XP grant invalidates a guild's cached top-N, wait this long
before pushing a Discord edit. Subsequent grants in the same window reset
the timer; only the final state is rendered. Prevents edit storms when
many level-ups land in quick succession."""

LEADERBOARD_SAFETY_POLL_MINUTES: int = 10
"""Belt-and-suspenders: every N minutes, re-check each configured guild's
top-N against cache in case any change-driven update was dropped (bot
restart between dispatch and edit, missed event, etc.)."""
