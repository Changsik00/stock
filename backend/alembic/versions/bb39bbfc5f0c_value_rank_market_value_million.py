"""value_rank market_value_million

Revision ID: bb39bbfc5f0c
Revises: 770ef1e191e5
Create Date: 2026-07-30 09:26:58.133227

PLAN.md §5.38-1 — value_rank가 이미 소스(naver_value_rank의 marketValueRaw)에서
받아 turnover 계산에만 쓰고 버리던 시가총액/AUM(백만 원)을 컬럼으로 저장한다.
새 외부 호출 없음, nullable(과거 행은 NULL로 남는다 — 소스가 과거 날짜 재조회를
지원하지 않아 backfill 불가, collectors/value_rank.py 모듈 docstring 참고).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb39bbfc5f0c'
down_revision: Union[str, None] = '770ef1e191e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('value_rank', sa.Column('market_value_million', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('value_rank', 'market_value_million')
