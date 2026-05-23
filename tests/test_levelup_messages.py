"""Unit tests for the level-up message helper in ``core.constants``.

The helper takes ``(new_level, mention)`` and returns a fully-rendered
string ready for ``channel.send(content=...)``. Tests assert both the
mention and the level number (as plain "lvl N" text, no formatting box)
appear in the output for bands that interpolate the level, and that the
level-50 message keeps its "final lvl" wording with no level number.
"""

from __future__ import annotations

from core.constants import level_up_message

# A stable fake mention - real mentions look like "<@123>". The exact bytes
# don't matter for the assertions; we just verify the helper passes the
# mention through to the rendered string.
_MENTION = "<@123456789>"


def _has_mention(s: str) -> bool:
    return _MENTION in s


def _has_lvl_number(s: str, level: int) -> bool:
    return f"lvl {level}" in s


def test_band_1_to_10_low_boundary_renders_mention_and_level():
    msg = level_up_message(1, _MENTION)
    assert _has_mention(msg)
    assert _has_lvl_number(msg, 1)
    assert "noob" in msg


def test_band_1_to_10_high_boundary_renders_correct_level():
    msg = level_up_message(10, _MENTION)
    assert _has_lvl_number(msg, 10)
    assert "noob" in msg
    # Same template, different level number
    assert msg != level_up_message(1, _MENTION)


def test_band_11_to_20_low_boundary():
    msg = level_up_message(11, _MENTION)
    assert _has_mention(msg)
    assert _has_lvl_number(msg, 11)
    assert "knight" in msg


def test_band_11_to_20_high_boundary():
    msg = level_up_message(20, _MENTION)
    assert _has_lvl_number(msg, 20)
    assert "knight" in msg


def test_band_21_to_30_low_boundary():
    msg = level_up_message(21, _MENTION)
    assert _has_lvl_number(msg, 21)
    assert "progmaster" in msg


def test_band_21_to_30_high_boundary():
    msg = level_up_message(30, _MENTION)
    assert _has_lvl_number(msg, 30)
    assert "progmaster" in msg


def test_band_31_to_40_low_boundary():
    msg = level_up_message(31, _MENTION)
    assert _has_lvl_number(msg, 31)
    assert "9-5" in msg


def test_band_31_to_40_high_boundary():
    msg = level_up_message(40, _MENTION)
    assert _has_lvl_number(msg, 40)
    assert "9-5" in msg


def test_band_41_to_49_low_boundary():
    msg = level_up_message(41, _MENTION)
    assert _has_lvl_number(msg, 41)
    assert "grasses" in msg


def test_band_41_to_49_high_boundary():
    msg = level_up_message(49, _MENTION)
    assert _has_lvl_number(msg, 49)
    assert "grasses" in msg


def test_level_50_is_final_message_without_lvl_number():
    msg = level_up_message(50, _MENTION)
    assert _has_mention(msg)
    assert "final lvl" in msg
    assert "j*b" in msg
    # Per spec: the level-50 message intentionally omits "lvl 50" since
    # "final lvl" already conveys it.
    assert "lvl 50" not in msg


def test_level_51_falls_back_to_grass_band_with_level_number():
    msg = level_up_message(51, _MENTION)
    assert "grasses" in msg
    # Fallback templates still interpolate the level number.
    assert _has_lvl_number(msg, 51)


def test_no_markdown_formatting_around_level_number():
    """The level number is rendered as plain 'lvl N' text - no backticks,
    no bold, no underscores, no code block."""
    for level in (1, 5, 10, 11, 21, 31, 41, 49):
        msg = level_up_message(level, _MENTION)
        assert f"`lvl {level}`" not in msg, f"backticks around 'lvl {level}'"
        assert f"**lvl {level}**" not in msg, f"bold around 'lvl {level}'"
        assert f"__lvl {level}__" not in msg, f"underline around 'lvl {level}'"
        assert f"`{level}`" not in msg, f"backticks around bare number {level}"
        assert f"**{level}**" not in msg, f"bold around bare number {level}"


def test_each_band_uses_a_distinct_template():
    """Sanity check that the six bands don't collide."""
    samples = [
        level_up_message(5, _MENTION),
        level_up_message(15, _MENTION),
        level_up_message(25, _MENTION),
        level_up_message(35, _MENTION),
        level_up_message(45, _MENTION),
        level_up_message(50, _MENTION),
    ]
    # Strip the level-number portion so we compare templates, not renderings.
    skeletons = [s.replace(str(i), "?") for s, i in zip(samples, (5, 15, 25, 35, 45, 50))]
    assert len(set(skeletons)) == 6, f"expected 6 distinct templates, got {len(set(skeletons))}"
