"""장중 수급 스냅샷 DB 영속화 — PLAN.md §5.14 (2026-07-22, §5.4-2/5.4-3/5.10/5.13의
계승).

**배경**: 이 모듈은 원래(§5.4-2) 순수 인메모리 버퍼였다 — 재배포(``--reload``)마다
그날 적립분이 통째로 사라지는 게 §5.6 사고 원인이었고, 과거 날짜 조회도 원천적으로
불가능했다. 사용자 지적("정보를 흘려 보내는게 문제야.. 이전 정보는 볼 수 있어야
하잖아")으로 §5.14에서 ``intraday_sample`` 테이블(models.py)에 영속화하도록 전면
재작성했다. 보관 정책(사용자 확인): **최근 7일은 60초 원본 그대로, 8일 전부터는
15분 단위로 압축**(다운샘플링 배치는 collectors/intraday_compaction.py).

**여전히 유효한 설계**: 이미 `collectors/live_refresh.py`의 60초/7분 잡이
`_warm_flow_live`/`_warm_futures_flow_live`/`_warm_breadth_live`를 선제적으로
호출해 캐시를 채우고 있다 — 이 모듈은 그 warm 함수들이 **이미 fetch를 마치고
반환한 값**을 받아서(추가 HTTP/키움/네이버 호출 없음) DB에 INSERT만 한다. 새 외부
API 호출은 여전히 전혀 없다.

**series_key 8종 고정값**(PLAN.md §5.14): 투자자별 수급 6개(``flow_kospi_개인``/
``flow_kospi_외국인``/``flow_kospi_기관계``/``flow_kosdaq_개인``/
``flow_kosdaq_외국인``/``flow_kosdaq_기관계``, §5.10 — 코스피/코스닥 분리) + 외인선물
1개(``futures_외국인``) + 등락비율 1개(``breadth_ratio``, §5.13). "외인 양손"의
현물(spot) 시리즈는 별도 저장하지 않는다 — 조회 시 ``flow_kospi_외국인``+
``flow_kosdaq_외국인``을 시간(정확히 일치하는 timestamp) 매칭으로 합산해서
계산한다(같은 ``record_flow_snapshot`` 호출 안에서 두 시장 행에 동일한
``dt.datetime.now(KST)``를 쓰므로 timestamp가 정확히 일치한다).

**즉시 commit**: 세 record_* 함수 모두 INSERT 직후 자체적으로 commit한다
(collectors/base.py의 run_job 트랜잭션 계약과 다르다 — 이 함수들은 run_job이
호출하는 배치 collect_fn이 아니라 live_refresh 스케줄러가 매 틱마다 직접 부르는
소단위 쓰기라, 즉시 커밋이 더 안전하다). 같은 (series_key, time)이 이미 있으면
``ON CONFLICT DO NOTHING``으로 조용히 무시한다 — 60초 잡이 정확히 60초 간격으로
도는 게 아니라 살짝 어긋날 수 있어 방어적으로 둔다.

**market_closed 스킵**: 세 record_* 함수 모두 ``payload["market_closed"]``가
true면 아무 것도 하지 않는다 — 이 경우 warm 함수 자체가 라이브 호출을 생략하고
DB 확정치/직전 캐시를 재사용 중이라, 그 값을 "장중 새 스냅샷"인 것처럼 적립하면
잘못된 시계열이 된다(§5.4-2 원칙 그대로 유지).

**조회부(get_*_series)**: 이제 ``session``과 ``days``(기본 1 = 오늘만, 최대 30)를
받아 DB를 쿼리한다. 반환 모양은 예전 메모리 버퍼 버전과 최대한 동일하게 유지해
프런트 변경을 최소화했다 — 다만 ``days>1``일 때는 여러 날짜가 섞이므로 각 포인트의
``time``을 ``"HH:MM"``이 아니라 ``"MM/DD HH:MM"``으로 포맷한다(날짜 구분이 필요해서,
프런트는 이 포맷도 그대로 문자열로 렌더한다)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..market_hours import KST, is_market_closed
from ..models import IntradaySample

# flow/live에서 다루는 투자자 3종 x 시장 2종(PLAN.md §5.10 — 코스피/코스닥 분리).
_FLOW_INVESTORS = ("개인", "외국인", "기관계")
_FLOW_MARKETS = ("kospi", "kosdaq")
_FLOW_SERIES_KEYS = [f"flow_{market}_{investor}" for market in _FLOW_MARKETS for investor in _FLOW_INVESTORS]

FUTURES_SERIES_KEY = "futures_외국인"
BREADTH_SERIES_KEY = "breadth_ratio"

DAYS_MIN = 1
DAYS_MAX = 30


def _now_kst() -> dt.datetime:
    """단일 시간 seam — 테스트가 이 함수 하나만 monkeypatch하면 record_*(적립
    timestamp)와 get_*(market_closed 재계산·오늘 날짜·조회 커트오프) 양쪽의 "지금"을
    모두 결정론적으로 통제할 수 있다(house 관례 — 옛 버전의 `_today_kst`/
    `_now_hhmm_kst` monkeypatch 패턴 계승, tests/test_market_hours.py의 "explicit
    datetime 구성" 관례와는 다르게 이 모듈은 여러 함수가 "지금"을 반복 참조해서
    seam을 하나로 통일해 뒀다)."""
    return dt.datetime.now(KST)


def _today_kst() -> dt.date:
    return _now_kst().date()


def _clamp_days(days: int) -> int:
    return max(DAYS_MIN, min(DAYS_MAX, days))


def _cutoff(days: int) -> dt.datetime:
    """``days``일 조회 창의 시작 시각(KST 자정) — days=1이면 오늘 00:00부터,
    days=7이면 6일 전 00:00부터(오늘 포함 최근 7일)."""
    days = _clamp_days(days)
    start_date = _today_kst() - dt.timedelta(days=days - 1)
    return dt.datetime.combine(start_date, dt.time.min, tzinfo=KST)


def _format_time(value: dt.datetime, days: int) -> str:
    local = value.astimezone(KST)
    if days > 1:
        return local.strftime("%m/%d %H:%M")
    return local.strftime("%H:%M")


async def _insert_points(session: AsyncSession, rows: list[dict]) -> None:
    """공통 INSERT + 즉시 commit — 모듈 docstring "즉시 commit" 절 참고. ``rows``가
    비어 있으면(예: payload에 유효한 값이 하나도 없음) 아무 것도 하지 않는다."""
    if not rows:
        return
    stmt = pg_insert(IntradaySample).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=[IntradaySample.series_key, IntradaySample.time])
    await session.execute(stmt)
    await session.commit()


async def record_flow_snapshot(session: AsyncSession, payload: dict) -> None:
    """`routers.markets._warm_flow_live`가 이미 반환한 값을 받아 kospi/kosdaq
    각 시장의 개인/외국인/기관계 3개 series_key에 순매수대금(net_value, 백만원)을
    같은 timestamp로 INSERT한다. 새 외부 호출은 전혀 없다.

    한 시장이 None이거나 해당 투자자 키가 없으면 그 시장의 그 투자자만 건너뛴다
    (다른 시장/다른 투자자는 영향받지 않는다). 두 시장이 항상 같은 payload에서
    함께 오므로, 한쪽만 빠지는 상황이라도 time 컬럼은 두 시장 다 동일한
    ``dt.datetime.now(KST)``로 찍혀 `get_foreign_position_series`의 시간 매칭
    전제와 일관된다."""
    if payload.get("market_closed"):
        return

    now = _now_kst()
    rows: list[dict] = []
    for market_key in _FLOW_MARKETS:
        market_data = payload.get(market_key)
        if not market_data:
            continue
        investors = market_data.get("investors") or {}
        for investor in _FLOW_INVESTORS:
            entry = investors.get(investor)
            if not entry:
                continue
            net_value = entry.get("net_value")
            if net_value is None:
                continue
            rows.append(
                {
                    "series_key": f"flow_{market_key}_{investor}",
                    "time": now,
                    "value": net_value,
                    "resolution_seconds": 0,
                }
            )

    await _insert_points(session, rows)


async def record_futures_flow_snapshot(session: AsyncSession, payload: dict) -> None:
    """`routers.markets._warm_futures_flow_live`가 이미 반환한 값을 받아
    ``futures_외국인`` series_key에 외국인 투자자의 순매수대금(net_value, 백만원)을
    INSERT한다. `record_flow_snapshot`과 동일하게 새 외부 호출은 없고,
    ``market_closed``면 스킵한다."""
    if payload.get("market_closed"):
        return

    investors = payload.get("investors") or {}
    entry = investors.get("외국인") or {}
    net_value = entry.get("net_value")
    if net_value is None:
        return

    now = _now_kst()
    await _insert_points(
        session,
        [{"series_key": FUTURES_SERIES_KEY, "time": now, "value": net_value, "resolution_seconds": 0}],
    )


async def record_breadth_snapshot(session: AsyncSession, payload: dict) -> None:
    """`routers.markets._warm_breadth_live`가 이미 반환한 값을 받아
    ``breadth_ratio`` series_key(코스피+코스닥 합산, 시장 구분 없는 단일 시리즈)에
    상승비율(%)을 INSERT한다. PLAN.md §5.13 지표 정의: ``ratio = total_adv /
    (total_adv + total_dec) * 100`` — 보합(flat)은 분모에서 제외한다.

    `record_flow_snapshot`과 동일하게 ``market_closed``면 스킵한다. kospi/kosdaq
    중 한쪽이 None이면 있는 쪽만으로 계산한다. 둘 다 없거나 adv+dec 합이 0이면
    이번 틱은 INSERT하지 않는다(0으로 나누기 방지)."""
    if payload.get("market_closed"):
        return

    total_adv = 0
    total_dec = 0
    for market_key in _FLOW_MARKETS:
        market_data = payload.get(market_key)
        if not market_data:
            continue
        adv = market_data.get("adv")
        dec = market_data.get("dec")
        if adv is not None:
            total_adv += adv
        if dec is not None:
            total_dec += dec

    denom = total_adv + total_dec
    if denom <= 0:
        return

    ratio = total_adv / denom * 100
    now = _now_kst()
    await _insert_points(
        session,
        [{"series_key": BREADTH_SERIES_KEY, "time": now, "value": ratio, "resolution_seconds": 0}],
    )


async def get_flow_series(session: AsyncSession, days: int = 1) -> dict:
    """1D 조회 API(`GET /api/markets/flow/intraday-accumulated`)가 그대로
    반환할 payload. ``date``는 오늘 KST 날짜(과거 구간을 포함해 조회해도 "오늘"
    라벨은 흔들리지 않는다 — 프런트가 항상 오늘 날짜를 헤더로 쓸 수 있도록).
    ``market_closed``는 저장된 값이 아니라 호출 시점에 새로 계산한다.

    ``series``는 ``kospi``/``kosdaq`` 두 시장 블록 아래에 각각 투자자 3종을
    담는다(PLAN.md §5.10). ``days>1``이면 각 포인트의 ``time``이
    ``"MM/DD HH:MM"``로 포맷된다(모듈 docstring 참고)."""
    days = _clamp_days(days)
    cutoff = _cutoff(days)

    rows = (
        await session.execute(
            select(IntradaySample.series_key, IntradaySample.time, IntradaySample.value)
            .where(IntradaySample.series_key.in_(_FLOW_SERIES_KEYS), IntradaySample.time >= cutoff)
            .order_by(IntradaySample.time)
        )
    ).all()

    series: dict[str, dict[str, list[dict[str, object]]]] = {
        market: {investor: [] for investor in _FLOW_INVESTORS} for market in _FLOW_MARKETS
    }
    prefix = "flow_"
    for series_key, time, value in rows:
        market, investor = series_key[len(prefix) :].split("_", 1)
        series[market][investor].append({"time": _format_time(time, days), "value": float(value)})

    return {
        "date": _today_kst().isoformat(),
        "series": series,
        "market_closed": is_market_closed(_now_kst()),
    }


async def get_foreign_position_series(session: AsyncSession, days: int = 1) -> dict:
    """1D 조회 API(`GET /api/markets/foreign-position/intraday-accumulated`)가
    그대로 반환할 payload. ``spot``은 ``flow_kospi_외국인``/``flow_kosdaq_외국인``을
    time(정확히 일치하는 timestamp) 기준으로 매칭해 합산한 값이다(모듈 docstring
    참고 — 두 행은 항상 같은 `record_flow_snapshot` 호출에서 동일한 timestamp로
    쓰인다). 한쪽 시장에만 있는 timestamp가 있으면(예: 한쪽 fetch만 실패) 있는
    쪽 값 그대로 사용한다. ``futures``는 ``futures_외국인`` series_key."""
    days = _clamp_days(days)
    cutoff = _cutoff(days)

    rows = (
        await session.execute(
            select(IntradaySample.series_key, IntradaySample.time, IntradaySample.value)
            .where(
                IntradaySample.series_key.in_(["flow_kospi_외국인", "flow_kosdaq_외국인", FUTURES_SERIES_KEY]),
                IntradaySample.time >= cutoff,
            )
            .order_by(IntradaySample.time)
        )
    ).all()

    spot_order: list[dt.datetime] = []
    spot_totals: dict[dt.datetime, float] = {}
    futures_points: list[dict[str, object]] = []
    for series_key, time, value in rows:
        if series_key == FUTURES_SERIES_KEY:
            futures_points.append({"time": _format_time(time, days), "value": float(value)})
            continue
        if time not in spot_totals:
            spot_order.append(time)
            spot_totals[time] = 0.0
        spot_totals[time] += float(value)

    spot_points = [{"time": _format_time(t, days), "value": spot_totals[t]} for t in spot_order]

    return {
        "date": _today_kst().isoformat(),
        "spot": spot_points,
        "futures": futures_points,
        "market_closed": is_market_closed(_now_kst()),
    }


async def get_breadth_series(session: AsyncSession, days: int = 1) -> dict:
    """1D 조회 API(`GET /api/markets/breadth/intraday-accumulated`)가 그대로
    반환할 payload(PLAN.md §5.13). `get_flow_series`와 동일한 모양이지만
    ``series``가 투자자별 중첩이 아니라 바로 포인트 리스트다(단일 시리즈)."""
    days = _clamp_days(days)
    cutoff = _cutoff(days)

    rows = (
        await session.execute(
            select(IntradaySample.time, IntradaySample.value)
            .where(IntradaySample.series_key == BREADTH_SERIES_KEY, IntradaySample.time >= cutoff)
            .order_by(IntradaySample.time)
        )
    ).all()

    series = [{"time": _format_time(t, days), "value": float(v)} for t, v in rows]

    return {
        "date": _today_kst().isoformat(),
        "series": series,
        "market_closed": is_market_closed(_now_kst()),
    }
