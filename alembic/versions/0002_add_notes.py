"""Add notes support: notes table + notes_status/notes_model on lectures.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add notes columns to lectures (batch mode for SQLite)
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.add_column(sa.Column("notes_status", sa.String(), nullable=False, server_default="pending"))
        batch_op.add_column(sa.Column("notes_model", sa.String(), nullable=True))

    # Create notes table
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lecture_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("frame_timestamps", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["lecture_id"], ["lectures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("notes")
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.drop_column("notes_model")
        batch_op.drop_column("notes_status")
