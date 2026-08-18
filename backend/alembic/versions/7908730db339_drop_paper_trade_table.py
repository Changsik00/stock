"""drop paper_trade table

Revision ID: 7908730db339
Revises: e36f368f3c93
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7908730db339'
down_revision: Union[str, None] = 'e36f368f3c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 대시보드 "가상 매매 기록"(PaperTradeModal) 기능 제거에 따른 정리 —
    # AutoTradePage.jsx(실계좌 자동매매 전용 탭)가 생기면서 연습용 가상 장부의
    # 용도가 없어졌다는 사용자 판단(2026-08-18). 삭제 시점 실측: 0행 — 데이터
    # 손실 없음.
    op.drop_index('ix_paper_trade_status', table_name='paper_trade')
    op.drop_index('ix_paper_trade_code', table_name='paper_trade')
    op.drop_table('paper_trade')


def downgrade() -> None:
    op.create_table(
        'paper_trade',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('side', sa.String(length=10), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('entry_qty', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('entry_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exit_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('exit_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_paper_trade_code', 'paper_trade', ['code'])
    op.create_index('ix_paper_trade_status', 'paper_trade', ['status'])
