"""Pure formatting for the persistent leaderboard embed.

The persistent leaderboard message is a single Discord *embed* (not an
image): a top-10 list rendered as one description block. This module owns
only the text formatting so it can be unit-tested without Discord; the
cog (:mod:`cogs.leaderboard_channel`) wraps the returned string in a
``discord.Embed`` and handles sending/editing.

Hierarchy (translated from the design reference, which embeds can't
reproduce literally):

* **1st** is the hero — gold medal, bold level *and* XP.
* **2nd / 3rd** are secondary — silver/bronze medals, plain stats.
* **4th-10th** are a compact monospace-ranked list, lighter still.

A blank line separates the podium from the field so the "podium then
list" descent reads at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Medal emoji per podium position.
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

_EMPTY = "_No one has earned XP yet — be the first._"


@dataclass(frozen=True)
class LeaderboardRow:
    """One ranked entry for the embed."""

    rank: int
    user_id: int
    level: int
    xp: int


def _mention(user_id: int) -> str:
    # Mentions inside an embed description render as the member's name but
    # never send a notification, which is exactly what we want here.
    return f"<@{user_id}>"


def format_leaderboard_description(rows: Sequence[LeaderboardRow]) -> str:
    """Render the top-10 rows into a single embed-description string.

    Returns a placeholder line when ``rows`` is empty so the persistent
    message still has valid content.
    """
    if not rows:
        return _EMPTY

    lines: list[str] = []

    for row in (r for r in rows if r.rank <= 3):
        medal = _MEDALS[row.rank]
        if row.rank == 1:
            lines.append(
                f"{medal}  {_mention(row.user_id)}  ·  "
                f"Level **{row.level}**  ·  **{row.xp:,} XP**"
            )
        else:
            lines.append(
                f"{medal}  {_mention(row.user_id)}  ·  "
                f"Level {row.level}  ·  {row.xp:,} XP"
            )

    field = [r for r in rows if r.rank >= 4]
    if field:
        lines.append("")  # blank line: podium ends, list begins
        for row in field:
            # Right-pad the rank to width 2 inside inline code so 4-10
            # stay column-aligned in a monospace run.
            lines.append(
                f"`{row.rank:>2}`  {_mention(row.user_id)}  ·  "
                f"Lv {row.level}  ·  {row.xp:,} XP"
            )

    return "\n".join(lines)
