"""Pure logic for the LeetCode website-solve feature.

No Discord, DB, or network access lives here — only deterministic functions
that the cog and the unit tests both call. The three pieces of real logic are:

* :func:`compute_reward_xp`        - streak-scaled XP payout for a solve
* :func:`compute_streak_after_solve` - the new streak after a confirmed solve
* :func:`find_matching_submission` - the slug + timestamp detection rule

plus :func:`generate_verification_code`, the ``progsu-XXXX`` code minted during
account linking.

Everything is kept side-effect free so the reward/streak/detection rules can be
exercised without a database or a live LeetCode connection.
"""

from __future__ import annotations

import random
import string
from datetime import date, timedelta
from typing import Iterable, Mapping, NamedTuple

from core.constants import (
    LEET_EXTRA_SOLVE_XP,
    LEET_PRACTICE_DAILY_CAP,
    LEET_PRACTICE_FRACTION,
    LEET_PRACTICE_OVERFLOW_XP,
    LEET_REWARD_BASE_FRACTION,
    LEET_REWARD_CAP_FRACTION,
    LEET_REWARD_STREAK_MILESTONE_DAYS,
    LEET_VERIFICATION_CODE_CHARS,
    LEET_VERIFICATION_CODE_PREFIX,
)
from core.leveling import LEVEL_CAP, xp_for_level

# Unambiguous alphanumerics for the verification code (no 0/O, 1/I/l) so a user
# copying it into their LeetCode bio can't fat-finger a look-alike.
_CODE_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_verification_code(rng: random.Random | None = None) -> str:
    """Return a fresh ``progsu-XXXX`` verification code.

    ``rng`` is injectable so tests can pin the output; production passes None
    and uses the module-global RNG.
    """
    picker = rng or random
    suffix = "".join(
        picker.choice(_CODE_ALPHABET) for _ in range(LEET_VERIFICATION_CODE_CHARS)
    )
    return f"{LEET_VERIFICATION_CODE_PREFIX}{suffix}"


def code_present_in_bio(code: str, bio: str | None) -> bool:
    """True iff ``code`` appears in the profile ``bio`` (case-insensitive).

    Tolerant of surrounding text and whitespace — the user only needs the code
    *somewhere* in their About/bio field, not as the entire contents.
    """
    if not bio:
        return False
    return code.casefold() in bio.casefold()


def compute_reward_xp(
    streak_days: int,
    current_level: int,
    *,
    base_fraction: float = LEET_REWARD_BASE_FRACTION,
    cap_fraction: float = LEET_REWARD_CAP_FRACTION,
    milestone: int = LEET_REWARD_STREAK_MILESTONE_DAYS,
) -> int:
    """Return the XP payout for a solve, scaled to the user's level and streak.

    The payout is a fraction of the XP cost to cross into the user's *next*
    level (``xp_for_level(current_level + 1)``), so it's measured in fractions
    of a level rather than raw XP. The fraction climbs linearly with the streak::

        fraction = base_fraction + (cap_fraction - base_fraction)
                   * min(streak_days, milestone) / milestone
        reward   = round(fraction * xp_for_level(current_level + 1))

    With the defaults (0.75 -> 1.5 over 14 days): a fresh solo solve is worth
    ~0.75 of a level, a 14-day streak ~1.5 levels — at *every* level, because it
    tracks the curve. This stops a low-level solver from leaping many cheap early
    levels off one flat grant. ``streak_days`` includes today's solve (a first
    solve is day 1); ``current_level`` is the level before this reward. Floors at
    1 XP so a solve always grants something.
    """
    capped = min(max(streak_days, 0), milestone)
    fraction = base_fraction + (cap_fraction - base_fraction) * capped / milestone
    next_level = min(max(current_level, 0) + 1, LEVEL_CAP)
    return max(1, round(fraction * xp_for_level(next_level)))


def compute_streak_after_solve(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
) -> int:
    """Return the new streak after a confirmed solve on ``today`` (UTC date).

    Rules (streak = consecutive UTC days with a confirmed solve):

    * No prior solve            -> streak of 1 (this is day 1).
    * Last solve was yesterday  -> streak + 1 (consecutive).
    * Last solve was today      -> unchanged (idempotent; the one-per-day gate
      should prevent a second solve, but never double-count if it slips).
    * Any older gap             -> reset to 1 (the chain broke; today restarts).
    """
    if last_solve_date is None:
        return 1
    if last_solve_date == today:
        # Already counted today; don't double-increment.
        return max(current_streak, 1)
    if last_solve_date == today - timedelta(days=1):
        return current_streak + 1
    # Gap of two or more days: the streak broke; today starts a new one.
    return 1


class SolvePayout(NamedTuple):
    """The XP + streak outcome of a confirmed solve, before it's persisted."""

    reward_xp: int
    new_streak: int
    first_of_day: bool = True  # legacy; removed in the /daily-vs-/leet cleanup
    kind: str = "practice"     # "daily" | "practice"
    capped: bool = False       # practice solve over the daily cap (token payout)


