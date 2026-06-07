"""Unit tests for the pure LeetCode logic.

Covers the three rules that matter for correctness — reward calc, streak math,
and the slug+timestamp detection match — plus verification-code generation and
the problem-pool random picker. No DB or network is touched.
"""

from __future__ import annotations

import random
from datetime import date

from core.constants import LEET_VERIFICATION_CODE_PREFIX
from core.leveling import xp_for_level
from core.leetcode import (
    code_present_in_bio,
    compute_reward_xp,
    compute_streak_after_solve,
    find_matching_submission,
    generate_verification_code,
    slug_already_solved,
)
from core.leetcode_problems import (
    LeetProblem,
    ProblemPool,
    get_problem,
    parse_problem_list,
    pool_size,
    random_problem,
)


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


def test_reward_solo_solve_is_base_fraction_of_next_level():
    # streak 0/1 at level 0 -> base_fraction (0.75) * xp_for_level(1) = 24
    assert compute_reward_xp(0, 0) == round(0.75 * xp_for_level(1))


def test_reward_caps_at_milestone_fraction():
    full = round(1.5 * xp_for_level(1))  # level 0 -> next level is 1
    assert compute_reward_xp(14, 0) == full
    assert compute_reward_xp(20, 0) == full
    assert compute_reward_xp(1000, 0) == full


def test_reward_scales_up_with_level():
    # Same streak, higher level -> bigger payout (it tracks the curve).
    assert compute_reward_xp(1, 10) > compute_reward_xp(1, 0)


def test_reward_increases_with_streak():
    assert compute_reward_xp(7, 5) > compute_reward_xp(0, 5)
    assert compute_reward_xp(14, 5) > compute_reward_xp(7, 5)


def test_reward_never_rockets_a_low_level_solver():
    # The old flat grant was 571 at level 0 (past level 7). Now even a maxed
    # streak at level 0 is a fraction of a level — nowhere near that.
    assert compute_reward_xp(14, 0) < 60


def test_reward_floors_at_one():
    assert compute_reward_xp(0, 0, base_fraction=0.0, cap_fraction=0.0) == 1


def test_reward_respects_custom_fractions():
    # base 1.0 at level 0 -> exactly one level's cost
    assert compute_reward_xp(0, 0, base_fraction=1.0, cap_fraction=1.0) == xp_for_level(1)


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


# --- problem pool: parsing --------------------------------------------------


_SAMPLE_API = {
    "stat_status_pairs": [
        {
            "stat": {"question__title": "Two Sum", "question__title_slug": "two-sum"},
            "paid_only": False,
            "difficulty": {"level": 1},
        },
        {
            "stat": {"question__title": "Premium Q", "question__title_slug": "premium-q"},
            "paid_only": True,  # filtered out
            "difficulty": {"level": 2},
        },
        {
            "stat": {"question__title": "Hard One", "question__title_slug": "hard-one"},
            "paid_only": False,
            "difficulty": {"level": 3},
        },
        {  # missing slug -> skipped
            "stat": {"question__title": "No Slug", "question__title_slug": ""},
            "paid_only": False,
            "difficulty": {"level": 2},
        },
    ]
}


def test_parse_problem_list_filters_premium_and_maps_difficulty():
    problems = parse_problem_list(_SAMPLE_API)
    by_slug = {p.slug: p for p in problems}
    assert set(by_slug) == {"two-sum", "hard-one"}  # premium + slugless dropped
    assert by_slug["two-sum"].difficulty == "Easy"
    assert by_slug["hard-one"].difficulty == "Hard"


def test_parse_problem_list_handles_garbage():
    assert parse_problem_list({}) == []
    assert parse_problem_list({"stat_status_pairs": "nope"}) == []


# --- problem pool: ProblemPool selection ------------------------------------


_THREE = [
    LeetProblem("a", "A", "Easy"),
    LeetProblem("b", "B", "Medium"),
    LeetProblem("c", "C", "Hard"),
]


def test_pool_random_excludes():
    pool = ProblemPool(_THREE)
    chosen = pool.random(random.Random(1), exclude_slugs={"b", "c"})
    assert chosen is not None and chosen.slug == "a"


def test_pool_random_all_excluded_returns_none():
    pool = ProblemPool(_THREE)
    assert pool.random(random.Random(1), exclude_slugs={"a", "b", "c"}) is None


def test_pool_replace_ignores_empty():
    pool = ProblemPool(_THREE)
    assert pool.replace([]) is False
    assert len(pool) == 3  # unchanged
    assert pool.replace([LeetProblem("z", "Z", "Easy")]) is True
    assert len(pool) == 1 and pool.get("z") is not None


# --- problem pool: module singleton loaded from the committed snapshot -------


def test_module_pool_loaded_from_snapshot():
    assert pool_size() >= 50
    # A classic free problem is present, and lookups are well-formed.
    p = random_problem(random.Random(0))
    assert p is not None and p.url == f"https://leetcode.com/problems/{p.slug}/"
    assert get_problem("definitely-not-a-real-slug") is None


def test_daily_payout_pays_streak_multiplier_and_advances_streak():
    from core.leetcode import compute_daily_payout

    payout = compute_daily_payout(
        last_solve_date=date(2026, 6, 5),
        today=date(2026, 6, 6),
        current_streak=3,
        current_level=5,
    )
    assert payout.kind == "daily"
    assert payout.capped is False
    assert payout.new_streak == 4
    # multiplier reward equals the streak-scaled compute_reward_xp
    from core.leetcode import compute_reward_xp
    assert payout.reward_xp == compute_reward_xp(4, 5)


