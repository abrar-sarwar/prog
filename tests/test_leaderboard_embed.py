"""Tests for the pure leaderboard-embed description formatter."""

from __future__ import annotations

from core.leaderboard_embed import (
    LeaderboardRow,
    format_leaderboard_description,
)


def _rows(n: int) -> list[LeaderboardRow]:
    return [
        LeaderboardRow(rank=i + 1, user_id=100 + i, level=99 - i, xp=100_000 - i * 5_000)
        for i in range(n)
    ]


def test_empty_returns_placeholder() -> None:
    assert "No one has earned XP" in format_leaderboard_description([])


def test_medals_present_for_top_three() -> None:
    desc = format_leaderboard_description(_rows(10))
    assert "🥇" in desc
    assert "🥈" in desc
    assert "🥉" in desc


def test_first_place_stats_are_bold() -> None:
    desc = format_leaderboard_description(_rows(3))
    first_line = desc.splitlines()[0]
    assert first_line.startswith("🥇")
    assert "**" in first_line  # level + xp emphasised for the hero


def test_second_third_not_bold() -> None:
    desc = format_leaderboard_description(_rows(3))
    second_line = desc.splitlines()[1]
    assert second_line.startswith("🥈")
    assert "**" not in second_line


def test_mentions_rendered() -> None:
    desc = format_leaderboard_description(_rows(5))
    assert "<@100>" in desc  # rank 1
    assert "<@104>" in desc  # rank 5


def test_xp_thousands_separator() -> None:
    desc = format_leaderboard_description(_rows(1))
    assert "100,000 XP" in desc


def test_field_rows_use_monospace_aligned_rank() -> None:
    desc = format_leaderboard_description(_rows(10))
    # Rank 4 padded to width 2 inside inline code.
    assert "` 4`" in desc
    assert "`10`" in desc


def test_blank_line_separates_podium_from_field() -> None:
    desc = format_leaderboard_description(_rows(10))
    assert "\n\n" in desc


def test_fewer_than_three_has_no_field_separator() -> None:
    desc = format_leaderboard_description(_rows(2))
    assert "\n\n" not in desc
    assert desc.count("\n") == 1
