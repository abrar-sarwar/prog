"""Unit tests for LeetCode features and XP multipliers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.multipliers import compute_final_xp


def make_cfg(
    channel_multipliers: dict[str, float] | None = None,
    role_multipliers: dict[str, float] | None = None,
) -> Any:
    return SimpleNamespace(
        channel_multipliers=channel_multipliers or {},
        role_multipliers=role_multipliers or {},
    )


def test_leetcode_boost_applied():
    cfg = make_cfg()
    # base 20 * 1.5 = 30
    assert compute_final_xp(20, 100, [], cfg, has_leetcode_boost=True) == 30


def test_leetcode_boost_with_channel_multiplier():
    cfg = make_cfg(channel_multipliers={"100": 2.0})
    # base 20 * channel 2.0 * boost 1.5 = 60
    assert compute_final_xp(20, 100, [], cfg, has_leetcode_boost=True) == 60


def test_leetcode_boost_with_role_multiplier():
    cfg = make_cfg(role_multipliers={"200": 3.0})
    # base 10 * role 3.0 * boost 1.5 = 45
    assert compute_final_xp(10, 100, [200], cfg, has_leetcode_boost=True) == 45


def test_leetcode_boost_all_stack():
    cfg = make_cfg(
        channel_multipliers={"100": 2.0},
        role_multipliers={"200": 3.0},
    )
    # base 10 * channel 2.0 * role 3.0 * boost 1.5 = 90
    assert compute_final_xp(10, 100, [200], cfg, has_leetcode_boost=True) == 90


def test_leetcode_boost_zero_base():
    cfg = make_cfg()
    assert compute_final_xp(0, 100, [], cfg, has_leetcode_boost=True) == 0
