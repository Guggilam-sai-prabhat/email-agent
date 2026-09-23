"""add local review workflow (email_reviews, review_audit_log)

Revision ID: 9a4df08f55e8
Revises: 384f7e411e70
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.database.models


revision: str = '9a4df08f55e8'
down_revision: Union[str, Sequence[str], None] = '384f7e411e70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('email_reviews',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_analysis_id', sa.String(length=36), nullable=False),
    sa.Column('review_status', sa.String(length=20), nullable=False),
    sa.Column('reviewed_at', app.database.models.UTCDateTime(timezone=True), nullable=True),
    sa.Column('completed_at', app.database.models.UTCDateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', app.database.models.UTCDateTime(timezone=True), nullable=True),
    sa.Column('user_note', sa.Text(), nullable=True),
    sa.Column('created_at', app.database.models.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.database.models.UTCDateTime(timezone=True), nullable=False),
    sa.CheckConstraint("review_status IN ('pending', 'reviewed', 'completed', 'dismissed')", name='ck_email_reviews_status'),
    sa.ForeignKeyConstraint(['email_analysis_id'], ['email_analyses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email_analysis_id', name='uq_email_reviews_email_analysis_id'),
    )
    op.create_table('review_audit_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_analysis_id', sa.String(length=36), nullable=False),
    sa.Column('previous_status', sa.String(length=20), nullable=True),
    sa.Column('new_status', sa.String(length=20), nullable=False),
    sa.Column('created_at', app.database.models.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['email_analysis_id'], ['email_analyses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('review_audit_log')
    op.drop_table('email_reviews')
