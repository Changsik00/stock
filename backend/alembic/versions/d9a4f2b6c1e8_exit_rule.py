"""manual exit rules: auto_trade_state 컬럼 + exit_preset (Phase 5.118)

Revision ID: d9a4f2b6c1e8
Revises: c8e5a1d7f3b2
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd9a4f2b6c1e8'
down_revision: Union[str, None] = 'c8e5a1d7f3b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 5.118 — 수동 매수 시 사용자가 고른 청산 조건. NULL/'auto' = 엔진 고정 규칙.
    # 두 컬럼 모두 nullable이라 기존 행·기존 엔진 코드에 영향이 없다.
    op.add_column('auto_trade_state', sa.Column('entry_source', sa.String(length=10), nullable=True))
    op.add_column('auto_trade_state', sa.Column('manual_exit_rules', sa.Text(), nullable=True))
    # 자주 쓰는 조건 조합 프리셋.
    op.create_table(
        'exit_preset',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # 추천 조합 3개(Phase 5.118 조건 가이드와 동일). 'activate 없는 트레일 + 익절' 같은 모순 조합은 두지 않는다.
    seeds = [
        ('① 고정 익절형: +1% 익절 / -1% 손절',
         '[{"kind":"take_profit_pct","value":1},{"kind":"stop_loss_pct","value":1}]'),
        ('② 추세 추종형: +1% 도달 후 고점 -0.5% / -1% 손절',
         '[{"kind":"stop_loss_pct","value":1},{"kind":"trail_pct","value":0.5,"activate_pct":1}]'),
        ('③ 추세+상한: ② + +3% 익절',
         '[{"kind":"stop_loss_pct","value":1},{"kind":"trail_pct","value":0.5,"activate_pct":1},'
         '{"kind":"take_profit_pct","value":3}]'),
    ]
    for n, r in seeds:
        op.execute(
            sa.text("INSERT INTO exit_preset (name, rules) VALUES (:n, CAST(:r AS jsonb))").bindparams(n=n, r=r)
        )


def downgrade() -> None:
    op.drop_table('exit_preset')
    op.drop_column('auto_trade_state', 'manual_exit_rules')
    op.drop_column('auto_trade_state', 'entry_source')
