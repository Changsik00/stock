"""0167A0(자동매매 대상) 1분봉 이력 일별 적재 — PLAN.md §5.75.

**목적**: §5.74에서 자동매매 전략(§5.54)을 백테스트해보니 실제 라이브가
쓰는 1분봉 이력이 DB에 저장된 적이 없어(`routers/stocks.py` §5.1 원칙 —
온디맨드 조회만, 미저장) 일봉으로 근사할 수밖에 없었고, 108일 표본으로는
골든크로스(3일)와 거래량스파이크(29일)가 단 한 번도 겹치지 않아 거래
0건이었다. 라이브와 동일한 1분봉으로 진짜 백테스트를 하려면 이력이
쌓여야 한다 — 이 잡이 오늘부터 매일 그날의 1분봉을 쌓는다. **지금 당장
백테스트가 가능해지는 건 아니고, 시간이 지나 표본이 쌓여야 의미가
생긴다**(§5.74 계획 문서의 "1분봉 이력 신규 수집" 후속 결정).

**중복 구현 금지**: 키움 호출/파싱/캐싱은 전부 `routers/stocks.py::
_warm_stock_intraday`(ka10080)를 그대로 재사용한다 — 이 잡은 그 함수가
이미 파싱해 주는 `bars`를 새 테이블(`stock_intraday_bar`)에 upsert만
한다. 새 TR 호출 로직을 두지 않는다.

**대상은 0167A0 하나만**(`quant/auto_trade_rules.TARGET_CODE`, 하드코딩
안 함) — 백테스트 목적이 이 종목 하나이므로 전종목 확장은 이번 범위 밖
(저장 규모가 전혀 다른 별개 결정, §5.68에서 "저장만 되고 안 쓰이는
데이터" 낭비를 이미 겪었다).

**반드시 장 마감 이후 실행**(다른 일별 배치와 동일 전제) — ka10080은
"그 순간까지 누적된 오늘 하루치"를 반환하므로 장중에 돌면 이후 봉을
영영 놓친다. 새 cron을 두지 않고 기존 18:00 KST 본배치
(`collectors/scheduler.py::_run_all_jobs`, REGISTRY 전체 순회)에
`collectors/__init__.py::register_all()` 등록만으로 자연히 편입시킨다.

collect_fn 계약(`collectors/base.py`): session에 upsert만 수행, commit/
rollback은 run_job이 전담. 반환값은 upsert된 행 수(휴장일 등으로 오늘
봉이 없으면 0 — 에러 아님, 다른 배치 잡들과 동일한 관례).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import StockIntradayBar
from ..quant.auto_trade_rules import TARGET_CODE
from ..routers.stocks import _warm_stock_intraday
from .base import REGISTRY

logger = logging.getLogger(__name__)


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    try:
        payload = await _warm_stock_intraday(TARGET_CODE, 1)
    except Exception as e:  # noqa: BLE001 - 휴장일/키움 장애 등, 이 잡 하나만 실패 처리
        logger.warning("stock_intraday_history: %s 1분봉 조회 실패, 건너뜀: %s", TARGET_CODE, e)
        return 0

    bars = payload.get("bars") or []
    if not bars:
        return 0

    # §5.60 배치화 원칙 그대로 — 행마다 개별 execute 대신 다중값 INSERT ..
    # ON CONFLICT 하나로 upsert(routers/stocks.py::_upsert_flow_rows와 동일한
    # 관례, 실측 벤치마크로 이미 검증된 패턴).
    values = [
        {
            "code": TARGET_CODE,
            "date": dt.datetime.strptime(b["date"], "%Y%m%d").date(),
            "time": b["time"],
            "open": b.get("open"),
            "high": b.get("high"),
            "low": b.get("low"),
            "close": b.get("close"),
            "volume": b.get("volume"),
        }
        for b in bars
        if b.get("date") and b.get("time")
    ]
    if not values:
        return 0

    stmt = pg_insert(StockIntradayBar).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[StockIntradayBar.code, StockIntradayBar.date, StockIntradayBar.time],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )
    await session.execute(stmt)

    logger.info(
        "stock_intraday_history: %s %d개 1분봉 upsert (%s)",
        TARGET_CODE,
        len(values),
        target_date.isoformat(),
    )
    return len(values)


REGISTRY["stock_intraday_history"] = collect
