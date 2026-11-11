"""news_issue table

Revision ID: b7d2c9e41a63
Revises: a4c7e19b52d8
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b7d2c9e41a63'
down_revision: Union[str, None] = 'a4c7e19b52d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.106 — 뉴스 이슈 묶음('오늘의 이슈', 배치 단위).
    op.create_table(
        'news_issue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('batch_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('confidence', sa.String(length=10), nullable=False),
        sa.Column('continuing', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('facts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('interpretation', sa.Text(), server_default='', nullable=False),
        sa.Column('counter', sa.Text(), server_default='', nullable=False),
        sa.Column('beneficiaries', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('victims', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('status', sa.String(length=10), server_default='new', nullable=False),
        sa.Column('narrative_id', sa.Integer(), nullable=True),
        sa.Column('generated_by', sa.String(length=60), nullable=True),
        sa.Column('news_total', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['narrative_id'], ['market_narrative.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_news_issue_batch_at', 'news_issue', ['batch_at'])


def downgrade() -> None:
    op.drop_index('ix_news_issue_batch_at', table_name='news_issue')
    op.drop_table('news_issue')
