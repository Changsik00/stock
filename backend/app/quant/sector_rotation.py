"""업종 자금 로테이션 관찰 지표 (PLAN.md §5.33-2, 2026-07-28 JTBC 뉴스 클립 검토).

**배경**: 사용자가 검토를 요청한 뉴스 클립의 진단 — 코스피 7,000선을 넘을 때마다
외국인이 차익실현 매도로 돌아서고, 개인은 삼성전자·SK하이닉스만 순매수하며
나머지는 순매도하고, 기관도 반도체 비중은 줄였지만 그 자금이 다른 업종으로
옮겨가지 않았다(대신 미국 주식·은행 예금으로 국내 증시 이탈) — 를 "업종 간
자금 순환이 실제로 있는지" 매번 체크할 수 있게 만든 관찰 지표. §5.33 상단
문서(PLAN.md) 참고.

**설계 원칙 — 판정 없음(§5.15/§5.23/§5.26과 동일한 house rule)**: 이 모듈은
"로테이션 있다/없다"는 블랙박스 판정을 반환하지 않는다. §5.15에서 "백워데이션 →
차익매도유의" 배지가 그럴듯해 보였지만 3년 백테스트로 예측력이 없었던 사례,
§5.23의 쏠림 비율 백테스트가 지표를 신호로 승격하기 전 반드시 검증부터 거친
사례를 반복하지 않기 위해서다. `quant/concentration_backtest.py::
compute_daily_concentration_series`가 "쏠림%"라는 숫자만 반환하고 "코스피가
유리하다/불리하다"를 말하지 않는 것과 동일하게, 이 모듈도 랭킹과 보조 통계
숫자만 반환한다 — 해석은 호출자(사람, 또는 나중에 만들 백테스트)의 몫이다.

**데이터 소스**: `collectors/market_flow.py`가 ka10051(업종별투자자순매수)
응답에서 파싱해 적재하는 `sector_flow`(§5.33-1). 저장된 3개 투자자 분류
(외국인/기관계/개인) 중 **외국인+기관계** 합을 "업종 자금 흐름"으로 쓴다 —
`quant/etf_weight_changes.py::FLOW_INVESTORS`, `quant/concentration_backtest.py::
ACTIVITY_INVESTORS`와 동일한 조합("스마트머니" 관례 재사용, 새 조합 발명 안 함).
개인은 보통 외국인+기관계의 거울상(zero-sum에 가까움)이라 포함하면 방향
신호가 상쇄되어 오히려 노이즈가 된다.

**반드시 signed(부호 있는) 값 — 절댓값 아님**: §5.18/§5.19 쏠림 비율은 "활동량"
(방향 무관 |순매수| 합)을 쓰지만, 로테이션은 정의상 방향(어디서 빠져서 어디로
갔는지)이 핵심이라 PLAN.md §5.33-2 작업 지시가 명시적으로 signed 값을
요구한다 — 이 판단을 흐리면 지표 자체가 질문에 답하지 못한다.

**"진짜 업종"만 랭킹 대상 — 실측 근거(2026-07-28)**: ka10051의 `inds_netprps`에는
산업별 분류 말고도 시가총액 규모별(코스피 대형주/중형주/소형주, `inds_cd`
002_AL/003_AL/004_AL) 행과 지수구성별(코스닥 KOSDAQ 100/MID 300/SMALL/
우량기업/벤처기업/중견기업/신성장기업/150/글로벌지수, 138_AL~151_AL 사이 9개
코드) 행이 섞여 있다(`collectors/market_flow.py::_parse_sector_rows`는 원본
손실 없이 전부 저장하고 이 판단을 여기로 미룬다). "대형주로 자금 유입"은
특정 산업으로 자금이 쏠렸다는 뜻이 아니라 "시총 큰 종목들이 잘 나갔다"는
동어반복이고, "KOSDAQ 150 유입"도 마찬가지로 산업 로테이션과 무관해
`NON_INDUSTRY_SECTOR_CODES`로 명시적으로 제외한다.

**`group_snapshot.py`(네이버, 업종 79개/테마 266개)와는 별개 분류 체계**: 코드
공간·분류 세분도가 완전히 다르다 — 실측 확인 예시로 코스피 "전기/전자"
(013_AL) 하나가 네이버 업종의 "반도체와반도체장비"/"전자장비와기기"/
"전자제품"/"디스플레이패널"/"디스플레이장비및부품"/"컴퓨터와주변기기"/
"핸드셋"/"통신장비"/"전기장비"/"전기제품" 등 여러 개로 쪼개져 있다. 즉
**뉴스가 말하는 "반도체" 업종에 대한 이 모듈의 가장 가까운 프록시는 "전기/전자"
(013_AL 코스피 / 124_AL 코스닥)이지만, 이는 반도체 외에 다른 전자 하위 산업도
섞인 더 넓은 버킷**이다 — 코스피 시가총액 상위가 삼성전자·SK하이닉스라 이
버킷의 움직임이 반도체 수급을 대체로 반영할 것으로 기대하되, 정밀한 "반도체만"
지표가 아님을 결과 해석 시 항상 함께 언급해야 한다. 이 모듈은 이 매핑을
코드로 강제하지 않는다(전기/전자 버킷을 "반도체"로 자동 라벨링하지 않음) —
`sector_name` 원본("전기/전자")을 그대로 노출해 호출자가 스스로 판단하게 한다.
group_snapshot의 업종·테마 트리맵과는 독립적으로 취급하며 상호 매핑 테이블은
만들지 않는다(코드 체계 자체가 근본적으로 다른 taxonomy라 억지로 매핑하면
왜곡이 더 크다는 판단, PLAN.md §5.33-1 참고).

**베이스라인: 오늘(recent) vs 직전 N거래일(baseline, 비중첩) — volume_surge.py
패턴 재사용**: `regime_backtest.compute_activity_baseline`처럼 "오늘도 포함한
최근 20일 평균"을 baseline으로 쓰지 않는다 — 오늘 자신이 자기 베이스라인에
섞이면 이상치가 평균에 흡수돼 배율이 둔감해지기 때문이다. 대신
`quant/volume_surge.py::compute_volume_surge`의 recent/baseline **비중첩**
슬라이싱 관례를 그대로 따른다: baseline은 오늘(target_date)을 포함하지 않는
직전 `baseline_days`거래일(기본 20, §5.19/§5.23과 동일한 윈도우)이다.

**배율 정의 — signed 분자 / unsigned 분모(판단 근거)**: baseline을 그대로
signed 평균으로 잡고 "오늘/평소" 배율을 계산하면 두 가지 수학적 결함이 생긴다 —
(a) baseline이 0 근처인 업종은 배율이 무한대로 튀고, (b) 오늘과 baseline이
둘 다 큰 음수인 업종은 배율이 크고 "정상적인" 양수로 나와 방향 정보가
사라진다. 그래서 이 모듈은 §5.18/§5.19/`volume_surge.py`가 이미 쓰는 "평소
활동량"(activity, 부호 무관 |signed| 평균 — 항상 0 이상이고 사실상 항상
0보다 큼, 어느 업종이든 매일 어느 정도는 거래가 있으므로)을 분모로 쓰고,
분자만 오늘의 **signed** 값을 써서 ``multiple = 오늘_signed / 평소_activity``로
정의한다. 결과 부호가 오늘의 방향을 그대로 보존한다 — "+0.3배"=평소 활동량의
30%만큼 오늘 순매수, "-3배"=평소 활동량의 3배 규모로 오늘 순매도. 분모가
0 이하(데이터 이상)이면 그 업종은 계산에서 제외한다(§5.19 "정규화 기준이
없으면 억지로 채우지 않는다" 원칙).

**"순환 vs 이탈" 보조 통계 — 판정 아님, 숫자만**: 오늘 signed > 0인 업종들의
signed 합(``gaining_sum``, 총 유입)과 signed < 0인 업종들의 signed 합
(``losing_sum``, 총 유출, 음수)을 각각 보여준다. 두 값의 절댓값이 비슷하면
"자금이 한쪽 업종에서 빠져 다른 쪽으로 그대로 옮겨갔다"는 정황(순환에 가까움),
``gaining_sum``이 ``abs(losing_sum)``보다 훨씬 작으면 "유출을 상쇄할 유입이
부족하다"는 정황(이탈에 가까움)이지만, **이 모듈은 이 해석을 라벨로 반환하지
않는다** — PLAN.md §5.33이 명시한 대로 사용자가 숫자를 보고 직접 판단한다.
``aggregate_delta``(오늘 전체 signed 합 - 평소 전체 signed 평균)는 "전체
순매수 총합 자체가 줄고 있는지"를 보는 보조 수치다.

**표본 부족 시 정직한 실패**: 전체 거래일 수가 `MIN_BASELINE_DAYS`(5) +
1(오늘)보다 적으면 베이스라인 자체를 계산하지 않고 `reason`으로 이유를 밝힌다
(§5.26/§5.30의 "표본 부족은 정직하게 표시" 관례). `baseline_days`만큼 데이터가
없어도(수집을 최근에 시작해 이력이 짧음) 있는 만큼(`baseline_days_used`)만
쓰고 이 사실을 결과에 그대로 노출한다 — 침묵하지 않는다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SectorFlow

# §5.23/§5.19와 동일한 20거래일 윈도우(새 값 발명하지 않음).
BASELINE_WINDOW_DAYS = 20

# 베이스라인을 계산하는 데 필요한 최소 과거 거래일 수 — 이보다 적으면 "평균"이라는
# 말 자체가 무의미해 아예 계산을 포기하고 reason으로 알린다(모듈 docstring
# "표본 부족 시 정직한 실패" 참고). 20일 전부를 요구하지 않는 이유는 수집 시작
# 초기(§5.33-1 도입 직후)에도 부분적으로나마 관찰치를 보여주기 위함.
MIN_BASELINE_DAYS = 5

# ka10051 inds_netprps 중 "산업 업종"이 아니라 시가총액 규모별/지수구성별
# 분류인 inds_cd (실측 2026-07-28, 모듈 docstring "진짜 업종만 랭킹 대상" 참고).
NON_INDUSTRY_SECTOR_CODES: frozenset[str] = frozenset(
    {
        # 코스피 — 시가총액 규모별 분류(대형주/중형주/소형주).
        "002_AL",
        "003_AL",
        "004_AL",
        # 코스닥 — 지수 구성종목 집합(KOSDAQ 100/MID 300/SMALL/우량기업/
        # 벤처기업/중견기업/신성장기업/150/글로벌지수).
        "138_AL",
        "139_AL",
        "140_AL",
        "142_AL",
        "143_AL",
        "144_AL",
        "145_AL",
        "150_AL",
        "151_AL",
    }
)

DEFAULT_TOP_N = 5


async def _sector_signed_series(
    session: AsyncSession, market: str
) -> tuple[dict[str, dict[dt.date, int]], dict[str, str]]:
    """``market``의 sector_flow 전체를 sector_code -> {date: signed_net_value}로
    모은다(NON_INDUSTRY_SECTOR_CODES는 제외, 모듈 docstring 참고).
    signed_net_value = frgnr_net_value + orgn_net_value(외국인+기관계, 둘 다
    NULL이면 그 (sector, date)는 시리즈에서 제외 — 0으로 채우면 진짜 0과
    "데이터 없음"이 구분 안 된다, §5.25/§5.19의 공통 관례).

    Returns: (sector_code -> {date: signed}, sector_code -> sector_name 최신값).
    """
    stmt = select(
        SectorFlow.sector_code,
        SectorFlow.sector_name,
        SectorFlow.date,
        SectorFlow.frgnr_net_value,
        SectorFlow.orgn_net_value,
    ).where(
        SectorFlow.market == market,
        SectorFlow.sector_code.notin_(NON_INDUSTRY_SECTOR_CODES),
    )
    rows = (await session.execute(stmt)).all()

    by_sector: dict[str, dict[dt.date, int]] = {}
    names: dict[str, str] = {}
    for code, name, date, frgnr, orgn in rows:
        if frgnr is None and orgn is None:
            continue
        by_sector.setdefault(code, {})[date] = (frgnr or 0) + (orgn or 0)
        if name:
            names[code] = name
    return by_sector, names


async def compute_sector_rotation(
    session: AsyncSession,
    market: str,
    baseline_days: int = BASELINE_WINDOW_DAYS,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """``market``(kospi/kosdaq)의 업종별 자금 흐름을 각 업종 자신의 최근
    ``baseline_days``거래일 평소 활동량 대비 배율로 정규화해 유입/유출 랭킹과
    "순환 vs 이탈" 보조 통계를 반환한다(모듈 docstring 참고 — 판정 없이 숫자만).

    Returns (표본 부족):
        ``{"market", "date": None, "reason": str, "gainers": [], "losers": [],
        "aggregate": None}``.

    Returns (정상):
        ``{"market", "date": iso, "baseline_days_requested": int,
        "baseline_days_used": int, "sector_count": int,
        "gainers": [...], "losers": [...],
        "aggregate": {"today_net_value", "baseline_signed_avg",
        "aggregate_delta", "gaining_sum", "losing_sum"}}``.
        gainers/losers 각 원소: ``{"sector_code", "sector_name",
        "today_net_value", "baseline_activity", "baseline_signed_avg",
        "multiple", "delta"}``. gainers는 multiple 내림차순(양수만) 상위
        ``top_n``, losers는 multiple 오름차순(음수만) 상위 ``top_n``.
    """
    by_sector, names = await _sector_signed_series(session, market)
    if not by_sector:
        return {
            "market": market,
            "date": None,
            "reason": "sector_flow 데이터가 없음",
            "gainers": [],
            "losers": [],
            "aggregate": None,
        }

    all_dates = sorted({d for series in by_sector.values() for d in series})
    if len(all_dates) < MIN_BASELINE_DAYS + 1:
        return {
            "market": market,
            "date": None,
            "reason": (
                f"거래일 {len(all_dates)}개 — 베이스라인 최소 표본"
                f"({MIN_BASELINE_DAYS}일)+오늘을 채우기에 데이터 부족"
            ),
            "gainers": [],
            "losers": [],
            "aggregate": None,
        }

    target_date = all_dates[-1]
    # 오늘(target_date)을 포함하지 않는 직전 baseline_days개 거래일 — 있는 만큼만
    # 쓰고 실제 사용한 일수(baseline_days_used)를 그대로 노출한다(모듈 docstring
    # "표본 부족 시 정직한 실패" 참고).
    prior_dates = all_dates[:-1]
    baseline_dates = prior_dates[-baseline_days:]
    baseline_days_used = len(baseline_dates)

    results: list[dict] = []
    for code, series in by_sector.items():
        today_signed = series.get(target_date)
        if today_signed is None:
            continue
        baseline_values = [series[d] for d in baseline_dates if d in series]
        if len(baseline_values) < MIN_BASELINE_DAYS:
            continue
        baseline_activity = sum(abs(v) for v in baseline_values) / len(baseline_values)
        if baseline_activity <= 0:
            continue
        baseline_signed_avg = sum(baseline_values) / len(baseline_values)
        results.append(
            {
                "sector_code": code,
                "sector_name": names.get(code, code),
                "today_net_value": today_signed,
                "baseline_activity": round(baseline_activity, 1),
                "baseline_signed_avg": round(baseline_signed_avg, 1),
                "multiple": round(today_signed / baseline_activity, 2),
                "delta": round(today_signed - baseline_signed_avg, 1),
                "baseline_n": len(baseline_values),
            }
        )

    gainers = sorted(
        (r for r in results if r["multiple"] > 0), key=lambda r: r["multiple"], reverse=True
    )[:top_n]
    losers = sorted((r for r in results if r["multiple"] < 0), key=lambda r: r["multiple"])[
        :top_n
    ]

    gaining_sum = sum(r["today_net_value"] for r in results if r["today_net_value"] > 0)
    losing_sum = sum(r["today_net_value"] for r in results if r["today_net_value"] < 0)
    aggregate_today = sum(r["today_net_value"] for r in results)
    aggregate_baseline = sum(r["baseline_signed_avg"] for r in results)

    return {
        "market": market,
        "date": target_date.isoformat(),
        "baseline_days_requested": baseline_days,
        "baseline_days_used": baseline_days_used,
        "sector_count": len(results),
        "gainers": gainers,
        "losers": losers,
        "aggregate": {
            "today_net_value": aggregate_today,
            "baseline_signed_avg": round(aggregate_baseline, 1),
            "aggregate_delta": round(aggregate_today - aggregate_baseline, 1),
            "gaining_sum": gaining_sum,
            "losing_sum": losing_sum,
        },
    }
