"""value_rank / stock_ohlcv 수집 시각 컬럼 collected_at (Phase 5.120 C4)

Revision ID: a7c3e9d15b42
Revises: d9a4f2b6c1e8
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e9d15b42'
down_revision: Union[str, None] = 'd9a4f2b6c1e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('value_rank', 'stock_ohlcv')


def upgrade() -> None:
    # 과거 행은 NULL(=수집 시각 모름)로 남겨야 하므로 컬럼을 default 없이 먼저 추가한 뒤
    # server_default만 따로 건다(ADD COLUMN ... DEFAULT now()는 기존 행에 마이그레이션 시각을 채운다).
    for t in _TABLES:
        op.add_column(t, sa.Column('collected_at', sa.DateTime(timezone=True), nullable=True))
        op.alter_column(t, 'collected_at', server_default=sa.text('now()'))


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, 'collected_at')
