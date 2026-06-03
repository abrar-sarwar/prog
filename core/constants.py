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

LEADERBOARD_TOP_N: int = 10
"""How many users are shown in ``/leaderboard`` (the fixed top-N podium
board: ranks 1-3 in pills, 4-10 in the side column)."""

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

# --- LeetCode integration (the /leet website-solve feature) ----------------
#
# A user runs /leet in the configured channel, gets a random real LeetCode
# problem in a private thread, solves it on leetcode.com, and the bot detects
# their accepted submission by polling their PUBLIC profile. All timing and
# reward values are tunable here.

LEET_SESSION_MINUTES: int = 60
"""How long a /leet session stays open. The clock starts at assignment;
``expires_at = assigned_at + LEET_SESSION_MINUTES``. Never extended."""

LEET_ARE_YOU_THERE_MINUTES: int = 30
"""Minutes into a session at which the bot pings the user in the thread to
confirm they're still working (the "are you there?" check)."""

LEET_ARE_YOU_THERE_WAIT_SECONDS: int = 300
"""How long the user has to respond to the "are you there?" ping before the
bot stops polling early and lets the session quietly expire (default 5 min)."""

LEET_POLL_MIN_SECONDS: int = 30
"""Lower bound of the per-session detection poll interval."""

LEET_POLL_MAX_SECONDS: int = 60
"""Upper bound of the per-session detection poll interval. Each poll waits a
random duration in [MIN, MAX] so the request pattern isn't a perfectly even
machine-gun against LeetCode."""

# The reward is LEVEL-SCALED, not a flat XP grant: a solve is worth a fraction
# of the XP needed to cross into the user's NEXT level. That keeps it fair across
# the curve — a low-level solver doesn't rocket up many cheap early levels, and a
# high-level solver still gets a meaningful chunk — because the payout is always
# measured in "fraction of a level", never raw XP.
LEET_REWARD_BASE_FRACTION: float = 0.75
"""Fraction of the next level's XP cost granted at a 0/1-day streak."""

LEET_REWARD_CAP_FRACTION: float = 1.5
"""Fraction of the next level's XP cost granted at/above the streak milestone."""

LEET_REWARD_STREAK_MILESTONE_DAYS: int = 14
"""Streak length (consecutive UTC solve-days) at which the reward hits the cap."""

LEET_REWARD_ROLE_DURATION_HOURS: int = 24
"""How long the configured reward role stays on a user after a solve before a
background task removes it. Re-earning refreshes (does not stack) the timer."""

LEET_VERIFICATION_CODE_PREFIX: str = "progsu-"
"""Prefix for the one-time profile verification code (``progsu-XXXX``)."""

LEET_VERIFICATION_CODE_CHARS: int = 4
"""Number of random alphanumeric characters after the prefix."""

LEET_GRANT_SWEEP_SECONDS: int = 60
"""Interval of the background sweep that removes expired reward-role grants
and reaps any sessions that outlived their window (belt-and-suspenders to the
per-session task)."""

LEET_HTTP_TIMEOUT_SECONDS: float = 10.0
"""Per-request timeout for all LeetCode GraphQL calls."""

LEET_RECENT_AC_LIMIT: int = 20
"""How many recent accepted submissions to pull when detecting a solve."""

LEET_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
"""Browser-like User-Agent sent on every LeetCode request (GraphQL hygiene)."""