def compute_solve_payout(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
    current_level: int,
    *,
    extra_xp: int = LEET_EXTRA_SOLVE_XP,
) -> SolvePayout:
    """Decide the payout for a solve on ``today`` (UTC), enforcing one-per-day reward.

    Members may solve any number of problems per day. Only the *first* solve of a
    UTC day pays the streak-scaled reward and advances the streak (the single
    daily "multiplier"); every additional solve that day pays a flat ``extra_xp``
    and leaves the streak unchanged. ``first_of_day`` is True when this solve is
    the first of ``today`` (i.e. ``last_solve_date`` is not ``today``), letting
    callers phrase the confirmation correctly.
    """
    first_of_day = last_solve_date != today
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    if first_of_day:
        reward_xp = compute_reward_xp(new_streak, current_level)
    else:
        reward_xp = max(0, extra_xp)
    return SolvePayout(
        reward_xp=reward_xp, new_streak=new_streak, first_of_day=first_of_day
    )


def compute_daily_payout(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
    current_level: int,
) -> SolvePayout:
    """Payout for an official daily-challenge (/daily) solve.

    Always pays the streak-scaled multiplier (:func:`compute_reward_xp`) and
    advances the streak. The daily is one problem per UTC day, so this is the
    single per-day multiplier.
    """
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    reward_xp = compute_reward_xp(new_streak, current_level)
    return SolvePayout(
        reward_xp=reward_xp,
        new_streak=new_streak,
        first_of_day=(last_solve_date != today),
        kind="daily",
        capped=False,
    )


def compute_practice_payout(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
    current_level: int,
    practice_solves_today: int,
    *,
    fraction: float = LEET_PRACTICE_FRACTION,
    daily_cap: int = LEET_PRACTICE_DAILY_CAP,
    overflow_xp: int = LEET_PRACTICE_OVERFLOW_XP,
) -> SolvePayout:
    """Payout for a practice (/leet) solve: reduced, capped rate.

    Keeps the streak alive (a practice solve counts as a streak day) but never
    pays the multiplier. The first ``daily_cap`` practice solves of the UTC day
    pay ``fraction`` of the next level's XP cost; further solves pay the flat
    ``overflow_xp`` token. ``practice_solves_today`` is the count of practice
    solves already recorded today, before this one.
    """
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    if practice_solves_today < daily_cap:
        next_level = min(max(current_level, 0) + 1, LEVEL_CAP)
        reward_xp = max(1, round(fraction * xp_for_level(next_level)))
        capped = False
    else:
        reward_xp = max(0, overflow_xp)
        capped = True
    return SolvePayout(
        reward_xp=reward_xp,
        new_streak=new_streak,
        first_of_day=(last_solve_date != today),
        kind="practice",
        capped=capped,
    )


def find_daily_solve_today(
    daily_slug: str,
    today_start_epoch: int,
    recent_accepted: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return a recent accepted submission of ``daily_slug`` dated today.

    Unlike :func:`find_matching_submission` (strict: after assignment), the daily
    challenge counts as solved if its slug appears in the recent accepted list
    with a timestamp at/after ``today_start_epoch`` (UTC midnight). Rows with a
    missing/unparseable timestamp are skipped.
    """
    for sub in recent_accepted:
        if sub.get("titleSlug") != daily_slug:
            continue
        raw_ts = sub.get("timestamp")
        try:
            ts = int(raw_ts)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if ts >= today_start_epoch:
            return sub
    return None


def find_matching_submission(
    assigned_slug: str,
    assigned_at_epoch: int,
    recent_accepted: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return the recent accepted submission that satisfies the assignment.

    A solve counts only when the *assigned* problem's slug appears in the
    recent accepted-submission list with a timestamp strictly later than
    ``assigned_at_epoch`` (so a pre-existing solve can never satisfy a fresh
    assignment). Returns the matching submission mapping, or None.

    Each ``recent_accepted`` entry is expected to look like LeetCode's
    ``recentAcSubmissionList`` rows: ``{"titleSlug": str, "timestamp": str|int}``.
    Rows with a missing/unparseable timestamp are skipped defensively.
    """
    for sub in recent_accepted:
        if sub.get("titleSlug") != assigned_slug:
            continue
        raw_ts = sub.get("timestamp")
        try:
            ts = int(raw_ts)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if ts > assigned_at_epoch:
            return sub
    return None


def slug_already_solved(
    slug: str,
    recent_accepted: Iterable[Mapping[str, object]],
) -> bool:
    """True iff ``slug`` appears anywhere in the recent accepted list.

    Used at assignment time to avoid handing out a problem the user has very
    recently solved (so a pre-existing solve can't be claimed). NOTE: this only
    sees the recent window LeetCode returns, so a solve older than that window
    is not detected — the timestamp rule in :func:`find_matching_submission` is
    the real guard; this is a best-effort pre-check.
    """
    return any(sub.get("titleSlug") == slug for sub in recent_accepted)
