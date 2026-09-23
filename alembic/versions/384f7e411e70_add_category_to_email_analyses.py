"""add category to email_analyses

Revision ID: 384f7e411e70
Revises: 86c12cb88f4d
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '384f7e411e70'
down_revision: Union[str, Sequence[str], None] = '86c12cb88f4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('email_analyses', sa.Column('category', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('email_analyses', 'category')
