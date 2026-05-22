"""Unit tests for ``compute_final_xp``.

The function takes a structural ``MultiplierConfig`` so we don't need a real
ORM ``GuildConfig`` instance; a ``SimpleNamespace`` with the two dict
attributes is enough.
"""

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


def test_no_multipliers_returns_base():
    cfg = make_cfg()
    assert compute_final_xp(20, 100, [200, 201], cfg) == 20


def test_channel_multiplier_applied():
    cfg = make_cfg(channel_multipliers={"100": 1.5})
    assert compute_final_xp(20, 100, [], cfg) == 30


def test_role_multiplier_applied():
    cfg = make_cfg(role_multipliers={"200": 2.0})
    assert compute_final_xp(20, 100, [200], cfg) == 40


def test_channel_and_role_multiply():
    cfg = make_cfg(
        channel_multipliers={"100": 1.5},
        role_multipliers={"200": 2.0},
    )
    # 20 * 1.5 * 2.0 = 60
    assert compute_final_xp(20, 100, [200], cfg) == 60


def test_multiple_roles_pick_highest_not_stacked():
    cfg = make_cfg(role_multipliers={"200": 1.5, "201": 3.0, "202": 2.0})
    # max applicable role mult is 3.0; stacking would give 9.0 - rejected
    assert compute_final_xp(10, 999, [200, 201, 202], cfg) == 30


def test_missing_channel_key_uses_default():
    cfg = make_cfg(channel_multipliers={"999": 2.0})
    # channel 100 is absent; default is 1.0
    assert compute_final_xp(20, 100, [], cfg) == 20


def test_missing_role_key_uses_default():
    cfg = make_cfg(role_multipliers={"999": 2.0})
    # neither member role is in the dict; max defaults to 1.0
    assert compute_final_xp(20, 100, [200, 201], cfg) == 20


def test_member_with_no_roles_defaults_to_one():
    cfg = make_cfg(role_multipliers={"200": 2.0})
    # empty role list -> max(...) default kicks in -> 1.0
    assert compute_final_xp(20, 100, [], cfg) == 20


def test_floor_at_one_when_base_positive():
    cfg = make_cfg(channel_multipliers={"100": 0.01})
    # 20 * 0.01 = 0.2 -> int 0 -> floored to 1 because base_xp > 0
    assert compute_final_xp(20, 100, [], cfg) == 1


def test_zero_base_xp_stays_zero():
    cfg = make_cfg(channel_multipliers={"100": 5.0})
    # base_xp is 0 - the floor only protects positive grants
    assert compute_final_xp(0, 100, [], cfg) == 0


def test_truncates_not_rounds():
    cfg = make_cfg(channel_multipliers={"100": 1.49})
    # 20 * 1.49 = 29.8 -> int 29 (truncation, not rounding)
    assert compute_final_xp(20, 100, [], cfg) == 29
