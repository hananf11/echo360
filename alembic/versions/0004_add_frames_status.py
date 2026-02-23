"""Add frames_status to lectures table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.add_column(sa.Column("frames_status", sa.String(), nullable=False, server_default="pending"))


def downgrade() -> None:
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.drop_column("frames_status")
