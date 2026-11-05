"""market_narrative table

Revision ID: e7b3a915c4d8
Revises: c5a91d3e7f20
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e7b3a915c4d8'
down_revision: Union[str, None] = 'c5a91d3e7f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.90-1 — 시장 내러티브 메모(수동 입력).
    op.create_table(
        'market_narrative',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('beneficiaries', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('victims', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('confidence', sa.String(length=10), nullable=False),
        sa.Column('expires_on', sa.Date(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('market_narrative')
