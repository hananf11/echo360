"""Add action_items to notes

Revision ID: 0005
Revises: ea5ce8cae5ce
Create Date: 2026-02-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = 'ea5ce8cae5ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('action_items', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_column('action_items')
