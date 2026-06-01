"""Unit tests for welcome-on-join message rendering.

``render_welcome`` must substitute the three known placeholders and, crucially,
never raise on admin-edited text -- stray braces and unknown placeholders are
left intact rather than crashing the join handler.
"""

from __future__ import annotations

from core.welcome import (
    DEFAULT_WELCOME_MESSAGE,
    INTRO_CHANNEL_FALLBACK,
    render_welcome,
)


def test_default_template_renders_all_placeholders() -> None:
    out = render_welcome(
        DEFAULT_WELCOME_MESSAGE,
        user="<@123>",
        server="progsu",
        intro_channel="<#456>",
    )
    assert "<@123>" in out
    assert "progsu" in out
    assert "<#456>" in out
    # No raw placeholder tokens left behind.
    assert "{user}" not in out
    assert "{server}" not in out
    assert "{intro_channel}" not in out


def test_subset_of_placeholders_renders() -> None:
    out = render_welcome(
        "hi {user}, welcome!",
        user="<@1>",
        server="x",
        intro_channel="<#2>",
    )
    assert out == "hi <@1>, welcome!"


def test_intro_channel_fallback_used_when_absent() -> None:
    out = render_welcome(
        "say hi in {intro_channel}",
        user="<@1>",
        server="x",
        intro_channel=INTRO_CHANNEL_FALLBACK,
    )
    assert out == f"say hi in {INTRO_CHANNEL_FALLBACK}"


def test_malformed_and_unknown_braces_do_not_raise() -> None:
    # Unbalanced brace, unknown placeholder, and a literal brace pair -- none
    # of these should raise, and they should pass through untouched.
    template = "hey {user} {not_a_key} {unclosed and {{literal}} :)"
    out = render_welcome(
        template,
        user="<@9>",
        server="s",
        intro_channel="<#0>",
    )
    assert out == "hey <@9> {not_a_key} {unclosed and {{literal}} :)"
