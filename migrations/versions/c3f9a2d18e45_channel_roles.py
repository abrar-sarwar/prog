"""channel_roles binding map on guild_config

Adds ``guild_config.channel_roles`` (JSONB ``{channel_id: role_id}``,
default ``{}``). The first qualifying message a member sends in a bound
channel grants them the role.

Revision ID: c3f9a2d18e45
Revises: b8e2f4a16c7d
Create Date: 2026-05-31 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3f9a2d18e45"
down_revision: Union[str, Sequence[str], None] = "b8e2f4a16c7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_config",
        sa.Column(
            "channel_roles",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("guild_config", "channel_roles")
