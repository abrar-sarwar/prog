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
from typing import Iterable, Mapping

from core.constants import (
    LEET_REWARD_BASE_XP,
    LEET_REWARD_CAP_XP,
    LEET_REWARD_STREAK_MILESTONE_DAYS,
    LEET_VERIFICATION_CODE_CHARS,
    LEET_VERIFICATION_CODE_PREFIX,
)

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
    *,
    base: int = LEET_REWARD_BASE_XP,
    cap: int = LEET_REWARD_CAP_XP,
    milestone: int = LEET_REWARD_STREAK_MILESTONE_DAYS,
) -> int:
    """Return the XP payout for a solve at the given (post-solve) streak.

    Linear climb from ``base`` to ``cap`` over ``milestone`` days::

        reward = base + (cap - base) * min(streak_days, milestone) / milestone

    With the defaults (500 / 1500 / 14): a 1-day streak pays 571, a 7-day
    streak pays 1000, and any streak >= 14 days pays the 1500 cap. ``streak_days``
    is the streak *including* today's solve (a first-ever solve is day 1).
    Defensive: a non-positive streak floors at the base payout.
    """
    if streak_days <= 0:
        return base
    capped = min(streak_days, milestone)
    return round(base + (cap - base) * capped / milestone)


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
