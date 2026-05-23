"""Project-wide constants.

Everything here is plain data so it can be imported from any module without
side effects. Per-guild values (which channels are blacklisted, which roles
map to which level) live in the database, not here.
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

# --- Leaderboard channel embed (change-driven, no schedule) ----------------

LEADERBOARD_TOP_N: int = 5
"""How many users are shown in ``/leaderboard`` and the persistent channel embed."""

LEADERBOARD_DEBOUNCE_SECONDS: float = 3.0
"""After an XP grant invalidates a guild's cached top-N, wait this long
before pushing a Discord edit. Subsequent grants in the same window reset
the timer; only the final state is rendered. Prevents edit storms when
many level-ups land in quick succession."""

LEADERBOARD_SAFETY_POLL_MINUTES: int = 10
"""Belt-and-suspenders: every N minutes, re-check each configured guild's
top-N against cache in case any change-driven update was dropped (bot
restart between dispatch and edit, missed event, etc.)."""


# --- Level-up announcement messages -----------------------------------------

# Bands 1-49 carry both ``{mention}`` and ``{level}`` placeholders so the
# rendered text always advertises the level a member just hit. The level-50
# template has no ``{level}`` placeholder because "final lvl" already conveys
# the number; ``str.format`` quietly ignores the extra keyword.
_LEVELUP_NOOB = (
    "{mention} congrats you lvl up to lvl {level}, keep grinding your way noob"
)
_LEVELUP_KNIGHT = (
    "{mention} congrats you lvl up to lvl {level}, "
    "knight your way up to get swore"
)
_LEVELUP_PROGMASTER = (
    "{mention} congrats you lvl up to lvl {level}, "
    "now keep progging to become a progmaster"
)
_LEVELUP_9_TO_5 = (
    "{mention} congrats you lvl up to lvl {level}, get back to your 9-5 already"
)
_LEVELUP_GRASS = (
    "{mention} congrats you lvl up to lvl {level}, "
    "can you see green? you're close to grasses"
)
_LEVELUP_FINAL = "{mention} yay you reached the final lvl, do you got a j*b yet or no"


def _levelup_template(new_level: int) -> str:
    """Pick the template band for ``new_level``. Out-of-range falls back to grass."""
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
    # Anything outside 1-50 (e.g. 51+, or defensive 0/negative input) falls
    # back to the grass band so the helper is total over int.
    return _LEVELUP_GRASS


def level_up_message(new_level: int, mention: str) -> str:
    """Return the rendered level-up message for ``(new_level, mention)``.

    The returned string is ready to be passed straight to
    ``channel.send(content=...)``; no further formatting is required at the
    call site.

    Band mapping (the level is inlined as plain text - no markdown, no
    backticks, no code block):

    * 1-10  -> noob message      ("... lvl up to lvl N, keep grinding your way noob")
    * 11-20 -> knight message    ("... lvl up to lvl N, knight your way up to get swore")
    * 21-30 -> progmaster message
    * 31-40 -> 9-to-5 message
    * 41-49 -> grass message
    * 50    -> final-level message (no level number; "final lvl" suffices)

    Levels outside 1-50 fall back to the grass band, so the helper is total
    over int.
    """
    return _levelup_template(new_level).format(mention=mention, level=new_level)
