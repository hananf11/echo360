"""Remove frame extraction columns.

Drops lectures.frames_status and notes.frame_timestamps now that the
frame-extraction feature has been removed.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.drop_column("frames_status")
    with op.batch_alter_table("notes") as batch_op:
        batch_op.drop_column("frame_timestamps")


def downgrade() -> None:
    with op.batch_alter_table("notes") as batch_op:
        batch_op.add_column(sa.Column("frame_timestamps", sa.Text(), nullable=True))
    with op.batch_alter_table("lectures") as batch_op:
        batch_op.add_column(sa.Column("frames_status", sa.String(), nullable=False, server_default="pending"))
