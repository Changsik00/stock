"""stock_flow: drop unused investor rows

Revision ID: e36f368f3c93
Revises: 04332be31aba
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e36f368f3c93'
down_revision: Union[str, None] = '04332be31aba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 스키마 변경 없음 — 데이터 정리만. stock_flow는 ka10059가 주는 13종
    # 투자자 분류를 전부 저장해왔지만(2026-08-17 리소스 점검), 실제로 읽히는
    # 건 개인/외국인/기관계 3종뿐이다(백엔드: scalp.py/stocks.py의
    # flow-percentile/accumulation_screener.py, 프런트:
    # StockDetailModal.jsx의 DEFAULT_INVESTORS — 나머지 10종을 노출하는
    # 코드는 어디에도 없음, routers/stocks.py의 STOCK_FLOW_STORED_INVESTORS
    # 주석 참고). 실측: 삭제 전 13종 x 403,941행(테이블 1065MB) 중 10종
    # 970MB가 한 번도 읽힌 적 없던 데이터.
    op.execute(
        "DELETE FROM stock_flow WHERE investor NOT IN ('개인', '외국인', '기관계')"
    )


def downgrade() -> None:
    # 삭제된 원본 데이터는 복구 불가 — ka10059 자체가 최근 90일 이전 조회를
    # 지원하지 않는다(routers/stocks.py의 FLOW_BACKFILL_DAYS=90). 애초에
    # 한 번도 읽힌 적 없는 데이터라 되돌릴 필요가 없다는 판단으로 삭제했다
    # (PLAN.md Phase 5.68 참고).
    pass
