"""Unit tests for the progsuvian app-command gate (cogs.checks).

The predicate touches the DB session factory and ``crud.get_guild_config``;
both are monkeypatched so these stay pure unit tests with no live Postgres
or Discord gateway. ``discord.Member`` is swapped for a lightweight fake so
the predicate's ``isinstance`` check engages without constructing a real
gateway object. Coroutines are driven with ``asyncio.run`` since the project
has no pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import cogs.checks as checks
from cogs.checks import ProgsuvianRequired, progsuvian_predicate


class FakeMember:
    """Stands in for ``discord.Member`` (monkeypatched in via ``_member``)."""

    def __init__(self, role_ids: tuple[int, ...]) -> None:
        self.roles = [SimpleNamespace(id=rid) for rid in role_ids]


def _patch_config(monkeypatch, config) -> None:
    """Point the predicate's session factory + crud lookup at a fake config."""

    @asynccontextmanager
    async def _fake_session():
        yield object()  # session is passed straight through to the stub crud

    monkeypatch.setattr(checks, "get_session_factory", lambda: _fake_session)

    async def _get_guild_config(_session, _guild_id):
        return config

    monkeypatch.setattr(checks.crud, "get_guild_config", _get_guild_config)


def _member(monkeypatch, *role_ids: int) -> FakeMember:
    """Build a fake member and make ``isinstance(m, discord.Member)`` True."""
    monkeypatch.setattr(checks.discord, "Member", FakeMember)
    return FakeMember(role_ids)


_GUILD = SimpleNamespace(id=123)


def _interaction(member, *, guild=_GUILD):
    return SimpleNamespace(guild=guild, user=member)


def test_member_with_role_passes(monkeypatch) -> None:
    _patch_config(monkeypatch, SimpleNamespace(progsuvian_role_id=42))
    interaction = _interaction(_member(monkeypatch, 42, 99))
    assert asyncio.run(progsuvian_predicate(interaction)) is True


def test_member_without_role_is_blocked(monkeypatch) -> None:
    _patch_config(monkeypatch, SimpleNamespace(progsuvian_role_id=42))
    interaction = _interaction(_member(monkeypatch, 99))
    with pytest.raises(ProgsuvianRequired) as excinfo:
        asyncio.run(progsuvian_predicate(interaction))
    assert excinfo.value.role_id == 42


def test_fails_open_when_role_unconfigured(monkeypatch) -> None:
    _patch_config(monkeypatch, SimpleNamespace(progsuvian_role_id=None))
    interaction = _interaction(_member(monkeypatch, 99))
    assert asyncio.run(progsuvian_predicate(interaction)) is True


def test_fails_open_when_no_guild_config(monkeypatch) -> None:
    _patch_config(monkeypatch, None)
    interaction = _interaction(_member(monkeypatch, 99))
    assert asyncio.run(progsuvian_predicate(interaction)) is True


def test_fails_open_outside_guild(monkeypatch) -> None:
    # No DB patch needed: the guild-None short-circuit returns before any query.
    interaction = _interaction(_member(monkeypatch, 99), guild=None)
    assert asyncio.run(progsuvian_predicate(interaction)) is True
