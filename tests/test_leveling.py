"""Unit tests for the leveling curve, tier ladder, and message helpers.

Two threshold suites are covered:

* The exit thresholds listed in the spec (`end lvl 9` = 51 msgs, ... ,
  `end lvl 99` = 9,897 msgs) at the base rate of 20 XP/msg, with the
  ±2 msg tolerance the spec calls for.
* The tier-band boundaries (lvl 9 vs 10, lvl 99 vs 100, Aura cap).

The ``admin_rank_change_action`` pure helper is exercised separately so
the admin cog can call it without needing to mock Discord.
"""

from __future__ import annotations

import pytest

from core.leveling import (
    LEVEL_CAP,
    LEVEL_MESSAGES,
    TIER_BANDS,
    AdminRankAction,
    admin_rank_change_action,
    cumulative_xp_to_level,
    get_level_message,
    get_tier_for_level,
    get_tier_role_name,
    level_from_total_xp,
    xp_for_level,
)


# ---------------------------------------------------------------------------
# Formula + threshold sanity checks
# ---------------------------------------------------------------------------

# (level reached, expected msgs at 20 XP/msg base rate)
_SPEC_THRESHOLDS = [
    (10, 51),
    (20, 203),
    (30, 498),
    (40, 975),
    (50, 1_674),
    (60, 2_634),
    (70, 3_897),
    (80, 5_502),
    (90, 7_488),
    (100, 9_897),
]

_XP_PER_MSG = 20
_MSG_TOLERANCE = 2  # spec allows ±2 msgs of curve drift


@pytest.mark.parametrize("level,expected_msgs", _SPEC_THRESHOLDS)
def test_cumulative_xp_matches_spec_thresholds(level: int, expected_msgs: int):
    """Each spec-listed exit threshold falls within ±2 msgs of the curve."""
    actual_msgs = cumulative_xp_to_level(level) / _XP_PER_MSG
    assert abs(actual_msgs - expected_msgs) <= _MSG_TOLERANCE, (
        f"level {level}: spec says {expected_msgs} msgs, "
        f"curve gives {actual_msgs:.1f} msgs"
    )


def test_xp_for_level_curve_value_at_1():
    """Sanity: the curve coefficients produce the expected rounded value."""
    # 0.4*1 + 12 + 20 = 32.4 -> 32
    assert xp_for_level(1) == 32


def test_xp_for_level_rejects_out_of_range():
    with pytest.raises(ValueError):
        xp_for_level(0)
    with pytest.raises(ValueError):
        xp_for_level(LEVEL_CAP + 1)


def test_cumulative_xp_to_level_zero_is_zero():
    """A brand new user (0 XP) has cleared 0 levels."""
    assert cumulative_xp_to_level(0) == 0


def test_cumulative_xp_to_level_rejects_out_of_range():
    with pytest.raises(ValueError):
        cumulative_xp_to_level(-1)
    with pytest.raises(ValueError):
        cumulative_xp_to_level(LEVEL_CAP + 1)


def test_level_from_total_xp_zero():
    """Brand new user (0 XP) is level 0, not in any tier yet."""
    assert level_from_total_xp(0) == 0


def test_level_from_total_xp_negative_clamps_to_zero():
    """Defensive: a negative input shouldn't crash."""
    assert level_from_total_xp(-100) == 0


def test_level_from_total_xp_round_trips_each_level():
    """cumulative_xp_to_level(L) decodes back to lvl L (inclusive thresholds)."""
    for level in range(0, LEVEL_CAP + 1):
        assert level_from_total_xp(cumulative_xp_to_level(level)) == level


def test_level_from_total_xp_just_below_threshold():
    """One XP short of the threshold for level L stays at L-1."""
    for level in range(1, LEVEL_CAP + 1):
        just_below = cumulative_xp_to_level(level) - 1
        assert level_from_total_xp(just_below) == level - 1


def test_level_from_total_xp_caps_at_max():
    """Even at absurd XP, the user clamps to LEVEL_CAP."""
    huge_xp = cumulative_xp_to_level(LEVEL_CAP) * 10
    assert level_from_total_xp(huge_xp) == LEVEL_CAP


# ---------------------------------------------------------------------------
# Tier lookups
# ---------------------------------------------------------------------------


def test_tier_for_level_zero_is_none():
    """Level 0 has no tier - the user hasn't earned enough XP to be a Freshie."""
    assert get_tier_for_level(0) is None
    assert get_tier_role_name(0) is None


@pytest.mark.parametrize(
    "level,expected_tier",
    [
        # boundary cases the spec explicitly calls out
        (9, "Freshie"),
        (10, "Viber"),
        (99, "Ascendant"),
        (100, "Aura"),
        # interior samples for every band
        (1, "Freshie"),
        (19, "Viber"),
        (20, "Innovator"),
        (29, "Innovator"),
        (30, "Hustler"),
        (39, "Hustler"),
        (40, "Yapper"),
        (49, "Yapper"),
        (50, "Chud"),
        (59, "Chud"),
        (60, "Yappatron"),
        (69, "Yappatron"),
        (70, "Challenger"),
        (79, "Challenger"),
        # Ascendant is the widened band: 80..99 (no Sigma in this layout).
        (80, "Ascendant"),
        (89, "Ascendant"),
        (90, "Ascendant"),
    ],
)
def test_get_tier_for_level_boundaries(level: int, expected_tier: str):
    assert get_tier_for_level(level) == expected_tier
    # The role name matches the tier name for every band in the new scheme.
    assert get_tier_role_name(level) == expected_tier


