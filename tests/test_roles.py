"""Unit tests for role-persistence policy (core.roles).

These pure helpers decide which roles survive a leave/rejoin round-trip without
needing a live Discord guild.
"""

from __future__ import annotations

from core.roles import (
    DANGEROUS_PERMISSIONS,
    has_dangerous_permission,
    role_is_restorable,
)


def test_plain_role_is_restorable() -> None:
    assert role_is_restorable(
        is_default=False,
        is_managed=False,
        has_dangerous_perm=False,
        is_above_bot=False,
    )


def test_each_flag_blocks_restoration() -> None:
    base = dict(
        is_default=False,
        is_managed=False,
        has_dangerous_perm=False,
        is_above_bot=False,
    )
    for flag in base:
        kwargs = dict(base)
        kwargs[flag] = True
        assert not role_is_restorable(**kwargs), f"{flag}=True should block"


def test_has_dangerous_permission_detects_admin() -> None:
    assert has_dangerous_permission({"administrator"})
    assert has_dangerous_permission({"send_messages", "manage_roles"})


def test_has_dangerous_permission_ignores_harmless() -> None:
    assert not has_dangerous_permission(set())
    assert not has_dangerous_permission({"send_messages", "add_reactions"})


def test_every_dangerous_permission_is_detected() -> None:
    for perm in DANGEROUS_PERMISSIONS:
        assert has_dangerous_permission({perm}), f"{perm} should be dangerous"
