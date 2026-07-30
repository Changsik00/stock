"""스켈핑 후보 스크리너 적중률 사후 검증 집계 (PLAN.md §5.40).

**배경**: §5.37 경쟁 벤치마크(Quantus/GenPort 등)에서 전용 퀀트 스크리닝
툴들은 자기 스크리닝 기준의 과거 적중률(백테스트 성과)을 사용자에게 보여준다는
사실을 확인했다 — 반면 이 프로젝트의 "스켈핑 후보" 스크리너
(``quant/screener.py``)는 지금까지 실제로 얼마나 맞았는지를 노출한 적이 없다.
`scalp_pick`(§5.7, ``collectors/scalp_tracker.py``)이 이미 매일 처음 등장한
상위 후보의 진입 스코어/순위/등락률과 이후 5/15/30/60분·당일 마감(EOD)
change_rate를 기록해 두고 있으므로, 이 모듈은 **새 데이터를 만들지 않고**
그 축적된 관찰 기록을 집계만 한다 — 새 예측이 아니라 기존 스코어링의 사후
검증 투명성을 높이는 작업이다(§5.40 상단 설계 그대로).

**세션 기반 설계(pure function 아님) — 판단 근거**: 이 모듈은 자체적으로
``scalp_pick`` 전체를 조회해야 하는데(호출부인 라우터가 이미 그 행들을
들고 있지 않다 — track-record 엔드포인트와 달리 이 집계는 새로 조회해야
함), `quant/regime_backtest.py`/`quant/sector_rotation.py`가 각각
``index_ohlcv``/``market_flow``·``sector_flow``를 세션으로 직접 읽는 것과
동일한 상황이다. 반대로 `quant/volume_surge.py`/`quant/risk_alert.py`가
순수 함수인 이유는 호출부(라우터)가 이미 다른 목적으로 캐시 페이로드를 들고
있어 그걸 그대로 넘겨주면 되기 때문인데, 여기는 해당하지 않는다. 그래서
세션을 받아 직접 쿼리하는 방식을 택한다.

**호라이즌 컬럼의 NULL 의미(models.ScalpPick 클래스 docstring 그대로)**:
NULL은 "아직 그 시각이 안 됐거나 아직 못 채움"이지 "변동률 0%"가 아니다.
표본 크기(``n``)는 반드시 해당 호라이즌 컬럼이 NULL이 아닌 행만 센다 — NULL을
0으로 취급하거나 표본에서 빠뜨리지 않고 그냥 세지 않는 것(단순 제외)이 유일하게
정직한 처리다.

**평균 vs 중앙값 — 둘 다 반환**: 표본이 작고(현재 수백 건, 거래일 기준 열흘
안팎) 최근 기간이 극심한 하락장이라 개별 종목의 극단치(상한가/하한가 근접
등락)가 평균을 쉽게 왜곡할 수 있다. 평균만 보여주면 "평균 +X%"라는 숫자가
실제로는 소수의 극단치에 끌려간 결과일 수 있는데 이를 감출 위험이 있어,
평균과 중앙값을 함께 반환해 호출자(프런트)가 둘의 괴리를 통해 왜곡 여부를
스스로 판단할 수 있게 한다.

**표본수·기간을 집계 결과 자체에 포함 — docstring에만 두지 않는다**:
`total_picks`/`distinct_days`/`date_from`/`date_to`를 최상위에 반환한다 —
호출자(API 응답, 프런트 문구)가 퍼센트 옆에 표본수·기간을 항상 함께 노출해야
한다는 하우스 원칙(PLAN.md §5.40, §5.36/§5.33/§5.15 등과 동일 계열의
"관찰 사실만 서술, 검증됨이라 말하지 않는다" 원칙)을 지키려면 이 정보가
집계 함수의 반환값 자체에 있어야 나중에 문서·주석에만 있다가 프런트까지
전달되지 않는 사고를 막을 수 있다.

**score/rank 구간 분해(top-3 vs 4-10위)는 선택적 부가 정보**: 진입 순위가
스코어 내림차순이므로(``entry_rank`` = 1이 최상위) "더 높은 순위(스코어)가
실제로 더 잘 맞는지"를 보는 것이 undifferentiated 전체 평균보다 스코어
자체의 유효성을 더 직접적으로 검증한다 — 데이터가 이미 있어 추가 비용도
거의 없다. 다만 표본을 둘로 쪼개면 당연히 각 구간 표본이 전체보다 작아지므로
(top-3는 하루 최대 3건, 4-10위는 최대 7건) 반환값에 ``bucket`` 라벨과 별도
``n``을 항상 동반해 "전체보다 더 작은 표본"임이 항상 드러나게 한다. 버킷별
중앙값은 표본이 더 작아 신뢰도가 더 낮아지므로 생략하고 win_rate/avg/n만
반환한다(과설계 방지 — 지금 표본 규모에서 버킷별 중앙값까지는 노이즈에
가깝다는 판단).

**절대 "검증됨"이라고 말하지 않는다**: 이 모듈 자체는 숫자만 계산해 반환할
뿐 "이 스크리너는 유효하다/무효하다"는 판정을 내리지 않는다(§5.15/§5.23/
§5.33과 동일한 house rule — 판정은 호출자·최종적으로는 사람의 몫). 호출자
(routers/scalp.py, 프런트)도 이 숫자를 "지금까지 관찰된 결과"로만 표현해야
하며 "승률 검증됨" 같은 확정적 문구를 쓰면 안 된다(PLAN.md §5.40 명시 요구
— 표본이 9거래일 안팎이고 그 기간이 서킷브레이커 수준의 이례적 하락장이라
평상시를 대표하지 않는다).
"""

