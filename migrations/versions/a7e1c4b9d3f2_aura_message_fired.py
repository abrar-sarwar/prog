"""aura message fired + level default 0 + recompute levels

Revision ID: a7e1c4b9d3f2
Revises: 39a036b2e6c4
Create Date: 2026-05-28 12:00:00.000000

Schema changes:

* ``users.aura_message_fired`` (Boolean, NOT NULL, default false) - one-shot
  flag for the level-100 Aura announcement. Persists across level drops
  so the message never re-fires.
* ``users.level`` server default changed 1 -> 0 so brand-new rows match
  the new curve (a 0-XP user is level 0 until they earn ~32 XP).

Data backfill:

* Existing ``users`` rows are re-leveled in-place under the new curve
  (``0.4n^2 + 12n + 20`` per level, capped at 100). The formula is
  inlined here on purpose: migrations should be hermetic and not import
  application code that may change in future revisions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7e1c4b9d3f2"
down_revision: Union[str, Sequence[str], None] = "39a036b2e6c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEVEL_CAP = 100


def _xp_for_level(n: int) -> int:
    """XP cost to enter level ``n``. Mirrors :func:`core.leveling.xp_for_level`."""
    return round(0.4 * n * n + 12 * n + 20)


def _build_cumulative() -> list[int]:
    table = [0]
    running = 0
    for n in range(1, _LEVEL_CAP + 1):
        running += _xp_for_level(n)
        table.append(running)
    return table


def _level_from_xp(xp: int, table: list[int]) -> int:
    if xp <= 0:
        return 0
    for level in range(_LEVEL_CAP, -1, -1):
        if xp >= table[level]:
            return level
    return 0


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "aura_message_fired",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.alter_column("users", "level", server_default=sa.text("0"))

    # Re-level every existing user under the new curve so the first XP
    # grant after migration doesn't dispatch a spurious "demotion" event.
    conn = op.get_bind()
    table = _build_cumulative()
    rows = conn.execute(sa.text("SELECT id, xp FROM users")).fetchall()
    for row in rows:
        new_level = _level_from_xp(int(row.xp), table)
        conn.execute(
            sa.text("UPDATE users SET level = :lvl WHERE id = :id"),
            {"lvl": new_level, "id": row.id},
        )
    # Anyone who is *currently* at level 100 keeps their Aura flag set so
    # we don't ping them again on the next grant.
    conn.execute(
        sa.text(
            "UPDATE users SET aura_message_fired = true WHERE level = :cap"
        ),
        {"cap": _LEVEL_CAP},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("users", "level", server_default=sa.text("1"))
    op.drop_column("users", "aura_message_fired")
