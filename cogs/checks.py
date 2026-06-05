"""Shared app-command checks.

:func:`requires_progsuvian` gates a member-facing command behind the
guild's configured ``progsuvian`` base role. That role is the marker of an
onboarded member - :mod:`cogs.onboarding` assigns it (alongside the
``users`` row) on join, first message, and voice join - so gating the
member commands behind it keeps them for members the bot has actually
initialized.

The gate **fails open** when a guild has not configured a progsuvian role
yet (``progsuvian_role_id is None``) or when the interaction is outside a
guild: it must never lock a server out of its own commands before
``/setup-progsuvian`` has run. This mirrors onboarding, which also no-ops
role assignment while the role is unset.

Failure raises :class:`ProgsuvianRequired` (an ``app_commands.CheckFailure``
subclass carrying the role id) so each cog's ``cog_app_command_error`` can
render a friendly ephemeral reply that names the role.
"""

from __future__ import annotations

import discord
from discord import app_commands

from db import crud
from db.engine import get_session_factory


class ProgsuvianRequired(app_commands.CheckFailure):
    """Raised when an invoker lacks the guild's configured progsuvian role."""

    def __init__(self, role_id: int) -> None:
        self.role_id = role_id
        super().__init__(f"missing progsuvian role {role_id}")


async def progsuvian_predicate(interaction: discord.Interaction) -> bool:
    """Return True if the invoker may use a progsuvian-gated command.

    Fails open (returns True) when the interaction is outside a guild or the
    guild has no progsuvian role configured. Otherwise returns True if the
    invoker holds the role, and raises :class:`ProgsuvianRequired` if not.
    """
    member = interaction.user
    if interaction.guild is None or not isinstance(member, discord.Member):
        # Let the command's own out-of-guild handling take over.
        return True
    async with get_session_factory()() as session:
        config = await crud.get_guild_config(session, interaction.guild.id)
    role_id = config.progsuvian_role_id if config is not None else None
    if role_id is None:
        return True  # Fail open: progsuvian role not set up for this guild.
    if any(role.id == role_id for role in member.roles):
        return True
    raise ProgsuvianRequired(role_id)


def requires_progsuvian():
    """App-command check decorator wrapping :func:`progsuvian_predicate`."""
    return app_commands.check(progsuvian_predicate)


def progsuvian_required_message(role_id: int) -> str:
    """Friendly ephemeral copy for a :class:`ProgsuvianRequired` failure."""
    return (
        f"You need the <@&{role_id}> role to use this. It's assigned "
        "automatically - send a message in the server (or rejoin) and "
        "you'll get it."
    )
