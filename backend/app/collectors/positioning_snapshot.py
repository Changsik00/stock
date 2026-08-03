"""§5.50 포지셔닝 프레임(하이닉스 중심 top-down 브리핑) 사후 검증 스냅샷 기록
(PLAN.md §5.52) — 관찰 로그, 매매 신호 아님.

사용자 질의 배경: "이 로직이 유용하다는 걸 어떻게 증명할 수 있을까?" — 이 질문에
대해 (a) AI가 하루치 데이터를 보고 매매를 임의로 "감지"해 판단하지 않는다는 이
프로젝트 house rule(§5, 관찰 데이터만 제공·판단은 사용자 몫)과 (b) 표본 1개로는
애초에 아무것도 증명되지 않는다는 원칙(§5.15/§5.19/§5.34)을 그대로 지키기로 하고,
이 프로젝트가 다른 지표에 이미 적용해 온 것과 동일한 사후 검증(§5.7 ScalpPick,
§5.15 regime_backtest의 "버킷별 다음날 수익률" 방법론)을 §5.50 포지셔닝 프레임에도
적용한다. `collectors/scalp_tracker.py`와 완전히 같은 구조·같은 태도로 작성한다.

**세 가지 책임(§5.52 설계)**:

1. **오늘 스냅샷 기록(1일 1회)** — `record_snapshot`. 오늘 아직 행이 없으면
   §5.50/§5.15가 이미 만든 warm 함수들(`routers.markets._warm_regime`,
   `flow_concentration_intraday_accumulated`, `foreign_position_intraday_
   accumulated`, `hynix_relative_strength`, `_warm_nasdaq_futures_live`,
   `_warm_index_tiles_live`의 risk, `_warm_stock_intraday`)을 그대로 재호출해
   한 행을 INSERT한다. **새 외부 API 호출이 전혀 없다** — 전부 이미 존재하는
   60초/300초 캐시 warm 함수의 재사용이다. 각 필드는 서로 독립적으로 try/except로
   감싸 하나가 실패해도 나머지는 기록되게 한다(ScalpPick과 동일한 관대함).

2. **`same_day_remaining_change_rate` 채우기** — `fill_same_day`. NXT 마감 후
   (`market_hours.is_nxt_closed`) 첫 폴링에, 오늘 행이 있고 이 필드가 아직
   None이고 스냅샷 시점 가격(`hynix_price_at_snapshot`)이 있으면, 그날 최종가
   근사치(`_warm_stock_intraday`의 마지막 close)를 가져와 "스냅샷 이후 남은 하루
   동안 실제로 어떻게 움직였는지"를 계산한다 — 스냅샷에 이미 포함된
   `relative_strength_pct`(코스피 대비 초과수익률)와는 순환참조 없이 격리된
   별도 관찰치다.

3. **`next_day_change_rate` 채우기** — `fill_next_day`. 아직 채워지지 않은 과거
   행(최근 `NEXT_DAY_LOOKBACK_DAYS`일 이내로 한정 — 너무 오래된 결측을 매번
   스캔하지 않게)마다 `services.get_stock_series_from_db`에서 그 행의 날짜
   바로 다음에 저장된 확정 거래일이 이미 들어와 있으면, 그 다음 행의
   `changeRate`(이미 "전일 종가 대비" 등락률)를 그대로 옮겨 담는다.

세 단계 모두 개별 try/except로 감싸 하나가 실패해도 나머지가 죽지 않는다
(호출부 `track_positioning_snapshot`이 이 계약을 진다 — `scalp_tracker.
track_scalp_picks`와 동일한 관례).

## 스케줄링 배선

`collectors/live_refresh.py`의 60초 잡에서 `scalp_tracker.track_scalp_picks`
호출 바로 옆(같은 NXT 게이트 바깥)에서 `track_positioning_snapshot`을 호출한다.
1번(신규 기록)은 함수 내부에서 `is_nxt_closed`로 스스로 게이트하고(§5.50 warm
함수들이 이미 장마감 시 대부분 None을 주므로, 장 마감 중 새로 행을 만들어봤자
의미 있는 값이 거의 없다 — "장중에만 의미 있는 기록"이라는 ScalpPick의 원칙을
그대로 따른다), 2번(same_day 채우기)은 NXT 마감 시점에 실행돼야 하므로 오히려
게이트 **밖**에서 항상 시도해야 하고, 3번(next_day 채우기)도 새 외부 호출이
없어 마감 여부와 무관하게 항상 시도해도 비용이 없다 — 그래서 `scalp_tracker.
track_scalp_picks`와 완전히 동일하게, 이 모듈의 최상위 진입점
(`track_positioning_snapshot`)은 NXT 게이트 바깥에서 호출되고 각 단계가 내부적으로
알아서 스스로를 게이트한다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..market_hours import KST, is_nxt_closed
from ..models import MacroSeries, PositioningSnapshot
from ..services import get_stock_series_from_db
from . import intraday_snapshot

logger = logging.getLogger(__name__)

# 하이닉스 상대강도/가격 조회 대상 — §5.50 top-down 프레임이 "수급에 제일 영향받는
# 종목"으로 확정한 베이스지표(PLAN.md §5.50 상단 설계).
HYNIX_CODE = "000660"

# next_day_change_rate 채우기 대상 과거 행 범위(거래일 아닌 달력일 기준, 여유
# 있게 잡음) — 너무 오래된 결측을 매번 스캔하지 않으려는 상한(§5.52 설계).
NEXT_DAY_LOOKBACK_DAYS = 30


async def _sox_change_rate(session: AsyncSession) -> float | None:
    """macro_series(series='us_sox')의 최근 확정 종가 2개로 등락률(%)을 계산한다
    — HynixPositioningModal(프런트)이 ②구획에서 하는 것과 동일한 방식(가장 최근
    두 EOD 종가의 차이). 저장된 행이 2개 미만이면 None."""
    stmt = (
        select(MacroSeries.value)
        .where(MacroSeries.series == "us_sox")
        .order_by(MacroSeries.date.desc())
        .limit(2)
    )
    values = (await session.execute(stmt)).scalars().all()
    if len(values) < 2:
        return None
    latest, prev = float(values[0]), float(values[1])
    if not prev:
        return None
    return round((latest - prev) / prev * 100, 4)


async def record_snapshot(session: AsyncSession, now_kst: dt.datetime) -> bool:
    """오늘 행이 아직 없으면 §5.50/§5.15 warm 함수들을 재조합해 1행을 INSERT한다
    (§5.52 설계 1번). NXT 마감 중이면 아무 것도 하지 않는다 — 이 시각엔 재료가
    되는 warm 함수 대부분이 이미 None을 주므로(장 마감 게이트, 각 함수 docstring
    참고) 새로 기록해봤자 의미 있는 값이 거의 없다(ScalpPick "장중에만 의미
    있는 기록" 원칙과 동일).

    각 필드는 서로 독립적으로 try/except로 감싼다 — 소스 하나가 실패해도
    나머지 필드는 그대로 기록된다. 새로 행이 생겼으면 True."""
    today = now_kst.date()
    if is_nxt_closed(now_kst):
        return False

    existing = await session.get(PositioningSnapshot, today)
    if existing is not None:
        return False

    # 지연 임포트 — collectors/live_refresh.py와 동일한 이유(routers.markets는
    # FastAPI 라우터 모듈이라 main.py의 임포트 순서에 얽히기 쉽다). 다만 이
    # 모듈은 live_refresh.py처럼 main.py lifespan에서만 불리는 게 아니라
    # 테스트에서도 직접 임포트되므로, 함수 내부 임포트로 안전하게 지연시킨다.
    from ..routers import markets

    regime: str | None = None
    try:
        regime_payload = await markets._warm_regime(session)
        regime = regime_payload.get("regime")
    except Exception as e:  # noqa: BLE001 - 소스 하나 실패가 나머지를 막지 않도록
        logger.warning("positioning-snapshot: regime 조회 실패: %s", e)

    concentration_pct: float | None = None
    try:
        concentration_payload = await markets.flow_concentration_intraday_accumulated(
            days=1, session=session
        )
        series = concentration_payload.get("series") or []
        concentration_pct = series[-1]["value"] if series else None
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: flow-concentration 조회 실패: %s", e)

    foreign_spot_cum: float | None = None
    foreign_futures_cum: float | None = None
    try:
        foreign_payload = await intraday_snapshot.get_foreign_position_series(session, 1)
        spot_series = foreign_payload.get("spot") or []
        futures_series = foreign_payload.get("futures") or []
        foreign_spot_cum = spot_series[-1]["value"] if spot_series else None
        foreign_futures_cum = futures_series[-1]["value"] if futures_series else None
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: foreign-position 조회 실패: %s", e)

    sox_change_rate: float | None = None
    try:
        sox_change_rate = await _sox_change_rate(session)
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: SOX 등락률 계산 실패: %s", e)

    nasdaq_futures_change_pct: float | None = None
    try:
        nasdaq_payload = await markets._warm_nasdaq_futures_live()
        nasdaq_futures_change_pct = nasdaq_payload.get("latest_change_pct")
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: 나스닥선물 조회 실패: %s", e)

    hynix_price_at_snapshot: float | None = None
    try:
        intraday = await markets._warm_stock_intraday(HYNIX_CODE, 1)
        bars = intraday.get("bars") if intraday else None
        hynix_price_at_snapshot = bars[-1]["close"] if bars else None
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: 하이닉스 인트라데이 조회 실패: %s", e)

    hynix_change_rate_at_snapshot: float | None = None
    kospi_change_rate_at_snapshot: float | None = None
    relative_strength_pct: float | None = None
    try:
        rs_payload = await markets.hynix_relative_strength(session)
        hynix_change_rate_at_snapshot = rs_payload.get("hynix_change_rate")
        kospi_change_rate_at_snapshot = rs_payload.get("kospi_change_rate")
        relative_strength_pct = rs_payload.get("relative_strength_pct")
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: 하이닉스 상대강도 조회 실패: %s", e)

    risk_alert_count: int | None = None
    try:
        tiles_payload = await markets._warm_index_tiles_live(session)
        risk = tiles_payload.get("risk")
        risk_alert_count = len(risk.get("alerts") or []) if risk else 0
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: index-tiles(risk) 조회 실패: %s", e)

    session.add(
        PositioningSnapshot(
            date=today,
            snapshot_at=now_kst,
            regime=regime,
            concentration_pct=concentration_pct,
            foreign_spot_cum=foreign_spot_cum,
            foreign_futures_cum=foreign_futures_cum,
            sox_change_rate=sox_change_rate,
            nasdaq_futures_change_pct=nasdaq_futures_change_pct,
            hynix_price_at_snapshot=hynix_price_at_snapshot,
            hynix_change_rate_at_snapshot=hynix_change_rate_at_snapshot,
            kospi_change_rate_at_snapshot=kospi_change_rate_at_snapshot,
            relative_strength_pct=relative_strength_pct,
            risk_alert_count=risk_alert_count,
        )
    )
    await session.commit()
    return True


async def fill_same_day(session: AsyncSession, now_kst: dt.datetime) -> bool:
    """NXT 마감 후 오늘 행의 `same_day_remaining_change_rate`를 채운다(§5.52
    설계 2번) — "스냅샷 시점 가격" 대비 "그날 마감가 근사치"(`_warm_stock_
    intraday`의 마지막 close)의 등락률. 마감 전이거나, 오늘 행이 없거나, 이미
    채워져 있거나, 스냅샷 시점 가격 자체가 없으면(1번이 실패해 못 남겼거나) 아무
    것도 하지 않고 False."""
    if not is_nxt_closed(now_kst):
        return False

    today = now_kst.date()
    row = await session.get(PositioningSnapshot, today)
    if row is None or row.same_day_remaining_change_rate is not None:
        return False
    if row.hynix_price_at_snapshot is None:
        return False

    from ..routers import markets

    try:
        intraday = await markets._warm_stock_intraday(HYNIX_CODE, 1)
    except Exception as e:  # noqa: BLE001 - 실패해도 다음 폴링에서 재시도
        logger.warning("positioning-snapshot: same-day 채우기 위한 인트라데이 조회 실패: %s", e)
        return False

    bars = intraday.get("bars") if intraday else None
    close = bars[-1]["close"] if bars else None
    if close is None:
        return False

    snapshot_price = float(row.hynix_price_at_snapshot)
    if not snapshot_price:
        return False

    row.same_day_remaining_change_rate = round((close - snapshot_price) / snapshot_price * 100, 4)
    await session.commit()
    return True


async def fill_next_day(session: AsyncSession, now_kst: dt.datetime) -> int:
    """`next_day_change_rate`가 아직 None인 최근 `NEXT_DAY_LOOKBACK_DAYS`일
    이내 행들에 대해, 각 행의 날짜 바로 다음 확정 거래일 종가가 이미
    `stock_ohlcv`에 들어와 있으면 그 행의 "전일 대비 등락률"(``changeRate`` —
    `services.get_stock_series_from_db`가 이미 전일 종가 대비로 계산해 두는
    필드라 원하는 "다음날 수익률"과 정확히 같다)을 그대로 옮겨 담는다(§5.52
    설계 3번). 채운 행 수를 반환한다."""
    today = now_kst.date()
    cutoff = today - dt.timedelta(days=NEXT_DAY_LOOKBACK_DAYS)
    stmt = (
        select(PositioningSnapshot)
        .where(
            PositioningSnapshot.next_day_change_rate.is_(None),
            PositioningSnapshot.date >= cutoff,
        )
        .order_by(PositioningSnapshot.date)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return 0

    # 조회 창을 넉넉히 잡아(로선 대상 최고령 행 + 여유분) "다음 거래일"이 그
    # 안에 잡히지 않는 경계 사고를 피한다.
    series = await get_stock_series_from_db(session, HYNIX_CODE, days=NEXT_DAY_LOOKBACK_DAYS + 10)
    date_index = {r["date"]: i for i, r in enumerate(series)}

    filled = 0
    for row in rows:
        date_str = row.date.strftime("%Y%m%d")
        idx = date_index.get(date_str)
        if idx is None or idx + 1 >= len(series):
            continue
        row.next_day_change_rate = series[idx + 1]["changeRate"]
        filled += 1

    if filled:
        await session.commit()
    return filled


async def track_positioning_snapshot(
    session: AsyncSession, now_kst: dt.datetime | None = None
) -> dict[str, object]:
    """§5.52 전체 흐름(스냅샷 기록 + same_day 채우기 + next_day 채우기)을 한 번에
    실행한다. `collectors/live_refresh.py`의 60초 잡에서
    `scalp_tracker.track_scalp_picks` 바로 옆에서 호출된다(NXT 게이트 바깥,
    모듈 docstring "스케줄링 배선" 참고). 세 단계 모두 개별 try/except로 감싸
    하나가 실패해도 나머지를 막지 않는다(`scalp_tracker.track_scalp_picks`와
    동일한 관례).

    Returns ``{"created": bool, "same_day_filled": bool, "next_day_filled_count": int}``."""
    now_kst = now_kst or dt.datetime.now(KST)
    result: dict[str, object] = {
        "created": False,
        "same_day_filled": False,
        "next_day_filled_count": 0,
    }

    try:
        result["created"] = await record_snapshot(session, now_kst)
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: 스냅샷 기록 실패: %s", e)

    try:
        result["same_day_filled"] = await fill_same_day(session, now_kst)
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: same-day 채우기 실패: %s", e)

    try:
        result["next_day_filled_count"] = await fill_next_day(session, now_kst)
    except Exception as e:  # noqa: BLE001
        logger.warning("positioning-snapshot: next-day 채우기 실패: %s", e)

    return result
