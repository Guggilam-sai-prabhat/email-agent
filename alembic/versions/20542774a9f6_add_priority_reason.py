"""add priority_reason to email_analyses

Revision ID: 20542774a9f6
Revises: 9a4df08f55e8
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20542774a9f6'
down_revision: Union[str, Sequence[str], None] = '9a4df08f55e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('email_analyses', sa.Column('priority_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('email_analyses', 'priority_reason')
