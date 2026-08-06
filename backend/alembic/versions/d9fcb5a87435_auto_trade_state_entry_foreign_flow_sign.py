"""auto_trade_state entry_foreign_flow_sign

Revision ID: d9fcb5a87435
Revises: d4afe41f2108
Create Date: 2026-08-06 09:23:22.805882

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9fcb5a87435'
down_revision: Union[str, None] = 'd4afe41f2108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PLAN.md §5.55-4(2026-08-06, 실제 손실 사고 이후 안전 규칙) — 진입 시점
    # 외인 현물 누적 순매수 부호("positive"|"negative"|NULL)를 기록해 두고,
    # 이후 반전되면 조기 청산하는 데 쓴다. 기존 행(싱글턴 id=1)은 NULL로
    # 채워진다 — 다음 진입 때부터 값이 채워지므로 백필은 필요 없다.
    op.add_column(
        "auto_trade_state",
        sa.Column("entry_foreign_flow_sign", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auto_trade_state", "entry_foreign_flow_sign")
