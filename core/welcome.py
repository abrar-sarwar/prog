"""Welcome-on-join message rendering.

Pure logic for the welcome message posted when a member joins a guild. Kept
free of Discord imports so it can be unit-tested directly.

The welcome message supports three placeholders:

* ``{user}``          -- a mention of the joining member
* ``{server}``        -- the guild's name
* ``{intro_channel}`` -- a mention/link to the configured "introduce yourself"
  channel, or :data:`INTRO_CHANNEL_FALLBACK` when none is configured

:func:`render_welcome` substitutes only those three tokens and deliberately
leaves everything else (including stray or unbalanced braces in admin-edited
text) untouched, so a malformed custom message can never crash the join
handler.
"""

from __future__ import annotations

DEFAULT_WELCOME_MESSAGE: str = (
    "welcome {user}! take a look at what we offer here in the {server} "
    "server and introduce yourself in {intro_channel}"
)
"""The message used when a guild has not set a custom welcome message."""

INTRO_CHANNEL_FALLBACK: str = "the intro channel"
"""Rendered in place of ``{intro_channel}`` when no intro channel is set."""


def render_welcome(
    template: str,
    *,
    user: str,
    server: str,
    intro_channel: str,
) -> str:
    """Substitute the three known placeholders into ``template``.

    Only ``{user}``, ``{server}`` and ``{intro_channel}`` are replaced. Unknown
    placeholders and stray/unbalanced braces are left exactly as written -- we
    use plain string replacement rather than :meth:`str.format`, which would
    raise on admin-edited text containing other ``{...}`` sequences.
    """
    replacements = {
        "{user}": user,
        "{server}": server,
        "{intro_channel}": intro_channel,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered
