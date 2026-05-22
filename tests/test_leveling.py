"""Unit tests for the leveling curve."""

from __future__ import annotations

import pytest

from core.leveling import cumulative_xp_to_level, level_from_xp, xp_for_level


def test_xp_for_level_1():
    assert xp_for_level(1) == 81


def test_cumulative_xp_to_level_1_is_zero():
    assert cumulative_xp_to_level(1) == 0


def test_cumulative_xp_to_level_2_is_81():
    assert cumulative_xp_to_level(2) == 81


def test_level_from_xp_zero():
    assert level_from_xp(0) == 1


def test_level_from_xp_80():
    assert level_from_xp(80) == 1


def test_level_from_xp_81():
    assert level_from_xp(81) == 2


def test_level_round_trip():
    """For every level 1..50, the cumulative XP for that level decodes back to
    that level."""
    for n in range(1, 51):
        cumulative = cumulative_xp_to_level(n)
        assert level_from_xp(cumulative) == n, (
            f"round-trip failed for level {n} (cumulative={cumulative})"
        )


def test_invalid_level_raises():
    with pytest.raises(ValueError):
        xp_for_level(0)
    with pytest.raises(ValueError):
        cumulative_xp_to_level(0)


def test_negative_xp_clamped():
    """Defensive: negative XP shouldn't crash; treated as 0."""
    assert level_from_xp(-100) == 1
