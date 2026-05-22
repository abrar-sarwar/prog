"""Unit tests for the level-up message helper in ``core.constants``."""

from __future__ import annotations

from core.constants import levelup_message


def _has_mention_placeholder(template: str) -> bool:
    return "{mention}" in template


def test_band_1_to_10_low_boundary():
    msg = levelup_message(1)
    assert _has_mention_placeholder(msg)
    assert "noob" in msg


def test_band_1_to_10_high_boundary():
    assert levelup_message(10) == levelup_message(1)


def test_band_11_to_20_low_boundary():
    msg = levelup_message(11)
    assert "knight" in msg
    assert msg != levelup_message(10)


def test_band_11_to_20_high_boundary():
    assert levelup_message(20) == levelup_message(11)


def test_band_21_to_30_low_boundary():
    msg = levelup_message(21)
    assert "progmaster" in msg
    assert msg != levelup_message(20)


def test_band_21_to_30_high_boundary():
    assert levelup_message(30) == levelup_message(21)


def test_band_31_to_40_low_boundary():
    msg = levelup_message(31)
    assert "9-5" in msg
    assert msg != levelup_message(30)


def test_band_31_to_40_high_boundary():
    assert levelup_message(40) == levelup_message(31)


def test_band_41_to_49_low_boundary():
    msg = levelup_message(41)
    assert "grasses" in msg
    assert msg != levelup_message(40)


def test_band_41_to_49_high_boundary():
    assert levelup_message(49) == levelup_message(41)


def test_level_50_is_final_message():
    msg = levelup_message(50)
    assert "final lvl" in msg
    assert "j*b" in msg
    assert msg != levelup_message(49)


def test_level_51_falls_back_to_grass_band():
    # Out-of-range fallback (documented): use the level-41 message.
    assert levelup_message(51) == levelup_message(41)


def test_all_templates_have_mention_placeholder():
    for level in (1, 10, 11, 20, 21, 30, 31, 40, 41, 49, 50, 51, 999):
        assert _has_mention_placeholder(levelup_message(level)), (
            f"level {level} message is missing the {{mention}} placeholder"
        )
