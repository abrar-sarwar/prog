"""Unit tests for the pure LeetCode logic.

Covers the three rules that matter for correctness — reward calc, streak math,
and the slug+timestamp detection match — plus verification-code generation and
the problem-pool random picker. No DB or network is touched.
"""

from __future__ import annotations

import random
from datetime import date

from core.constants import (
    LEET_REWARD_BASE_XP,
    LEET_REWARD_CAP_XP,
    LEET_VERIFICATION_CODE_PREFIX,
)
from core.leetcode import (
    code_present_in_bio,
    compute_reward_xp,
    compute_streak_after_solve,
    find_matching_submission,
    generate_verification_code,
    slug_already_solved,
)
from core.leetcode_problems import PROBLEM_POOL, get_problem, random_problem


# --- verification code ------------------------------------------------------


def test_verification_code_format():
    code = generate_verification_code(random.Random(0))
    assert code.startswith(LEET_VERIFICATION_CODE_PREFIX)
    suffix = code[len(LEET_VERIFICATION_CODE_PREFIX):]
    assert len(suffix) == 4
    assert suffix.isalnum()


def test_verification_code_is_deterministic_with_seed():
    assert generate_verification_code(random.Random(42)) == generate_verification_code(
        random.Random(42)
    )


def test_code_present_in_bio_case_insensitive_substring():
    assert code_present_in_bio("progsu-AB12", "hi my code is PROGSU-ab12 thanks")
    assert code_present_in_bio("progsu-AB12", "progsu-ab12")
    assert not code_present_in_bio("progsu-AB12", "progsu-zz99")
    assert not code_present_in_bio("progsu-AB12", None)
    assert not code_present_in_bio("progsu-AB12", "")


# --- reward calc ------------------------------------------------------------


def test_reward_floor_at_base_for_first_day():
    # day 1: 500 + 1000*1/14 = 571.43 -> 571
    assert compute_reward_xp(1) == 571


def test_reward_midpoint_at_seven_days():
    # day 7: 500 + 1000*7/14 = 1000 exactly
    assert compute_reward_xp(7) == 1000


def test_reward_caps_at_milestone():
    assert compute_reward_xp(14) == LEET_REWARD_CAP_XP
    assert compute_reward_xp(20) == LEET_REWARD_CAP_XP
    assert compute_reward_xp(1000) == LEET_REWARD_CAP_XP


def test_reward_nonpositive_streak_is_base():
    assert compute_reward_xp(0) == LEET_REWARD_BASE_XP
    assert compute_reward_xp(-3) == LEET_REWARD_BASE_XP


def test_reward_respects_custom_constants():
    # base 100, cap 200, milestone 10 -> day 5 = 150
    assert compute_reward_xp(5, base=100, cap=200, milestone=10) == 150


# --- streak math ------------------------------------------------------------


def test_streak_first_ever_solve():
    assert compute_streak_after_solve(None, date(2026, 6, 3), 0) == 1


def test_streak_consecutive_day_increments():
    assert compute_streak_after_solve(date(2026, 6, 2), date(2026, 6, 3), 4) == 5


def test_streak_same_day_does_not_double_count():
    assert compute_streak_after_solve(date(2026, 6, 3), date(2026, 6, 3), 4) == 4


def test_streak_gap_resets_to_one():
    assert compute_streak_after_solve(date(2026, 5, 30), date(2026, 6, 3), 9) == 1


def test_streak_two_day_gap_resets():
    assert compute_streak_after_solve(date(2026, 6, 1), date(2026, 6, 3), 9) == 1


# --- detection match --------------------------------------------------------


def _sub(slug, ts):
    return {"titleSlug": slug, "timestamp": ts, "id": "1", "title": slug}


def test_match_requires_slug_and_later_timestamp():
    recent = [_sub("two-sum", "1000"), _sub("3sum", "2000")]
    match = find_matching_submission("two-sum", 500, recent)
    assert match is not None and match["titleSlug"] == "two-sum"


def test_match_rejects_preexisting_solve():
    # Submission timestamp is BEFORE the assignment -> not a valid solve.
    recent = [_sub("two-sum", "400")]
    assert find_matching_submission("two-sum", 500, recent) is None


def test_match_rejects_wrong_problem():
    recent = [_sub("3sum", "9999")]
    assert find_matching_submission("two-sum", 500, recent) is None


def test_match_handles_integer_and_bad_timestamps():
    recent = [_sub("two-sum", None), _sub("two-sum", "notanumber"), _sub("two-sum", 9999)]
    match = find_matching_submission("two-sum", 500, recent)
    assert match is not None and match["timestamp"] == 9999


def test_slug_already_solved():
    recent = [_sub("two-sum", "400"), _sub("3sum", "500")]
    assert slug_already_solved("two-sum", recent)
    assert not slug_already_solved("merge-intervals", recent)


# --- problem pool -----------------------------------------------------------


def test_pool_is_nonempty_and_well_formed():
    assert len(PROBLEM_POOL) >= 50
    for p in PROBLEM_POOL:
        assert p.slug and p.title
        assert p.difficulty in {"Easy", "Medium", "Hard"}
        assert p.url == f"https://leetcode.com/problems/{p.slug}/"


def test_pool_slugs_unique():
    slugs = [p.slug for p in PROBLEM_POOL]
    assert len(slugs) == len(set(slugs))


def test_random_problem_excludes():
    only_two = {p.slug for p in PROBLEM_POOL[2:]}  # exclude all but first two
    chosen = random_problem(random.Random(1), exclude_slugs=only_two)
    assert chosen is not None and chosen.slug in {PROBLEM_POOL[0].slug, PROBLEM_POOL[1].slug}


def test_random_problem_all_excluded_returns_none():
    everything = {p.slug for p in PROBLEM_POOL}
    assert random_problem(random.Random(1), exclude_slugs=everything) is None


def test_get_problem_roundtrip():
    p = PROBLEM_POOL[0]
    assert get_problem(p.slug) is p
    assert get_problem("definitely-not-a-real-slug") is None
