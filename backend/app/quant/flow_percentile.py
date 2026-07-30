"""수급 유니버스 상대순위(percentile) — 시가총액 tier 내 순매수 백분위
(PLAN.md §5.38, 2026-07-30 §5.37 경쟁 서비스 벤치마킹 B-1).

**배경**: §5.37 조사에서 뉴지랭크는 종목별 수급을 "비슷한 시가총액 규모
종목들 대비 얼마나 몰렸는지"(peer-relative percentile)로 보여주는데, 이
프로젝트는 지금까지 수급을 절대값(원)이나 그 종목 자기 과거 대비(§5.13류)로만
보여줬을 뿐 "오늘 유니버스 안에서 이 종목이 얼마나 특이한지"는 답하지
못했다. 이 모듈이 그 공백을 메운다.

**입력 데이터는 전부 이미 수집 중이던 것 — 새 외부 호출 없음**:
`value_rank`(거래대금 상위 시장당 top100, collectors/value_rank.py)가 §5.38-1로
`market_value_million`(시가총액/AUM, 백만 원)을 저장하기 시작했고, 같은 종목군에
대해 `stock_flow`(외국인+기관 순매수)는 `collectors/live_refresh.py::
_run_stock_flow_scan`이 10분마다 이미 채우고 있다 — 이 모듈은 두 테이블을 조인한
결과(호출자가 조립해서 넘김, 아래 "입력 형태" 참고)를 받아 계산만 하는 순수
함수다(DB/네트워크 의존 없음, quant/sector_rotation.py·screener.py와 동일한
compute/collect 분리 원칙 — 단위테스트 대상).

**시장별 분리 — §5.19와 동일한 원칙, 이 모듈이 직접 강제한다**: 코스피와 코스닥은
시가총액 규모가 구조적으로 다르다(§5.19 "쏠림 비율 시총 편향" 문제와 동일한
클래스의 함정) — 두 시장을 합쳐서 시총 tier를 나누면 코스닥 종목은 전부 최하위
tier에 몰리고 "코스닥 안에서는 수급이 몰린 대형주"조차 "코스피 기준 소형주"로
잘못 분류된다. 그래서 이 함수는 입력 rows에 여러 시장이 섞여 있어도(``market``
필드 기준) **내부에서 시장별로 완전히 나눠서** tier 분류·percentile 계산을 각각
수행한다 — 호출자가 시장을 미리 분리해서 넘겨야 하는 책임을 지지 않는다(실수로
섞어 넘겨도 이 함수가 방어한다, 단위테스트 "per-market separation" 참고).

**tier 분류 — 순위 기준 4등분(값 기준 사분위가 아님, 판단 근거)**: 시가총액
"값"을 4등분(예: 최댓값-최솟값 구간을 균등 4분할)하면 소수의 초대형주(예:
코스피의 삼성전자·SK하이닉스)가 분포를 극단적으로 왜곡해 최상위 구간에
종목이 1~2개만 남고 나머지 98개가 나머지 3개 tier에 몰리는 불균형이 생긴다
— tier마다 표본 수가 크게 달라지면 percentile 자체의 신뢰도가 tier별로
들쭉날쭉해진다. 그래서 이 모듈은 값이 아니라 **순위**를 4등분한다(시가총액
내림차순 정렬 후 개수 기준으로 tier_count개 구간으로 자름) — 각 tier의 표본
수가 항상 거의 동일(§5.19가 "활동량 배율로 정규화"해 시장 간 비교 가능하게 만든
것과 같은 방향의 판단: 표본 크기를 인위적으로 균등하게 만들어야 비교가 의미
있어진다). tier 수는 기본 4(사분위, PLAN.md §5.38 설계 절이 예시로 든 값을
그대로 채택) — 시장당 실측 종목 수(~70~100, ETF 제외 후)에서 tier당 15~25개
정도면 percentile 계산에 충분하다고 판단했다. 나머지(N을 tier_count로 나눈
나머지)는 앞쪽(시총이 더 큰) tier부터 하나씩 배분한다.

**percentile 계산 — 표준 percentile rank 공식(동점 평균 처리)**: tier 안에서
``flow_net_value``의 percentile은
``(해당 값보다 작은 개수 + 0.5 * 같은 값 개수) / tier_size * 100``으로 정의한다
(통계학의 표준 percentile rank 공식 — 동점이 있어도 tier 전체 percentile의
평균이 정확히 50이 되도록 보정됨, 단위테스트로 손계산 검증). 값이 클수록(더 큰
순매수) percentile이 높다 — 100에 가까울수록 "이 tier 안에서 오늘 수급이 가장
쏠린 축에 속한다", 0에 가까울수록 반대(순매도가 가장 두드러짐)라는 뜻이다.

**이것은 관찰 지표다 — 매매 신호가 아니다**: §5.15(3년 백테스트로 예측력이
없었던 사례)·§5.33(로테이션 지표가 판정을 반환하지 않는 house rule)·
volume_profile.py("근사치 — 예측 아님")와 동일한 원칙. 이 percentile이 높다고
"오를 것"이라는 근거는 전혀 없다 — "오늘 비슷한 규모 종목들 대비 수급이
쏠렸다/빠졌다"는 관찰 사실만 말한다. 이 지표 자체의 예측력은 검증된 적이
없다.

**스코프 한계 — 전 시장이 아니라 value_rank top100×시장 안에서만의 상대순위**:
입력 rows는 호출자가 `value_rank`에서 가져온 그날 그 시장의 종목군(최대 100개,
거래대금 상위)으로 제한된다는 전제다 — 전체 상장 종목(~4천개) 대비 순위가
아니라 "오늘 거래대금 상위권 안에서의" 상대순위라는 뜻이다. 이 한계를 호출자
(라우터/프런트)가 사용자에게 그대로 노출해야 한다.

**입력 형태**: ``rows: list[dict]``, 각 원소 최소
``{"code": str, "market": str, "market_value_million": int|None,
"flow_net_value": int|None}``. ``market_value_million``/``flow_net_value``
둘 중 하나라도 None인 행은 조용히 제외한다(0으로 채우면 "진짜 0"과 "데이터
없음"이 구분 안 된다는 이 프로젝트 공통 관례, §5.25/§5.19/screener.py의
`_stock_flow_lookup`와 동일). ``flow_net_value``는 호출자가 "외국인+기관계
순매수 합"으로 미리 조립해서 넘긴다(조합 규칙 자체는 이 모듈이 정하지 않음
— routers/scalp.py::_stock_flow_lookup, quant/screener.py와 동일한 조합을
재사용하는 게 호출자의 책임).
"""

