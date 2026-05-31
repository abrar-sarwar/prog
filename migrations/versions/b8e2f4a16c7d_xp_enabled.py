"""xp_enabled opt-in toggle on guild_config

XP earning is now opt-in. New guilds start with ``xp_enabled = false`` and
an admin turns it on with /enable-xp (or by setting the level-up channel).
Existing guilds (already using the bot) are flipped to ``true`` here so they
keep working without intervention.

Revision ID: b8e2f4a16c7d
Revises: a7e1c4b9d3f2
Create Date: 2026-05-31 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8e2f4a16c7d"
down_revision: Union[str, Sequence[str], None] = "a7e1c4b9d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New column: opt-out by default at the DB level so future inserts (new
    # guilds) start with XP OFF.
    op.add_column(
        "guild_config",
        sa.Column(
            "xp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Existing guilds were all running with XP on; preserve that.
    op.execute("UPDATE guild_config SET xp_enabled = true")


def downgrade() -> None:
    op.drop_column("guild_config", "xp_enabled")
