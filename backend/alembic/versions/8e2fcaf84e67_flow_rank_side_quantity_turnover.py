"""flow_rank side quantity turnover

Revision ID: 8e2fcaf84e67
Revises: 0d899d04697c
Create Date: 2026-07-18 00:44:47.653027

PLAN.md §6 3.5-2b — flow_rank를 순매수 전용에서 순매수/순매도 겸용으로 확장한다.

- side String(4) ('buy'/'sell') — 기존 행은 전부 순매수 랭킹이었으므로 'buy'로
  backfill한 뒤 PK에 편입한다(PK가 (date, investor, rank)뿐이면 같은 rank의
  buy/sell 행이 서로 덮어써버린다).
- quantity BigInteger(천주, 항상 양수) — 순수급 규모(부가 지표).
- turnover Numeric(8,4)(%, 항상 양수) — 당일 거래대금/시가총액. 과거 스냅샷
  재현을 위해 수집 시점에 저장(§6 3.5-2b 작업 지시 "수집 시 저장" 채택 근거는
  PLAN.md 작업 로그 참고).

autogenerate가 PK 제약조건 변경을 잡아내지 못해(컬럼 추가만 감지) 이 리비전은
손으로 다시 작성했다 — PK 재생성 순서(제약 drop -> NOT NULL 적용 -> 제약 재생성)에
주의.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e2fcaf84e67'
down_revision: Union[str, None] = '0d899d04697c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 새 컬럼 추가 — side는 우선 nullable로 넣고 기존 행을 backfill한 뒤 NOT NULL로
    #    바꾼다(기존 160여 행이 이미 있어 nullable=False로 바로 추가하면 실패한다).
    op.add_column('flow_rank', sa.Column('side', sa.String(length=4), nullable=True))
    op.add_column('flow_rank', sa.Column('quantity', sa.BigInteger(), nullable=True))
    op.add_column('flow_rank', sa.Column('turnover', sa.Numeric(precision=8, scale=4), nullable=True))

    # 2) backfill: 기존 행은 전부 type=buy로만 수집됐던 순매수 랭킹이다.
    op.execute("UPDATE flow_rank SET side = 'buy' WHERE side IS NULL")

    op.alter_column('flow_rank', 'side', nullable=False)

    # 3) PK를 (date, investor, rank) -> (date, investor, side, rank)로 재생성한다.
    op.drop_constraint('flow_rank_pkey', 'flow_rank', type_='primary')
    op.create_primary_key(
        'flow_rank_pkey', 'flow_rank', ['date', 'investor', 'side', 'rank']
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_constraint('flow_rank_pkey', 'flow_rank', type_='primary')
    op.create_primary_key('flow_rank_pkey', 'flow_rank', ['date', 'investor', 'rank'])
    op.drop_column('flow_rank', 'turnover')
    op.drop_column('flow_rank', 'quantity')
    op.drop_column('flow_rank', 'side')
    # ### end Alembic commands ###