from __future__ import annotations

DEFAULT_TIER_COUNT = 4

# tier 하나당 최소 2개는 있어야 percentile이라는 말이 의미가 있다고 판단해
# tier_count * 2를 표본 최소치로 둔다(그 미만이면 "표본 부족"으로 정직하게
# 실패 — sector_rotation.py의 MIN_BASELINE_DAYS와 동일한 house 관례).
MIN_SAMPLES_PER_TIER = 2


def _tier_sizes(n: int, tier_count: int) -> list[int]:
    """n개를 tier_count개 tier로 최대한 균등하게(개수 기준) 나눈 크기 리스트.

    나머지는 앞쪽(시총이 더 큰) tier부터 하나씩 배분한다 — 예: n=102,
    tier_count=4 -> [26, 26, 25, 25]."""
    base, remainder = divmod(n, tier_count)
    return [base + 1 if i < remainder else base for i in range(tier_count)]


def _percentile_ranks(values: list[float]) -> list[float]:
    """표준 percentile rank 공식(동점 평균 처리) — 모듈 docstring "percentile
    계산" 참고. 반환값은 values와 같은 순서, 소수 1자리 반올림."""
    n = len(values)
    out: list[float] = []
    for v in values:
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append(round((below + 0.5 * equal) / n * 100, 1))
    return out


def compute_flow_percentiles(
    rows: list[dict], tier_count: int = DEFAULT_TIER_COUNT
) -> dict[str, dict]:
    """market -> 결과 dict. rows에 등장한 market 값별로 완전히 독립된 계산을
    수행한다(모듈 docstring "시장별 분리" 참고 — 섞여 들어와도 이 함수가
    분리를 보장한다).

    Returns 예시:
        ``{"kospi": {"reason": None, "tier_count": 4, "sample_size": 97,
        "results": [{"code", "market_value_million", "flow_net_value",
        "tier": 1, "tier_size": 25, "percentile": 62.0}, ...]},
        "kosdaq": {"reason": "표본 부족(...)", "tier_count": 4,
        "sample_size": 3, "results": []}}``

    ``tier``는 1이 시가총액 최상위 tier(1-based). ``results``는
    market_value_million 내림차순 정렬. 표본 부족(그 market의 유효 행 수가
    ``tier_count * MIN_SAMPLES_PER_TIER`` 미만)이면 그 market은 ``reason``에
    이유를 담고 ``results``는 빈 리스트(§5.26/§5.30/§5.33 "표본 부족은
    정직하게 표시" 관례).
    """
    by_market: dict[str, list[dict]] = {}
    for row in rows:
        market = row.get("market")
        market_value = row.get("market_value_million")
        flow_value = row.get("flow_net_value")
        if market is None or market_value is None or flow_value is None:
            continue
        by_market.setdefault(market, []).append(
            {
                "code": row.get("code"),
                "market_value_million": market_value,
                "flow_net_value": flow_value,
            }
        )

    min_sample = tier_count * MIN_SAMPLES_PER_TIER
    out: dict[str, dict] = {}
    for market, entries in by_market.items():
        n = len(entries)
        if n < min_sample:
            out[market] = {
                "reason": (
                    f"유효 표본 {n}개 — tier {tier_count}개 x 최소 "
                    f"{MIN_SAMPLES_PER_TIER}개({min_sample}개)에 못 미침"
                ),
                "tier_count": tier_count,
                "sample_size": n,
                "results": [],
            }
            continue

        entries_sorted = sorted(entries, key=lambda e: e["market_value_million"], reverse=True)
        sizes = _tier_sizes(n, tier_count)

        results: list[dict] = []
        idx = 0
        for tier_no, size in enumerate(sizes, start=1):
            tier_entries = entries_sorted[idx : idx + size]
            idx += size
            flow_values = [e["flow_net_value"] for e in tier_entries]
            percentiles = _percentile_ranks(flow_values)
            for entry, pct in zip(tier_entries, percentiles):
                results.append(
                    {
                        "code": entry["code"],
                        "market_value_million": entry["market_value_million"],
                        "flow_net_value": entry["flow_net_value"],
                        "tier": tier_no,
                        "tier_size": size,
                        "percentile": pct,
                    }
                )

        out[market] = {
            "reason": None,
            "tier_count": tier_count,
            "sample_size": n,
            "results": results,
        }

    return out