def test_get_tier_for_level_above_cap_clamps_to_aura():
    """Defensive: an admin overshoot still resolves to Aura."""
    assert get_tier_for_level(LEVEL_CAP + 50) == "Aura"


def test_tier_bands_are_exhaustive_and_contiguous():
    """Every level 1..LEVEL_CAP is covered by exactly one band."""
    seen: set[int] = set()
    for lo, hi, _name, _role in TIER_BANDS:
        for lvl in range(lo, hi + 1):
            assert lvl not in seen, f"level {lvl} listed in two bands"
            seen.add(lvl)
    assert seen == set(range(1, LEVEL_CAP + 1))


# ---------------------------------------------------------------------------
# Level-up message templates
# ---------------------------------------------------------------------------

_MENTION = "<@1234567890>"


def test_every_tier_has_a_message():
    """LEVEL_MESSAGES has exactly one entry per tier name."""
    tier_names = {name for _lo, _hi, name, _role in TIER_BANDS}
    assert set(LEVEL_MESSAGES.keys()) == tier_names


def test_messages_are_distinct_per_tier():
    """Sanity: no two tiers share the same template."""
    assert len(set(LEVEL_MESSAGES.values())) == len(LEVEL_MESSAGES)


def test_get_level_message_zero_raises():
    """No template exists for the untiered lvl 0 state."""
    with pytest.raises(ValueError):
        get_level_message(0)


@pytest.mark.parametrize(
    "level,expected_tier",
    [
        (1, "Freshie"),
        (9, "Freshie"),
        (10, "Viber"),
        (20, "Innovator"),
        (30, "Hustler"),
        (40, "Yapper"),
        (50, "Chud"),
        (60, "Yappatron"),
        (70, "Challenger"),
        (80, "Ascendant"),
        (90, "Ascendant"),
        (99, "Ascendant"),
        (100, "Aura"),
    ],
)
def test_get_level_message_returns_correct_template(level: int, expected_tier: str):
    """The template for each tier band matches LEVEL_MESSAGES[tier]."""
    assert get_level_message(level) == LEVEL_MESSAGES[expected_tier]


def test_level_message_renders_user_and_level():
    """{user} and {level} resolve correctly at format-time for non-Aura tiers."""
    for level in (1, 15, 35, 75, 95):
        template = get_level_message(level)
        rendered = template.format(user=_MENTION, level=level)
        assert _MENTION in rendered
        assert str(level) in rendered


def test_aura_template_renders_user_no_everyone():
    """The Aura template embeds the user mention and has no @everyone."""
    template = get_level_message(100)
    rendered = template.format(user=_MENTION, level=100)
    assert _MENTION in rendered
    assert "100" in rendered
    assert "@everyone" not in rendered


# ---------------------------------------------------------------------------
# Admin rank-change pure logic
# ---------------------------------------------------------------------------


def test_admin_promotion_within_band_fires_once_at_new_level():
    """Freshie L5 -> L8 still in Freshie: one message at lvl 8."""
    action = admin_rank_change_action(5, 8, aura_already_fired=False)
    assert action == AdminRankAction(
        fire_message=True, message_level=8, set_aura_flag=False
    )


def test_admin_cross_band_promotion_fires_only_for_destination_band():
    """Freshie L5 -> Yapper L47: one message at lvl 47 (Yapper template)."""
    action = admin_rank_change_action(5, 47, aura_already_fired=False)
    assert action.fire_message is True
    assert action.message_level == 47
    assert action.set_aura_flag is False
    # Confirm the resulting template is the Yapper one, not Freshie's.
    assert get_level_message(action.message_level) == LEVEL_MESSAGES["Yapper"]


def test_admin_demotion_fires_no_message():
    """Yapper L47 -> Viber L12 is a demotion: zero messages."""
    action = admin_rank_change_action(47, 12, aura_already_fired=False)
    assert action.fire_message is False
    assert action.set_aura_flag is False


def test_admin_demotion_within_band_fires_no_message():
    """L8 -> L3 (still Freshie) is still a demotion: zero messages."""
    action = admin_rank_change_action(8, 3, aura_already_fired=False)
    assert action.fire_message is False


def test_admin_noop_fires_no_message():
    """L100 -> L100 (no-op) fires nothing even when the flag is unset."""
    action = admin_rank_change_action(100, 100, aura_already_fired=False)
    assert action.fire_message is False
    assert action.set_aura_flag is False


def test_admin_first_promotion_to_aura_fires_and_sets_flag():
    """L95 -> L100 with flag unset: fire Aura message and set the flag."""
    action = admin_rank_change_action(95, 100, aura_already_fired=False)
    assert action == AdminRankAction(
        fire_message=True, message_level=100, set_aura_flag=True
    )


def test_admin_repeat_promotion_to_aura_suppressed_by_flag():
    """L50 -> L100 with flag already set: no message, no flag change."""
    action = admin_rank_change_action(50, 100, aura_already_fired=True)
    assert action == AdminRankAction(
        fire_message=False, message_level=100, set_aura_flag=False
    )


def test_organic_to_aura_simulation():
    """Single-level cross at lvl 99 -> 100 behaves the same as a big admin jump."""
    first = admin_rank_change_action(99, 100, aura_already_fired=False)
    assert first.fire_message is True
    assert first.set_aura_flag is True
    repeat = admin_rank_change_action(99, 100, aura_already_fired=True)
    assert repeat.fire_message is False
    assert repeat.set_aura_flag is False
