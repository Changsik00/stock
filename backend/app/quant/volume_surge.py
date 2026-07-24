"""시장 전체 거래량 급증 감지 — PLAN.md §5.24.

**배경**: 사용자가 "거래대금(량)이 방금 많이 이 수치를 치고 올라왔다"를
못 느낀다고 지적("§5.24 사용자 요청" 절 전문 참고) — 대시보드가 스냅샷
숫자만 보여줄 뿐 "지금 막 거래량이 튀었는지"는 어디서도 알 수 없었다.
추세 전환(바닥/반등) 감지는 §5.15/§5.23처럼 백테스트를 먼저 거쳐야 하는
더 큰 작업이라 이번 범위에서 제외하고, 사용자가 먼저 선택한 "시장 전체
(코스피/코스닥) 거래량 급증 감지"만 다룬다.

**새 외부 호출·DB 테이블 없음**: `routers/markets.py::_index_tile_from_intraday`가
대시보드 지수 타일용으로 이미 60초마다(`collectors/live_refresh.py`의
`_warm_index_tiles_live`) ka20005 1분봉(`_warm_market_intraday`)을 받아오면서
`bars[-1]["close"]`만 쓰고 각 봉의 `volume`(그 1분간 거래량,
`clients/kiwoom.py::parse_minute_chart_rows`가 `trde_qty`로 이미 파싱해 둔
필드)은 버리고 있었다 — §5.21(K200 선물 잠정치)·§5.19(쏠림 비율 시총 보정)와
같은 "이미 fetch해 놓고 버려지던 필드를 재사용" 패턴을 그대로 반복한다. 이
모듈은 그 버려지던 `volume`을 받아 "최근 거래량이 방금 얼마나 튀었는지"를
계산하는 순수 함수만 제공한다(DB/세션 의존 없음 — `flow_acceleration.py`의
compute/collect 분리 관례와 동일하게 단위테스트 대상으로 독립시킨다).

**윈도우 선택**: §5.17 `flow_acceleration.py`가 이미 "최근 30분 vs 그 이전
30분" 비교 관례를 확립해 뒀다 — 이 모듈도 새 윈도우 개념을 발명하지 않고
같은 철학을 따른다. 다만 "급증"은 가속도보다 훨씬 짧은 시간에 나타나는
현상이라(거래량은 특정 이벤트 발생 후 1~5분 안에 튀어 오르는 경우가 흔함)
비교 기준(baseline)은 flow_acceleration과 동일하게 30분을 쓰되, "방금"에
해당하는 recent 구간은 5분으로 짧게 잡는다 — recent_minutes=5(방금 튀었는지),
baseline_minutes=30(평소 수준, §5.17과 통일).

**데이터 부족·0-나눗셈 방어**: `flow_acceleration.py`와 동일하게 "세 시점 중
하나라도 없으면 억지로 계산하지 않고 None"이라는 철학을 따른다 — 여기서는
"recent+baseline 구간을 채울 만큼 봉이 쌓이지 않았으면(예: 장 시작 09:00
직후, 아직 35분이 지나지 않음) None"으로 반영한다. 또한 baseline_avg가
0 이하(데이터 이상 또는 실제로 그 구간 거래량이 0)이면 배율 계산이 무의미
(0-나눗셈 또는 무한대)해지므로, §5.18/§5.19에서 이미 확립한 "정규화 기준이
없으면 억지로 채우지 않는다" 원칙 그대로 None을 반환한다.
"""

from __future__ import annotations


def compute_volume_surge(
    bars: list[dict], recent_minutes: int = 5, baseline_minutes: int = 30
) -> dict | None:
    """`bars`(오름차순, 과거->최신, 각 원소는 최소 `"volume"` 키를 가진 dict)의
    최근 `recent_minutes`분 평균 분당 거래량이 그 직전 `baseline_minutes`분
    평균 분당 거래량 대비 몇 배인지 계산한다(모듈 docstring 지표 정의 참고).

    `len(bars) < recent_minutes + baseline_minutes`이면(아직 두 구간을 채울
    만큼 봉이 쌓이지 않음) None. baseline 구간 평균 거래량이 0 이하이면(데이터
    이상 또는 실제 0 거래량) 배율이 무의미해지므로 역시 None.

    Returns ``{"recent_minutes": int, "baseline_minutes": int,
    "recent_avg_volume": float, "baseline_avg_volume": float, "multiple": float}``
    또는 ``None``.
    """
    if len(bars) < recent_minutes + baseline_minutes:
        return None

    recent = bars[-recent_minutes:]
    baseline = bars[-(recent_minutes + baseline_minutes) : -recent_minutes]

    def _volume(bar: dict) -> int:
        return int(bar.get("volume") or 0)

    recent_volumes = [_volume(b) for b in recent]
    baseline_volumes = [_volume(b) for b in baseline]

    recent_avg = sum(recent_volumes) / len(recent_volumes)
    baseline_avg = sum(baseline_volumes) / len(baseline_volumes)

    if baseline_avg <= 0:
        return None

    return {
        "recent_minutes": recent_minutes,
        "baseline_minutes": baseline_minutes,
        "recent_avg_volume": round(recent_avg, 1),
        "baseline_avg_volume": round(baseline_avg, 1),
        "multiple": round(recent_avg / baseline_avg, 2),
    }
