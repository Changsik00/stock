"""accumulation_pick table

Revision ID: 04332be31aba
Revises: d9fcb5a87435
Create Date: 2026-08-07 11:16:15.584583

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04332be31aba'
down_revision: Union[str, None] = 'd9fcb5a87435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md 신규 — 개인 매도/외국인·기관 전환 매집 관찰 스크리너
    # (quant/screener.py::evaluate_accumulation_pattern, collectors/
    # accumulation_screener.py). 아래 create_table만 이 리비전이 의도한 변경이다
    # — autogenerate가 함께 잡아낸 4개 op.drop_index(auto_trade_log/
    # investor_warning_event/paper_trade)는 이 작업과 무관한 기존 모델↔DB 드리프트라
    # (모델에 Index() 선언이 없는데 DB에는 예전 인덱스가 남아있음) 의도적으로 제외했다
    # — 이번 변경 범위 밖이라 여기서 손대지 않는다.
    op.create_table('accumulation_pick',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('market', sa.String(length=10), nullable=True),
    sa.Column('individual_net_10d', sa.Numeric(precision=18, scale=0), nullable=True),
    sa.Column('foreign_inst_net_recent5d', sa.Numeric(precision=18, scale=0), nullable=True),
    sa.Column('foreign_inst_net_prior5d', sa.Numeric(precision=18, scale=0), nullable=True),
    sa.Column('price_return_10d_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('max_abs_daily_return_10d_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('date', 'code')
    )


def downgrade() -> None:
    op.drop_table('accumulation_pick')
