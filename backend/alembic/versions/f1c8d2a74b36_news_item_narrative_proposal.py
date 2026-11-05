"""news_item, narrative_proposal tables

Revision ID: f1c8d2a74b36
Revises: e7b3a915c4d8
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f1c8d2a74b36'
down_revision: Union[str, None] = 'e7b3a915c4d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.90-2 — 뉴스 수집 + LLM 내러티브 후보 제안(사용자 승인 필수).
    op.create_table(
        'news_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url'),
    )
    op.create_index('ix_news_item_published_at', 'news_item', ['published_at'])
    op.create_table(
        'narrative_proposal',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('beneficiaries', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('victims', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('confidence', sa.String(length=10), nullable=False),
        sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('status', sa.String(length=10), server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_narrative_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['approved_narrative_id'], ['market_narrative.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_narrative_proposal_status', 'narrative_proposal', ['status', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_narrative_proposal_status', table_name='narrative_proposal')
    op.drop_table('narrative_proposal')
    op.drop_index('ix_news_item_published_at', table_name='news_item')
    op.drop_table('news_item')