from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ScalpPick

# (호라이즌 라벨, ScalpPick 컬럼 이름) — collectors/scalp_tracker.py의
# HORIZONS_MINUTES + change_rate_eod를 합친 전체 호라이즌 집합.
HORIZONS: tuple[tuple[str, str], ...] = (
    ("5m", "change_rate_5m"),
    ("15m", "change_rate_15m"),
    ("30m", "change_rate_30m"),
    ("60m", "change_rate_60m"),
    ("eod", "change_rate_eod"),
)

# entry_rank(1이 최상위) 구간 — "상위 스코어가 실제로 더 잘 맞는지" 관찰용.
RANK_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("top3", 1, 3),
    ("rank4_10", 4, 10),
)


def _decimal_to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _horizon_stat(values: list[float]) -> dict[str, Any]:
    """단일 호라이즌의 값 리스트(이미 NULL 제외됨)에서 표본수/승률/평균/중앙값을
    계산한다. 표본이 비어 있으면 승률/평균/중앙값 전부 None(억지로 0 등으로
    채우지 않는다 — "표본 없음"과 "0%"는 다른 의미)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_change_rate": None, "median_change_rate": None}
    wins = sum(1 for v in values if v > 0)
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "avg_change_rate": round(statistics.mean(values), 3),
        "median_change_rate": round(statistics.median(values), 3),
    }


def _bucket_stat(values: list[float]) -> dict[str, Any]:
    """버킷(top3/rank4_10)별 축약 통계 — 표본이 headline보다 작아 중앙값은
    생략한다(모듈 docstring 참고)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_change_rate": None}
    wins = sum(1 for v in values if v > 0)
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "avg_change_rate": round(statistics.mean(values), 3),
    }


def _empty_result() -> dict[str, Any]:
    return {
        "total_picks": 0,
        "distinct_days": 0,
        "date_from": None,
        "date_to": None,
        "horizons": {label: _horizon_stat([]) for label, _column in HORIZONS},
        "rank_buckets": {
            bucket: {label: _bucket_stat([]) for label, _column in HORIZONS} for bucket, _lo, _hi in RANK_BUCKETS
        },
    }


def aggregate_scalp_hitrate(rows: list[Any]) -> dict[str, Any]:
    """이미 조회된 ``ScalpPick``(또는 같은 속성을 가진 임의 객체 — 단위테스트가
    실 DB 없이 synthetic row로 검증할 수 있도록 순수 함수로 분리했다) 리스트를
    집계한다. ``compute_scalp_hitrate``(세션 기반 실사용 진입점)의 실제 로직
    전부가 여기 있다 — 세션 쪽은 "전체를 읽어서 이 함수에 넘긴다"는 얇은
    래퍼일 뿐이다.

    **왜 분리했는가**: 이 집계는 ``scalp_pick`` 테이블 전체를 (다른 §5.15/
    §5.33 백테스트 모듈들과 달리) market/code 같은 격리 가능한 키로 필터링하지
    않고 그대로 읽는다 — 스크리너 전체의 적중률이 질문이지 특정 종목/시장
    한정 질문이 아니기 때문이다. 그래서 실 dev Postgres에 테스트 행을 심어
    격리하는 기존 하우스 패턴(먼 미래 날짜, 가짜 market 문자열 등)을 그대로
    쓰면 실제 프로덕션 ``scalp_pick`` 데이터가 항상 함께 집계되어 버려 assert가
    불가능하다. 순수 함수로 쪼개면 synthetic 객체만으로 완전히 격리된 단위테스트가
    가능하다.
    """
    if not rows:
        return _empty_result()

    dates = [row.date for row in rows]

    horizons: dict[str, Any] = {}
    for label, column in HORIZONS:
        values = [_decimal_to_float(getattr(row, column)) for row in rows]
        values = [v for v in values if v is not None]
        horizons[label] = _horizon_stat(values)

    rank_buckets: dict[str, Any] = {}
    for bucket, lo, hi in RANK_BUCKETS:
        bucket_rows = [row for row in rows if row.entry_rank is not None and lo <= row.entry_rank <= hi]
        bucket_horizons: dict[str, Any] = {}
        for label, column in HORIZONS:
            values = [_decimal_to_float(getattr(row, column)) for row in bucket_rows]
            values = [v for v in values if v is not None]
            bucket_horizons[label] = _bucket_stat(values)
        rank_buckets[bucket] = bucket_horizons

    return {
        "total_picks": len(rows),
        "distinct_days": len(set(dates)),
        "date_from": min(dates).isoformat(),
        "date_to": max(dates).isoformat(),
        "horizons": horizons,
        "rank_buckets": rank_buckets,
    }


async def compute_scalp_hitrate(session: AsyncSession) -> dict[str, Any]:
    """``scalp_pick`` 전체 행을 읽어 ``aggregate_scalp_hitrate``로 집계한다
    (PLAN.md §5.40-1). 새 외부 호출 없음(DB 읽기 전용). 행이 하나도 없으면
    (§5.40 "empty-table graceful handling") 크래시하지 않고 전부 0/None인
    정직한 "표본 없음" 결과를 반환한다(``aggregate_scalp_hitrate`` 자체가
    빈 리스트를 그렇게 처리한다)."""
    rows = (await session.execute(select(ScalpPick))).scalars().all()
    return aggregate_scalp_hitrate(rows)
