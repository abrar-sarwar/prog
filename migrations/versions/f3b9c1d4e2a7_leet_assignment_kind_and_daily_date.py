"""leet assignment kind and daily_date

Adds two columns to ``leetcode_assignments`` for the /daily vs /leet split:
``kind`` ('daily' | 'practice', server_default 'practice' so existing rows
become practice) and ``daily_date`` (nullable, indexed; set only for daily
sessions to enforce one daily per UTC day).

Revision ID: f3b9c1d4e2a7
Revises: 0a4cac82120a
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3b9c1d4e2a7'
down_revision: Union[str, Sequence[str], None] = '0a4cac82120a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "leetcode_assignments",
        sa.Column("kind", sa.String(), nullable=False, server_default="practice"),
    )
    op.add_column(
        "leetcode_assignments",
        sa.Column("daily_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_leetcode_assignments_daily_date",
        "leetcode_assignments",
        ["daily_date"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_leetcode_assignments_daily_date",
        table_name="leetcode_assignments",
    )
    op.drop_column("leetcode_assignments", "daily_date")
    op.drop_column("leetcode_assignments", "kind")
