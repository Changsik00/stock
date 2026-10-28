"""etf_pair_sample table

Revision ID: b7e41c2a9d05
Revises: a3c956903a33
Create Date: 2026-10-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e41c2a9d05'
down_revision: Union[str, None] = 'a3c956903a33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.88-1 — 하이닉스 단일종목 레버리지/인버스 2X ETF 쌍의 괴리율·
    # 본주 이론가 대비 시차 분 단위 관찰 표본(collectors/etf_pair_sampler.py).
    op.create_table(
        'etf_pair_sample',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('multiple', sa.SmallInteger(), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('base_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('nav', sa.Numeric(12, 2), nullable=True),
        sa.Column('underlying_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('underlying_change_rate', sa.Numeric(8, 4), nullable=False),
        sa.Column('theory_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('premium_pct', sa.Numeric(8, 4), nullable=True),
        sa.Column('lag_pct', sa.Numeric(8, 4), nullable=True),
        sa.PrimaryKeyConstraint('time', 'code'),
    )


def downgrade() -> None:
    op.drop_table('etf_pair_sample')
