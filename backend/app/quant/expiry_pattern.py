"""K200 선물 만기 수렴 패턴 관찰 (PLAN.md §5.42, 2026-07-30 사용자 요청).

**배경**: 대시보드 "외인 양손 · 현선물" 섹션의 "다음 만기" 타일이 날짜+D-day+
네 마녀의 날 배지만 보여줄 뿐 분석적 깊이가 없다는 사용자 지적에서 시작했다.
`index_ohlcv`에 K200 선물(``k200_futures``)·KOSPI200 현물지수(``kospi200``)
둘 다 2023-07부터 3년치가 이미 쌓여 있어(각 735~737행, 약 36회의 과거 월별
만기 사이클을 커버) 새 데이터 수집 없이 "만기가 다가올수록 베이시스(선물종가
- 현물종가)가 역사적으로 어떻게 움직였는지"를 관찰할 수 있다.

**핵심 아이디어 — 거래일 기준 D-day 정렬**: 각 과거 만기 사이클마다 만기일을
D-0으로 놓고, 그 이전 거래일들을 D-1, D-2, ... 순으로 거슬러 올라간다(달력일이
아니라 **실제 가격 데이터에 존재하는 거래일** 기준 — 공휴일/주말 때문에
사이클마다 달력 간격이 들쭉날쭉해도 "만기까지 남은 거래일수"는 사이클 간
비교가 가능하다). 그런 다음 동일 D-day끼리 모든 사이클의 베이시스 값을 모아
평균/중앙값/표본수를 계산한다.

**이건 과거 관찰 통계이지 예측이 아니다 — §5.15/§5.23/§5.33/§5.40과 동일한
house rule**: "과거 N회 사이클에서 D-day별 베이시스가 이랬다"는 사실 서술일
뿐, "이번 사이클도 같은 패턴으로 수렴할 것"이라는 보장이 전혀 없다. 이 모듈은
"수렴 신호가 유효하다/무효하다" 같은 판정을 내리지 않고 숫자만 반환한다
(§5.15에서 백워데이션 배지가 그럴듯해 보였지만 3년 백테스트로 예측력이 없다고
실증된 전례를 반복하지 않기 위함). 표본 수(사이클 개수, ``cycle_count``)는
모든 응답에 최상위로 동반된다 — §5.40과 동일한 "표본수는 문서·주석에만 두지
않고 반환값 자체에 넣는다"는 비타협 원칙. 트레이딩 시그널로 검증된 적이 없다.

**순수 함수 + 얇은 세션 래퍼로 분리 — 판단 근거
(quant/scalp_hitrate.py "왜 분리했는가"와 동일한 이유)**: 이 집계는
``index_ohlcv``를 격리 가능한 키로 필터링하지 않고 두 시장(``k200_futures``/
``kospi200``) 전체를 그대로 읽어야 한다. ``quant/sector_rotation.py``가
가짜 market 문자열(``zzz_test_*``)로 테스트 행만 조회되게 격리하는 방식이
있어 보이지만, 여기서는 라우터(``routers/basis.py``)·수집기 전체가 이 두
시장 이름을 상수로 하드코딩해 재사용하고 있어 함수 시그니처에 market 파라미터를
새로 얹으면 실사용 경로와 테스트 경로가 갈라져 버린다(가짜 market으로 넣은
행이 실제 만기일 계산·거래일 정렬 로직과 자연스럽게 맞물리게 하려면 결국
전체를 다시 설계해야 함). ``index_ohlcv``는 공유 프로덕션 데이터라 "먼 미래
날짜" 같은 자연스러운 격리 키도 없다(선물/현물 종가는 실제 거래일에만
존재하므로 미래 날짜를 심어도 의미 있는 사이클을 못 만든다). 그래서
scalp_hitrate.py와 동일하게 순수 집계 함수(``aggregate_expiry_pattern``)를
분리해 synthetic ``date -> close`` dict만으로 완전히 격리된 단위테스트를
작성하고, 세션 래퍼(``compute_expiry_pattern``)는 "전체를 읽어서 이 함수에
넘긴다"는 얇은 역할만 한다.

**만기일이 거래일이 아닌 경우 — 가장 가까운 이전 거래일을 D-0으로 사용**:
``_second_thursday``가 계산하는 만기 후보일이 공휴일 등으로 실제 거래일이
아닐 수 있다(``derivatives.py`` 모듈 docstring이 이미 밝힌 알려진 한계 —
공휴일 캘린더가 없어 순연을 반영하지 못함). 이 모듈은 만기 후보일 이하에서
가장 가까운 거래일을 D-0으로 삼는다. 다만 그 간극이
``MAX_EXPIRY_ANCHOR_GAP_DAYS``(5 달력일)를 넘으면 데이터 갭(수집 공백)으로
보고 그 사이클 자체를 건너뛴다 — 몇 주 전의 엉뚱한 거래일을 억지로 D-0에
붙이면 D-day 정렬 자체가 무의미해지기 때문이다.

**MAX_LOOKBACK_DAYS = 14 (D-0..D-14)**: 만기 한 달 전(약 20영업일)까지 보고
싶은 유혹이 있지만, 베이시스 수렴은 보통 만기 임박 구간(2~3주 전부터)에서
관찰 가치가 크고, 너무 긴 lookback을 요구하면 3년 데이터 앞부분의 초기
사이클들이 전부 탈락한다. 3주(약 15거래일)로 설정해 "만기 3주 전부터
만기 당일까지"라는 직관적인 창을 준다.

**MIN_CYCLES = 6**: ``quant/sector_rotation.py::MIN_BASELINE_DAYS``(5)과
같은 계열의 "표본이 이보다 적으면 평균이라는 말 자체가 무의미하다"는
house 관례를 따른다 — 3년치 데이터면 D-0 기준 30회 이상의 사이클이 잡힐
것으로 예상되므로 6은 넉넉히 낮은 문턱이다(수집 초기라 데이터가 훨씬
짧아지는 상황에서도 최소한의 관찰치는 보여주기 위함).

**표본 부족 시 정직한 실패 — sector_rotation.py의 {"reason": ...} 패턴
재사용**: 사이클 수가 ``MIN_CYCLES`` 미만이면 노이즈뿐인 평균을 억지로
반환하지 않고 ``reason``에 이유를 담아 빈 결과를 반환한다.
"""

