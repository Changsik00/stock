"""ETF 비중 변화 ↔ 실제 수급/가격 상관관계 백테스트 인프라 (PLAN.md §5.26-2).

**이 모듈이 측정하는 것**: `etf_holdings`에 존재하는 **모든** 연속 스냅샷 날짜
쌍(가장 최근 하나만이 아니라 전체)에 대해, 종목별 "여러 ETF에 걸친 비중 변화
합계"와 그 종목의 **실제** 수급 변화(`stock_flow`)·가격 변동률(`stock_ohlcv`)
사이의 피어슨 상관계수를 계산한다. `etf_weight_changes.py`(§5.25/§5.26-1)의
스크리너가 "가장 최근 2개 스냅샷"만 사람이 눈으로 대조하도록 보여주는 것과
달리, 이 모듈은 그 대조를 통계적으로 일반화하려는 시도다.

**왜 지금 만드는가 — 지금 당장은 쓸 수 없는데도**: `etf_holdings`(§4.5, ETF별
일별 top10 구성 스냅샷)는 현재 **4일치(2026-07-18/21/22/23)뿐**이라 연속 날짜
쌍이 3개뿐이다. §5.15(코스닥·외국인 연속매수 스트릭)·§5.23(코스피/코스닥 쏠림)
백테스트는 3년치(~750거래일)를 썼는데, 이 모듈은 그 100분의 1도 안 되는 표본
으로 시작한다. 그래도 인프라를 지금 만들어 두는 이유는 "쌓일 때마다 언제든
재계산할 수 있게"(§5.7 스켈핑 track-record와 동일한 "쌓일수록 갱신" 철학) —
`etf_holdings` 수집이 몇 주~몇 달 이어지면 이 모듈을 그대로 재실행해 §5.15/
§5.23처럼 정식 결과 판정을 내릴 수 있다. 지금 당장 만들지 않으면 그 시점에
가서야 처음부터 짜야 한다.

**표본 부족 경고 — 반드시 지킬 것(n < 30이면 참고용 이하)**: 이 모듈이
반환하는 상관계수는 **호출부가 관리자 전용으로만 다뤄야 하고, 어떤 대시보드
카드에도 노출하면 안 된다**(PLAN.md §5.26-2 명시). `n_stock_observations`가
`min_reliable_n`(30) 미만이면 결과에 담긴 상관계수는 방향성조차 참고하지
말아야 한다 — 표본이 이 정도로 작으면 상관계수는 신호가 아니라 노이즈이고,
부호가 우연히 양수든 음수든 다음 재계산에서 뒤집힐 수 있다. 이건 §5.15에서
"백워데이션 → 차익매도유의" 배지가 그럴듯해 보였지만 실제 3년치 백테스트로는
예측력이 전혀 없어(43.8% vs 43.0%) 배지를 통째로 제거했던 것과 §5.23에서
쏠림 비율 백테스트가 "신호 없음"으로 판정났던 것, 두 사례가 이미 이 프로젝트에
가르쳐 준 교훈이다 — 표본이 너무 적어 신호와 노이즈를 구분할 수 없는 상태에서
"상관관계가 있다/없다"를 결론 내리면 똑같은 실수를 반복하는 것이다. 현재 실
데이터의 독립적인 "사건" 수(`n_date_pairs`)는 3뿐이라 이 30이라는 최소치에
한참 못 미치므로, 지금 이 모듈이 내는 어떤 숫자도 "발견(finding)"이 아니라
그저 "배관이 작동한다"는 확인일 뿐이다.

**`n_stock_observations`가 30을 넘어도 방심 금지 — 진짜 독립 표본은 여전히
`n_date_pairs`다(2026-07-24 실 DB 검증으로 발견)**: 처음엔 "종목 표본도
날짜쌍만큼 적을 것"이라고 예상했지만, 실제로 돌려보니 `n_stock_observations`
가 995까지 나왔다 — 3개 날짜쌍 각각에서 300개 안팎의 종목이 동시에 "연속
노출 변화"를 겪었기 때문이다(코스피/코스닥 전체가 하루 사이 움직이면 그
움직임과 얽힌 ETF 비중도 한꺼번에 수백 종목에서 바뀐다). 하지만 이 300여
개는 서로 **독립적인 관측치가 아니다** — 같은 날짜쌍(같은 하루)의 시장 전체
분위기·지수 등락·수급 쏠림을 공유하는 하나의 "사건"에 속한 표본들이다
(횡단면 유사상관, cross-sectional pseudo-replication). 진짜 독립적인 "사건"의
개수는 `n_date_pairs`(현재 3)이고, `n_stock_observations`(현재 995)는 그 3개
사건을 종목 단위로 쪼개 부풀린 것에 가깝다. 그래서 `reliable =
n_stock_observations >= 30`이 `True`로 나와도(현재 실 데이터가 정확히 이
경우다) **날짜쌍이 여전히 3개뿐이라면 상관계수를 신뢰해서는 안 된다** —
`reliable` 필드는 작업 지시(PLAN.md §5.26-2)가 명시한 정의(종목 단위 표본수
기준) 그대로 계산해 반환하지만(이 필드의 정의를 임의로 바꾸지 않는다),
**호출부는 `reliable` 하나만 보지 말고 `n_date_pairs`도 반드시 함께
확인해야 한다** — `n_date_pairs`가 두 자릿수 이상으로 쌓이기 전에는
`reliable: true`만으로 결과를 신뢰해서는 안 된다.

**UI/라우터에 배선하지 않는다**: 이 모듈은 어떤 FastAPI 라우터에도 연결돼
있지 않고 프런트엔드 어디에서도 호출하지 않는다 — 의도적이다. `etf_holdings`
히스토리가 충분히 쌓인 뒤(예: 30일 이상, 위 최소 표본 기준과 동일선상) 누군가
Python REPL이나 일회성 스크립트로 `compute_etf_weight_correlation`을 직접
호출해 재평가하는 용도로 남겨둔다. 표본이 부족한 상태에서 일반 사용자에게
"상관관계" 숫자를 대시보드에 보여주면 그 자체로 과신을 유발하므로(정확히 위
"표본 부족 경고" 절이 경계하는 실수), 이번 단계에서는 노출하지 않는다.

**연속 노출 변화만 본다 — 신규편입/편출은 이 스코프에서 0으로 취급(의도적
단순화)**: `etf_weight_changes.py`는 신규편입/편출/비중확대/비중축소 4종
이벤트로 세밀하게 분류하지만(그 모듈의 "top10 스냅샷 한계" 절 참고 — top10
밖으로 밀려난 것과 실제로 판 것을 구분할 수 없다는 근본적 한계 때문에 그
분류가 필요했다), 이 백테스트는 그 분류 기계를 그대로 가져오지 않는다. 대신
"두 스냅샷 모두에서 그 ETF가 그 종목을 보유했던 경우"만 골라 `curr_weight -
prev_weight`를 더한다 — 한쪽에만 존재하는(신규편입/편출) 조합은 애초에 비교할
"이전 비중"이나 "이후 비중"이 없으므로 그 ETF 기여분을 0으로 취급하고
건너뛴다. 이건 버그가 아니라 스코프를 좁힌 것이다: 이 모듈은 "이미 들고
있던 노출이 얼마나 커지거나 작아졌는가"라는 연속적 크기 변화에 대한 상관관계를
보려는 것이지, `etf_weight_changes.py`가 이미 다루는 이산적 편입/편출 이벤트를
다시 만들려는 게 아니다(두 모듈이 각자 다른 질문에 답한다).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EtfHolding, Stock
from . import etf_weight_changes

# 통계적으로 "참고 이하로도 취급하지 말 것"인 최소 표본 수(PLAN.md §5.26-2
# 명시 임계값). 이 미만이면 correlation_* 값이 있어도 방향성조차 신뢰하지 않는다
# (모듈 독스트링 "표본 부족 경고" 절 참고).
MIN_RELIABLE_N = 30


async def _all_consecutive_date_pairs(session: AsyncSession) -> list[tuple[dt.date, dt.date]]:
    """`etf_holdings`에 실제로 존재하는 모든 날짜(오름차순)에서 연속한
    (이전, 다음) 쌍을 전부 만든다 — `etf_weight_changes._two_most_recent_dates`가
    "가장 최근 2개"만 보는 것과 달리 이 백테스트는 과거 전체를 훑어야 하므로
    전체 연속 쌍이 필요하다. 날짜가 4개(D1<D2<D3<D4)면 [(D1,D2), (D2,D3),
    (D3,D4)] 3쌍을 반환한다(현재 실 데이터 상태 그대로)."""
    stmt = select(EtfHolding.date).distinct().order_by(EtfHolding.date.asc())
    dates = (await session.execute(stmt)).scalars().all()
    return list(zip(dates, dates[1:]))


async def _aggregate_stock_weight_change(
    session: AsyncSession,
    prev_date: dt.date,
    curr_date: dt.date,
    exclude_leveraged: bool = True,
) -> dict[str, float]:
    """한 날짜쌍에 대해 종목별 "여러 ETF에 걸친 비중 변화 합계"를 계산한다
    (모듈 독스트링 "연속 노출 변화만 본다" 참고 — 두 스냅샷 모두에 존재하는
    (ETF, 종목) 조합만 `curr_weight - prev_weight`를 더하고, 신규편입/편출은
    그 ETF 기여분을 0으로 취급해 건너뛴다).

    ``exclude_leveraged``는 기본 ``True``다 — `etf_weight_changes.
    compute_etf_weight_changes`의 ``exclude_leveraged`` 기본값(``False``,
    스크리너는 숨기지 않고 플래그로만 보여주는 게 원칙)과 **의도적으로
    반대**다: 이 함수는 상관관계 연구용이라, 하이닉스 "KODEX 반도체레버리지"
    사례처럼 이미 기계적 리밸런싱임이 강하게 의심되는 노이즈 원천을 기본적으로
    빼고 계산해야 "진짜 재량적 자금 흐름과 가격의 관계"에 더 가까운 신호를
    본다(빼지 않으면 레버리지 ETF의 일상적 리밸런싱이 상관관계 계산에 매일
    큰 잡음으로 섞여 들어간다).

    Returns ``{stock_code: 비중변화 합계(%p), ...}`` — 두 스냅샷 모두에 존재하는
    (ETF, 종목) 조합이 하나도 없으면 빈 dict.
    """
    prev_rows = (
        await session.execute(select(EtfHolding).where(EtfHolding.date == prev_date))
    ).scalars().all()
    curr_rows = (
        await session.execute(select(EtfHolding).where(EtfHolding.date == curr_date))
    ).scalars().all()

    prev_map: dict[tuple[str, str], float] = {
        (r.etf_code, r.stock_code): float(r.weight) for r in prev_rows if r.weight is not None
    }
    curr_map: dict[tuple[str, str], float] = {
        (r.etf_code, r.stock_code): float(r.weight) for r in curr_rows if r.weight is not None
    }

    # 신규편입/편출(한쪽에만 존재)은 비교 대상이 없어 애초에 대상에서 빠진다 —
    # 교집합만 "연속 노출 변화"로 취급한다(모듈 독스트링 참고).
    common_keys = set(prev_map) & set(curr_map)
    if not common_keys:
        return {}

    if exclude_leveraged:
        etf_codes = {etf_code for etf_code, _ in common_keys}
        name_rows = (
            await session.execute(select(Stock.code, Stock.name).where(Stock.code.in_(etf_codes)))
        ).all()
        name_map = dict(name_rows)
        common_keys = {
            key
            for key in common_keys
            if not any(
                marker in name_map.get(key[0], key[0])
                for marker in etf_weight_changes.LEVERAGED_INVERSE_NAME_MARKERS
            )
        }

    result: dict[str, float] = {}
    for etf_code, stock_code in common_keys:
        delta = curr_map[(etf_code, stock_code)] - prev_map[(etf_code, stock_code)]
        result[stock_code] = result.get(stock_code, 0.0) + delta
    return result


def _pearson_correlation(pairs: list[tuple[float, float]]) -> float | None:
    """평범한 파이썬으로 구현한 피어슨 상관계수(scipy/numpy 등 새 의존성을
    추가하지 않는다 — PLAN.md §5.26-2 작업 지시). 표본이 2개 미만이거나 어느
    한쪽 변수의 분산이 0이면(전부 같은 값이라 상관계수 정의 자체가 불가능,
    분모가 0이 되는 경우) 예외를 던지지 않고 ``None``을 반환한다(이 프로젝트
    전반의 "표본 부족 → None, 예외 아님" 관례 —
    `regime_backtest`/`concentration_backtest`/`etf_weight_changes` 참고)."""
    n = len(pairs)
    if n < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


async def compute_etf_weight_correlation(
    session: AsyncSession, exclude_leveraged: bool = True
) -> dict:
    """모든 연속 `etf_holdings` 날짜쌍에 대해 종목별 비중변화 합계
    (`_aggregate_stock_weight_change`)와 그 종목의 실제 수급 변화
    (`etf_weight_changes._stock_flow_delta_map`)·가격 변동률
    (`etf_weight_changes._price_change_pct_map`, 두 함수 모두 §5.26-1 스크리너와
    완전히 동일한 로직을 그대로 재사용 — 복붙하지 않는다는 이 프로젝트의
    house rule) 사이의 피어슨 상관계수를 계산한다.

    모든 날짜쌍에서 나온 (비중변화, 수급변화) 쌍과 (비중변화, 가격변동률) 쌍을
    각각 하나의 평평한 리스트로 모아(풀링) 상관계수를 하나씩만 계산한다 —
    날짜쌍별로 따로 계산하지 않는다(날짜쌍 하나당 종목 표본이 적어 애초에
    개별 계산이 불안정하다). 수급/가격 값이 ``None``인 종목(그 구간에
    `stock_flow`/`stock_ohlcv` 데이터가 없음)은 해당 리스트에서 제외한다.

    ``n_stock_observations``는 두 상관계수 리스트의 표본수가 아니라, 모든
    날짜쌍에서 나온 "비중변화가 계산된 (종목, 날짜쌍)" 원본 관측치 총합이다
    (수급/가격 결측으로 리스트에서 빠진 것도 포함) — 이 값을 신뢰도 판단
    기준(``reliable``)으로 쓰는 이유는, 두 상관계수 각각의 표본수가 다를 수
    있어(수급은 있는데 가격이 없는 종목 등) 어느 한쪽만 기준으로 삼으면
    비대칭적이기 때문이다(작업 지시가 단일 값을 요구해 내린 판단 재량 — 이
    필드의 정의를 명확히 문서화해 두 상관계수 표본수와 혼동하지 않게 한다).
    **다만 이 값은 "독립 표본수"가 아니다** — 모듈 독스트링 "`n_stock_observations`
    가 30을 넘어도 방심 금지" 절 참고. 실 DB 검증 결과 날짜쌍이 3개뿐인데도
    각 날짜쌍마다 수백 종목이 동시에 움직여 ``n_stock_observations``가 995까지
    나왔다 — 이 종목들은 같은 날의 시장 전체 움직임을 공유하는 서로 종속적인
    표본이라, 진짜 독립 관측 단위는 여전히 ``n_date_pairs``다.

    Returns ``{"n_date_pairs": int, "n_stock_observations": int,
    "correlation_weight_vs_flow": float|None, "correlation_weight_vs_price":
    float|None, "min_reliable_n": 30, "reliable": bool}``.
    ``reliable = n_stock_observations >= min_reliable_n`` — 작업 지시(PLAN.md
    §5.26-2)가 명시한 정의 그대로이지만, **``True``라고 해서 상관계수를 믿어도
    된다는 뜻이 아니다**. ``n_date_pairs``가 아직 한 자릿수(현재 3)라면
    ``reliable``이 ``True``로 나와도 상관계수의 방향성조차 참고하면 안 된다
    (모듈 독스트링의 두 "표본 부족" 절을 모두 참고). 상관계수는 각각 쌍이
    2개 미만이거나 분산이 0이면 ``None``(`_pearson_correlation` 참고).
    """
    pairs = await _all_consecutive_date_pairs(session)
    n_date_pairs = len(pairs)

    weight_flow_pairs: list[tuple[float, float]] = []
    weight_price_pairs: list[tuple[float, float]] = []
    n_stock_observations = 0

    for prev_date, curr_date in pairs:
        weight_changes = await _aggregate_stock_weight_change(
            session, prev_date, curr_date, exclude_leveraged=exclude_leveraged
        )
        if not weight_changes:
            continue
        n_stock_observations += len(weight_changes)

        codes = sorted(weight_changes)
        flow_map = await etf_weight_changes._stock_flow_delta_map(session, codes, prev_date, curr_date)
        price_map = await etf_weight_changes._price_change_pct_map(session, codes, prev_date, curr_date)

        for code, w_delta in weight_changes.items():
            flow_delta = flow_map.get(code)
            if flow_delta is not None:
                weight_flow_pairs.append((w_delta, float(flow_delta)))
            price_change = price_map.get(code)
            if price_change is not None:
                weight_price_pairs.append((w_delta, price_change))

    return {
        "n_date_pairs": n_date_pairs,
        "n_stock_observations": n_stock_observations,
        "correlation_weight_vs_flow": _pearson_correlation(weight_flow_pairs),
        "correlation_weight_vs_price": _pearson_correlation(weight_price_pairs),
        "min_reliable_n": MIN_RELIABLE_N,
        "reliable": n_stock_observations >= MIN_RELIABLE_N,
    }
