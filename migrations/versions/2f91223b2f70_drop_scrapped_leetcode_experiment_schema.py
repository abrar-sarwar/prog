"""drop scrapped leetcode experiment schema

Removes untracked schema drift from a scrapped LeetCode experiment that lived in
prod but was never captured by a migration: the ``leetcode_history`` and
``leetcode_questions`` tables, plus six ``leetcode_*`` columns on ``users``.
Dropping them here makes the migrations the source of truth again so test and
prod stay in sync.

``downgrade()`` recreates the structure exactly as it existed in prod (columns,
types, PK/FK, unique constraint, index) so the migration is reversible. Note it
restores structure only, not the dropped rows/values.

Revision ID: 2f91223b2f70
Revises: e6c1f8b24a90
Create Date: 2026-06-03 13:57:36.742662
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f91223b2f70"
down_revision: Union[str, Sequence[str], None] = "e6c1f8b24a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These objects are untracked drift: they exist in prod but were never
    # created by any migration, so a fresh-from-scratch DB (e.g. the test DB)
    # does not have them. Use IF EXISTS so this migration is a real drop on
    # prod, a no-op everywhere else, and the chain stays runnable from zero.

    # Drop the six leetcode_* columns on users.
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_thread_created_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_thread_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_active_question_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_completed_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_streak")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS leetcode_total")

    # Drop tables child-first (leetcode_history FKs leetcode_questions).
    op.execute("DROP INDEX IF EXISTS ix_leetcode_history_guild_id")
    op.execute("DROP TABLE IF EXISTS leetcode_history")
    op.execute("DROP TABLE IF EXISTS leetcode_questions")


def downgrade() -> None:
    # Recreate parent table first so the FK below can reference it.
    op.create_table(
        "leetcode_questions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", name="leetcode_questions_title_key"),
    )

    op.create_table(
        "leetcode_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["leetcode_questions.id"],
            name="leetcode_history_question_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_leetcode_history_guild_id", "leetcode_history", ["guild_id"]
    )

    # Restore the six users columns in their original order/types/defaults.
    op.add_column(
        "users",
        sa.Column(
            "leetcode_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "leetcode_streak",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "leetcode_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("leetcode_active_question_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("leetcode_thread_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "leetcode_thread_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
