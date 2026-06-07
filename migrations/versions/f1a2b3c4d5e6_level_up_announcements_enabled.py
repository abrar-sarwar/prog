"""level_up_announcements_enabled toggle on guild_config

Adds a per-guild switch for level-up announcement messages. Defaults to true
so existing guilds keep posting; /disable-levelup-message turns it off and
/enable-levelup-message turns it back on. Only the announcement post is gated -
XP, levels, and ladder-role rewards are unaffected.

Revision ID: f1a2b3c4d5e6
Revises: f3b9c1d4e2a7
Create Date: 2026-06-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "f3b9c1d4e2a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_config",
        sa.Column(
            "level_up_announcements_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("guild_config", "level_up_announcements_enabled")
