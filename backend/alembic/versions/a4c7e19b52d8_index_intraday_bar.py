"""index_intraday_bar table

Revision ID: a4c7e19b52d8
Revises: f1c8d2a74b36
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4c7e19b52d8'
down_revision: Union[str, None] = 'f1c8d2a74b36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.93 — 지수(코스피/코스닥) 1분봉 이력(연구용).
    op.create_table(
        'index_intraday_bar',
        sa.Column('code', sa.String(length=10), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('time', sa.String(length=4), nullable=False),
        sa.Column('open', sa.Numeric(12, 2), nullable=True),
        sa.Column('high', sa.Numeric(12, 2), nullable=True),
        sa.Column('low', sa.Numeric(12, 2), nullable=True),
        sa.Column('close', sa.Numeric(12, 2), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'date', 'time'),
    )


def downgrade() -> None:
    op.drop_table('index_intraday_bar')