from __future__ import annotations

import bisect
import datetime as dt
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..derivatives import expiries_between
from ..models import IndexOhlcv

FUTURES_MARKET = "k200_futures"
SPOT_MARKET = "kospi200"

# D-0(만기 당일)부터 D-14까지 15개 지점 (모듈 docstring "MAX_LOOKBACK_DAYS" 참고).
MAX_LOOKBACK_DAYS = 14

# 이보다 적은 과거 사이클만 확보되면 평균/중앙값을 아예 계산하지 않는다
# (모듈 docstring "MIN_CYCLES" 참고).
MIN_CYCLES = 6

# 만기 후보일(둘째 목요일)이 실제 거래일이 아닐 때, 그 이하 가장 가까운 거래일을
# D-0으로 대신 쓰되 이 간극(달력일)을 넘으면 데이터 갭으로 보고 그 사이클을
# 건너뛴다(모듈 docstring "만기일이 거래일이 아닌 경우" 참고).
MAX_EXPIRY_ANCHOR_GAP_DAYS = 5


def _expiry_anchor_index(sorted_dates: list[dt.date], expiry: dt.date) -> int | None:
    """``expiry`` 이하에서 가장 가까운 거래일의 ``sorted_dates`` 인덱스.

    없거나(구간 이전) 간극이 ``MAX_EXPIRY_ANCHOR_GAP_DAYS``를 넘으면 None."""
    pos = bisect.bisect_right(sorted_dates, expiry) - 1
    if pos < 0:
        return None
    anchor_date = sorted_dates[pos]
    if (expiry - anchor_date).days > MAX_EXPIRY_ANCHOR_GAP_DAYS:
        return None
    return pos


def _insufficient(cycle_count: int, reason: str) -> dict[str, Any]:
    return {
        "cycle_count": cycle_count,
        "date_from": None,
        "date_to": None,
        "max_lookback_days": MAX_LOOKBACK_DAYS,
        "points": [],
        "reason": reason,
    }


