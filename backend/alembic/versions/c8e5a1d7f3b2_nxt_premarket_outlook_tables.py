"""nxt_intraday_bar / krx_regular_close / premarket_outlook_log tables

Revision ID: c8e5a1d7f3b2
Revises: b7d2c9e41a63
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8e5a1d7f3b2'
down_revision: Union[str, None] = 'b7d2c9e41a63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.107 — NXT 5분봉, KRX 정규장 종가, 프리마켓 예상 시작점 표본 외 기록.
    op.create_table(
        'nxt_intraday_bar',
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('time', sa.String(length=4), nullable=False),
        sa.Column('open', sa.BigInteger(), nullable=True),
        sa.Column('high', sa.BigInteger(), nullable=True),
        sa.Column('low', sa.BigInteger(), nullable=True),
        sa.Column('close', sa.BigInteger(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'date', 'time'),
    )
    op.create_table(
        'krx_regular_close',
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('close', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('code', 'date'),
    )
    op.create_table(
        'premarket_outlook_log',
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('nq_change_pct', sa.Float(), nullable=True),
        sa.Column('expected_gap_pct', sa.Float(), nullable=True),
        sa.Column('resid_std_pct', sa.Float(), nullable=True),
        sa.Column('n', sa.Integer(), nullable=True),
        sa.Column('alpha_pct', sa.Float(), nullable=True),
        sa.Column('beta', sa.Float(), nullable=True),
        sa.Column('base_close', sa.BigInteger(), nullable=True),
        sa.Column('actual_open', sa.BigInteger(), nullable=True),
        sa.Column('actual_gap_pct', sa.Float(), nullable=True),
        sa.Column('error_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('date', 'code'),
    )


def downgrade() -> None:
    op.drop_table('premarket_outlook_log')
    op.drop_table('krx_regular_close')
    op.drop_table('nxt_intraday_bar')
