"""trade_candidate table

Revision ID: c5a91d3e7f20
Revises: b7e41c2a9d05
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5a91d3e7f20'
down_revision: Union[str, None] = 'b7e41c2a9d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.89-1 — 수동 매수 종목 추천/후보 목록.
    op.create_table(
        'trade_candidate',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('multiple', sa.SmallInteger(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('scenario', sa.String(length=100), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('valid_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('trade_candidate')
