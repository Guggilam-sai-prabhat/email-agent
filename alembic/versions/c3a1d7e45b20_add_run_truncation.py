"""add truncated/unscanned_count to processing_runs

Revision ID: c3a1d7e45b20
Revises: 20542774a9f6
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3a1d7e45b20'
down_revision: Union[str, Sequence[str], None] = '20542774a9f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('processing_runs', sa.Column('truncated', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('processing_runs', sa.Column('unscanned_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('processing_runs', 'unscanned_count')
    op.drop_column('processing_runs', 'truncated')
