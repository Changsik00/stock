"""stock_intraday_bar table

Revision ID: a3c956903a33
Revises: 7908730db339
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c956903a33'
down_revision: Union[str, None] = '7908730db339'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.75 — 자동매매 대상 종목(0167A0) 1분봉 이력을 매일 밤 배치로
    # 쌓기 시작한다. §5.74 백테스트가 일봉 근사로만 검증 가능했던 한계를
    # 시간이 지나며 해소하기 위함(collectors/stock_intraday_history.py).
    op.create_table(
        'stock_intraday_bar',
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('time', sa.String(length=4), nullable=False),
        sa.Column('open', sa.BigInteger(), nullable=True),
        sa.Column('high', sa.BigInteger(), nullable=True),
        sa.Column('low', sa.BigInteger(), nullable=True),
        sa.Column('close', sa.BigInteger(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['code'], ['stocks.code']),
        sa.PrimaryKeyConstraint('code', 'date', 'time'),
    )


def downgrade() -> None:
    op.drop_table('stock_intraday_bar')
