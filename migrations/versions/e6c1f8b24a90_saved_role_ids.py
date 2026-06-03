"""saved_role_ids on users (role persistence across leave/rejoin)

Adds ``users.saved_role_ids`` (JSONB list of role IDs, default ``[]``). The
roles a member has when they leave are snapshotted here and re-applied on
rejoin. Default ``[]`` makes every existing row valid with no backfill.

Revision ID: e6c1f8b24a90
Revises: d5a7b30f9c12
Create Date: 2026-06-01 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6c1f8b24a90"
down_revision: Union[str, Sequence[str], None] = "d5a7b30f9c12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "saved_role_ids",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "saved_role_ids")