def aggregate_expiry_pattern(
    futures_by_date: dict[dt.date, float],
    spot_by_date: dict[dt.date, float],
    expiries: list[dt.date],
    max_lookback_days: int = MAX_LOOKBACK_DAYS,
    min_cycles: int = MIN_CYCLES,
) -> dict[str, Any]:
    """과거 만기 사이클별로 베이시스를 D-day 정렬해 집계한다(순수 함수 —
    단위테스트가 synthetic dict만으로 실행, 모듈 docstring "순수 함수 분리"
    참고).

    Args:
        futures_by_date: {거래일: 선물종가}.
        spot_by_date: {거래일: 현물종가}. 두 dict의 **교집합 날짜**만 거래일로
            취급한다(``routers/basis.py::basis_series``와 동일한 날짜 교집합
            관례 — 한쪽에만 있는 날짜는 소스 지연/휴장 차이로 보고 제외).
        expiries: 이미 지나간(fully-elapsed) 과거 만기일 후보 목록
            (``derivatives.expiries_between``로 만들어 넘기되, 호출자가
            "아직 안 지난" 미래 만기는 걸러서 넘길 필요 없음 — 아래에서
            데이터의 마지막 거래일보다 미래인 만기는 이 함수가 자동으로
            제외한다, 데이터가 없으므로 자연히 걸러진다).
        max_lookback_days / min_cycles: 모듈 상수 기본값, 테스트에서 작은
            값으로 오버라이드 가능.

    Returns (표본 부족 또는 입력 없음):
        ``{"cycle_count", "date_from": None, "date_to": None,
        "max_lookback_days", "points": [], "reason": str}``.

    Returns (정상):
        ``{"cycle_count", "date_from" (가장 이른 사이클의 만기일, iso),
        "date_to" (가장 늦은 사이클의 만기일, iso), "max_lookback_days",
        "points": [{"d_day" (0=만기일, 음수일수록 더 이전), "mean_basis",
        "median_basis", "n" (그 d_day에 데이터가 있는 사이클 수 — 데이터
        시작 초기 사이클은 lookback이 짧아 큰 |d_day|일수록 n이 줄어들 수
        있다)}], "reason": None}`` — points는 d_day 오름차순
        (-max_lookback_days -> 0).
    """
    common_dates = sorted(set(futures_by_date) & set(spot_by_date))
    if not common_dates or not expiries:
        return _insufficient(0, "가격 데이터 또는 과거 만기일 후보가 없음")

    basis_by_date = {d: futures_by_date[d] - spot_by_date[d] for d in common_dates}
    last_date = common_dates[-1]

    cycles: list[dict[int, float]] = []
    cycle_expiries: list[dt.date] = []
    for expiry in sorted(expiries):
        if expiry > last_date:
            continue  # 아직 안 지난(데이터가 없는) 사이클 — 자연히 제외.
        anchor_idx = _expiry_anchor_index(common_dates, expiry)
        if anchor_idx is None:
            continue  # 구간 이전이거나 데이터 갭.

        cycle_values: dict[int, float] = {}
        for k in range(0, max_lookback_days + 1):
            idx = anchor_idx - k
            if idx < 0:
                break  # 데이터 시작 이전 — 이 사이클은 여기서부터 lookback 없음.
            cycle_values[k] = basis_by_date[common_dates[idx]]
        cycles.append(cycle_values)
        cycle_expiries.append(expiry)

    n_cycles = len(cycles)
    if n_cycles < min_cycles:
        return _insufficient(
            n_cycles,
            f"과거 만기 사이클 {n_cycles}회만 확보 — 최소 {min_cycles}회 필요",
        )

    points: list[dict[str, Any]] = []
    for k in range(0, max_lookback_days + 1):
        values = [c[k] for c in cycles if k in c]
        if not values:
            continue
        points.append(
            {
                "d_day": -k,
                "mean_basis": round(statistics.mean(values), 2),
                "median_basis": round(statistics.median(values), 2),
                "n": len(values),
            }
        )
    points.sort(key=lambda p: p["d_day"])

    return {
        "cycle_count": n_cycles,
        "date_from": min(cycle_expiries).isoformat(),
        "date_to": max(cycle_expiries).isoformat(),
        "max_lookback_days": max_lookback_days,
        "points": points,
        "reason": None,
    }


async def compute_expiry_pattern(session: AsyncSession) -> dict[str, Any]:
    """``index_ohlcv``에서 ``k200_futures``/``kospi200`` 전체 히스토리를 읽어
    ``aggregate_expiry_pattern``으로 집계한다(PLAN.md §5.42-2). 새 외부 호출
    없음(DB 읽기 전용) — ``routers/basis.py::basis_series``와 동일한
    market -> {date: close} dict 구성 방식을 재사용하되, ``days`` 제한 없이
    **전체 히스토리**를 읽는다(만기 패턴 분석은 최대한 많은 과거 사이클이
    필요하므로 짧은 창이 아니라 있는 데이터 전부).

    데이터가 하나도 없거나 사이클 수가 부족하면 크래시하지 않고
    ``aggregate_expiry_pattern``의 정직한 "표본 부족"(``reason`` 있음)
    결과를 그대로 반환한다."""
    stmt = select(IndexOhlcv).where(IndexOhlcv.market.in_((FUTURES_MARKET, SPOT_MARKET)))
    rows = (await session.execute(stmt)).scalars().all()

    futures_by_date: dict[dt.date, float] = {}
    spot_by_date: dict[dt.date, float] = {}
    for r in rows:
        if r.close is None:
            continue
        if r.market == FUTURES_MARKET:
            futures_by_date[r.date] = float(r.close)
        else:
            spot_by_date[r.date] = float(r.close)

    common_dates = sorted(set(futures_by_date) & set(spot_by_date))
    if not common_dates:
        return _insufficient(0, "index_ohlcv에 k200_futures/kospi200 교집합 데이터가 없음")

    expiries = expiries_between(common_dates[0], common_dates[-1])
    return aggregate_expiry_pattern(futures_by_date, spot_by_date, expiries)
