"""welcome-on-join columns on guild_config

Adds three nullable columns supporting the opt-in welcome message posted when
a member joins:

* ``welcome_channel_id``        -- where the welcome posts (NULL = feature off)
* ``welcome_intro_channel_id``  -- target of the ``{intro_channel}`` placeholder
* ``welcome_message``           -- per-guild override (NULL = default template)

No backfill: NULL is the correct value for every existing guild, leaving the
feature off until an admin opts in via ``/setup-welcome-channel``.

Revision ID: d5a7b30f9c12
Revises: c3f9a2d18e45
Create Date: 2026-06-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5a7b30f9c12"
down_revision: Union[str, Sequence[str], None] = "c3f9a2d18e45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_config",
        sa.Column("welcome_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "guild_config",
        sa.Column("welcome_intro_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "guild_config",
        sa.Column("welcome_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("guild_config", "welcome_message")
    op.drop_column("guild_config", "welcome_intro_channel_id")
    op.drop_column("guild_config", "welcome_channel_id")
