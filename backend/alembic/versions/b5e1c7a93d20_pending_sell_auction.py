"""auto_trade_state 동시호가 대기 매도 컬럼 (Phase 5.134)

Revision ID: b5e1c7a93d20
Revises: a7c3e9d15b42
Create Date: 2026-11-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5e1c7a93d20'
down_revision: Union[str, None] = 'a7c3e9d15b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 동시호가 중 낸 매도 주문을 정산 전까지 보관한다. 전부 nullable — 기존 행·기존 엔진 코드에 영향 없음.
    op.add_column('auto_trade_state', sa.Column('pending_sell_order_no', sa.String(length=30), nullable=True))
    op.add_column('auto_trade_state', sa.Column('pending_sell_qty', sa.SmallInteger(), nullable=True))
    op.add_column('auto_trade_state', sa.Column('pending_sell_event', sa.String(length=20), nullable=True))
    op.add_column('auto_trade_state', sa.Column('pending_sell_reason', sa.Text(), nullable=True))
    op.add_column('auto_trade_state', sa.Column('pending_sell_price', sa.Numeric(12, 2), nullable=True))
    op.add_column('auto_trade_state', sa.Column('pending_sell_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ('pending_sell_at', 'pending_sell_price', 'pending_sell_reason',
                'pending_sell_event', 'pending_sell_qty', 'pending_sell_order_no'):
        op.drop_column('auto_trade_state', col)