def test_practice_payout_under_cap_is_fraction_of_next_level():
    from core.leetcode import compute_practice_payout
    from core.constants import LEET_PRACTICE_FRACTION

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 6),
        today=date(2026, 6, 6),
        current_streak=2,
        current_level=10,
        practice_solves_today=0,
    )
    assert payout.kind == "practice"
    assert payout.capped is False
    assert payout.reward_xp == max(1, round(LEET_PRACTICE_FRACTION * xp_for_level(11)))


def test_practice_payout_over_cap_pays_flat_overflow():
    from core.leetcode import compute_practice_payout
    from core.constants import LEET_PRACTICE_DAILY_CAP, LEET_PRACTICE_OVERFLOW_XP

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 6),
        today=date(2026, 6, 6),
        current_streak=2,
        current_level=10,
        practice_solves_today=LEET_PRACTICE_DAILY_CAP,
    )
    assert payout.capped is True
    assert payout.reward_xp == LEET_PRACTICE_OVERFLOW_XP


def test_practice_payout_keeps_streak_alive_across_days():
    from core.leetcode import compute_practice_payout

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 5),
        today=date(2026, 6, 6),
        current_streak=4,
        current_level=1,
        practice_solves_today=0,
    )
    assert payout.new_streak == 5


def test_find_daily_solve_today_accepts_today_rejects_yesterday():
    from core.leetcode import find_daily_solve_today

    today_start = 1_000_000  # arbitrary epoch for "start of today"
    recent = [
        {"titleSlug": "two-sum", "timestamp": "999999"},      # yesterday
        {"titleSlug": "two-sum", "timestamp": "1000050"},     # today
    ]
    hit = find_daily_solve_today("two-sum", today_start, recent)
    assert hit is not None and hit["timestamp"] == "1000050"

    miss = find_daily_solve_today(
        "two-sum", today_start, [{"titleSlug": "two-sum", "timestamp": "999999"}]
    )
    assert miss is None


def test_parse_daily_challenge_reads_date_and_problem():
    from core.leetcode_problems import parse_daily_challenge

    data = {
        "activeDailyCodingChallengeQuestion": {
            "date": "2026-06-06",
            "link": "/problems/two-sum/",
            "question": {
                "titleSlug": "two-sum",
                "title": "Two Sum",
                "difficulty": "Easy",
            },
        }
    }
    parsed = parse_daily_challenge(data)
    assert parsed is not None
    date_str, problem = parsed
    assert date_str == "2026-06-06"
    assert problem.slug == "two-sum"
    assert problem.title == "Two Sum"
    assert problem.difficulty == "Easy"


def test_parse_daily_challenge_handles_garbage():
    from core.leetcode_problems import parse_daily_challenge

    assert parse_daily_challenge({}) is None
    assert parse_daily_challenge({"activeDailyCodingChallengeQuestion": None}) is None
    assert parse_daily_challenge(
        {"activeDailyCodingChallengeQuestion": {"date": "x", "question": {}}}
    ) is None
    assert parse_daily_challenge(
        {"activeDailyCodingChallengeQuestion": {"date": "", "question": {"titleSlug": "two-sum", "title": "Two Sum"}}}
    ) is None


def test_parse_question_list_filters_premium_and_keeps_free():
    from core.leetcode_problems import parse_question_list, parse_question_total

    data = {
        "problemsetQuestionList": {
            "total": 2,
            "questions": [
                {"title": "Two Sum", "titleSlug": "two-sum", "difficulty": "Easy", "isPaidOnly": False},
                {"title": "Paid", "titleSlug": "paid", "difficulty": "Hard", "isPaidOnly": True},
            ],
        }
    }
    problems = parse_question_list(data)
    assert [p.slug for p in problems] == ["two-sum"]
    assert parse_question_total(data) == 2


def test_parse_question_list_handles_garbage():
    from core.leetcode_problems import parse_question_list, parse_question_total

    assert parse_question_list({}) == []
    assert parse_question_list({"problemsetQuestionList": None}) == []
    assert parse_question_list({"problemsetQuestionList": {"questions": "nope"}}) == []
    assert parse_question_total({}) == 0
    assert parse_question_total({"problemsetQuestionList": {}}) == 0


def test_question_list_query_uses_ispaidonly():
    # Regression guard: LeetCode renamed paidOnly -> isPaidOnly; the old field
    # made every tag/difficulty query 400. The query must request the new name.
    from core.leetcode_client import _QUESTION_LIST_QUERY

    assert "isPaidOnly" in _QUESTION_LIST_QUERY


def test_fetch_filtered_questions_builds_filters(monkeypatch):
    import asyncio

    import core.leetcode_client as client

    captured = {}

    async def _fake_graphql(query, variables, referer):
        captured["variables"] = variables
        return {}

    monkeypatch.setattr(client, "_graphql", _fake_graphql)

    def filters_for(tags, difficulty):
        captured.clear()
        asyncio.run(client.fetch_filtered_questions(tags, difficulty, limit=5))
        return captured["variables"]["filters"]

    assert filters_for(["array"], None) == {"tags": ["array"]}
    assert filters_for([], "HARD") == {"difficulty": "HARD"}
    assert filters_for(["array"], "EASY") == {"tags": ["array"], "difficulty": "EASY"}
    assert filters_for([], None) == {}  # no filters -> empty (caller uses the pool)


def test_daily_cache_round_trips_by_date():
    from core.leetcode_problems import (
        LeetProblem,
        get_cached_daily,
        set_cached_daily,
    )

    assert get_cached_daily("2099-01-02") is None
    set_cached_daily("2099-01-01", LeetProblem("two-sum", "Two Sum", "Easy"))
    assert get_cached_daily("2099-01-01").slug == "two-sum"
    assert get_cached_daily("2099-01-02") is None  # stale date misses
