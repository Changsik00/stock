"""auto_trade tables

Revision ID: d4afe41f2108
Revises: cb6ce553dc11
Create Date: 2026-08-04 10:03:24.421278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4afe41f2108'
down_revision: Union[str, None] = 'cb6ce553dc11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 자동으로 생성된 diff에는 이 작업과 무관한 기존 드리프트(investor_warning_event/
    # paper_trade 인덱스 삭제)가 섞여 나왔다 — 이 마이그레이션 범위 밖이라 손대지
    # 않고 auto_trade_log/auto_trade_state 두 테이블만 남겼다(PLAN.md §5.54).
    op.create_table('auto_trade_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('event_type', sa.String(length=20), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('reason', sa.String(length=1000), nullable=False),
    sa.Column('signal_snapshot', sa.String(length=2000), nullable=True),
    sa.Column('order_response', sa.String(length=2000), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_auto_trade_log_ts', 'auto_trade_log', ['ts'])
    op.create_table('auto_trade_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('entry_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('entry_qty', sa.SmallInteger(), nullable=True),
    sa.Column('entry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('entry_order_no', sa.String(length=30), nullable=True),
    sa.Column('peak_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    # **안전장치 — 반드시 enabled=False인 시드 행을 직접 심는다** (PLAN.md
    # §5.54 절대 원칙 1). 앱이 처음 기동되는 순간부터 이 행이 이미 "꺼짐" 상태로
    # 존재해야, 라우터/엔진이 행이 없을 때를 대비해 만드는 방어적 기본값에
    # 기대지 않고도 배포 직후부터 안전하다.
    op.execute(
        "INSERT INTO auto_trade_state (id, enabled, status, code) "
        "VALUES (1, false, 'idle', '0167A0')"
    )


def downgrade() -> None:
    op.drop_table('auto_trade_state')
    op.drop_index('ix_auto_trade_log_ts', table_name='auto_trade_log')
    op.drop_table('auto_trade_log')
