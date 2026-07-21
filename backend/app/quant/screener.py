"""스켈핑 스크리너 — 종목 선정 순수 계산부 (PLAN.md §5.2).

이 모듈은 DB/네트워크 무관 순수 함수만 담는다(단위테스트 대상,
tests/test_quant_screener.py) — 후보 종목 데이터 수집·조립은
routers/scalp.py가 담당한다(quant/signals.py·sentiment.py의 "계산부/조립부
분리" 패턴과 동일, collectors/flow_path.py의 compute/collect 분리 원류).

**원칙(§5 전체 원칙 그대로): 관찰 사실만 서술한다.** 여기서 만드는 ``score``는
"지금 거래대금·변동성·관심도가 몰려 있는 정도"를 나타내는 상대적 순위 지표일
뿐 매수/매도 판단이 아니다 — 호출부·프런트도 "참고용 스크리닝 — 매매 신호
아님"을 항상 병기해야 한다.

## 스코어 산식

재료는 전부 이미 수집·적재된 것만 쓴다(§5.2 지시 "신규 수집 불필요") — 4개
이하 요소로 단순하게(과설계 금지):

1. ``abs(change_rate)`` — 오늘 등락률의 크기(방향 무관, 스켈핑은 변동성 자체가
   기회이지 방향이 아니다).
2. ``turnover`` — 회전율(거래대금÷시가총액 %). 시가총액 대비 얼마나 활발히
   손바뀜하는지 — 스켈핑은 유동성이 핵심이라 거래대금 절대액보다 이 비율이
   더 적합하다.
3. ``value_rank`` — 거래대금 순위(1이 1등, 값이 작을수록 좋음) — "얼마나 많은
   돈이 이 종목에 몰려 있는지"의 절대 규모(회전율과는 독립적인 신호: 회전율은
   시총 대비 비율이라 소형주가 유리하게 왜곡될 수 있는데, 거래대금 순위가 이를
   보완한다).
4. ``in_attention_top`` — 키움 실시간 관심순위(조회수) TOP 편입 여부. 위 세
   요소가 전부 "장중 스냅샷 1회" 기준인 것과 달리, 이건 투자자들이 지금 이
   순간 실제로 보고 있는 종목이라는 별도 신호라 가산점으로만 반영한다.

1~3번은 스케일이 서로 달라(등락률 %, 회전율 %, 순위 1..N) 그대로 더할 수 없으므로
후보군(candidates) 안에서 각각 z-score(평균 0, 표준편차 1로 표준화)로 정규화한
뒤 가중합한다. 4번(불리언)은 z-score가 아니라 "대략 1 표준편차에 해당하는"
고정 가산점(``ATTENTION_BONUS``)을 더한다 — 다른 세 요소의 z-score 분포와
크기 자릿수를 맞추기 위함(별도 정규화 없이도 과도하게 튀지 않도록).

가중치는 등락률·회전율(변동성·유동성, 스켈핑의 핵심 재료)을 동일하게 가장
높게 두고, 거래대금 순위·관심순위는 보조 신호로 낮게 둔다:
``{"change": 0.35, "turnover": 0.35, "value_rank": 0.15, "attention": 0.15}``
(합 1.0). 최종 점수는 상대 순위용이라 절대 스케일에 의미를 두지 않는다 —
후보군이 바뀌면(장중 재조회) 같은 종목도 값이 달라질 수 있다.

후보군 자체(어떤 종목을 스코어링 대상으로 삼을지)는 이 모듈이 정하지 않는다
— 호출부가 거래대금 상위 스냅샷(value-rank/live)에서 이미 "돈이 몰리는 곳"
상위 종목만 추려 넘겨준다는 전제다(ETF 제외는 호출부 책임, §5.2 "ETF는
제외(개별주만)").
"""

from __future__ import annotations

from typing import Any, TypedDict

# 가중치 합은 1.0이어야 한다(문서화된 산식과 일치 — compute_scalp_scores가
# 이를 전제로 검산하지는 않지만, 값을 바꿀 때는 합을 유지해야 한다).
WEIGHTS: dict[str, float] = {"change": 0.35, "turnover": 0.35, "value_rank": 0.15, "attention": 0.15}

# 관심순위 편입 가산점 — z-score 단위(대략 1 표준편차)에 맞춘 고정값.
ATTENTION_BONUS = 1.0


class ScalpCandidate(TypedDict, total=False):
    code: str
    name: str
    market: str | None
    change_rate: float | None
    turnover: float | None
    value_rank: int


def _zscores(values: list[float]) -> list[float]:
    """평균 0, 표준편차 1로 표준화. 값이 전부 같으면(표준편차 0) 전부 0.0."""
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    stdev = variance**0.5
    if stdev == 0:
        return [0.0] * n
    return [(v - mean) / stdev for v in values]


def compute_scalp_scores(
    candidates: list[ScalpCandidate],
    attention_codes: set[str],
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """후보 종목 리스트에 스켈핑 적합도 ``score``를 매겨 내림차순으로 정렬해
    반환한다(모듈 docstring "스코어 산식" 참고).

    candidates 각 원소는 최소 ``code``, ``value_rank``(int, 거래대금 순위)를
    가져야 한다 — ``change_rate``/``turnover``는 None이면 0.0으로 취급한다
    (값이 없는 종목을 계산에서 빼면 z-score 분포 자체가 왜곡되므로, 대신
    "변동성/회전율 없음"을 중립값으로 반영). candidates가 비어 있으면 빈 리스트.

    반환 원소는 입력 dict를 그대로 복사한 뒤 ``in_attention_top``(bool)과
    ``score``(round 3자리)를 추가한 것 — 정렬은 score 내림차순, 동점이면
    value_rank 오름차순(거래대금이 더 많이 몰린 쪽을 우선).
    """
    if not candidates:
        return []

    w = weights if weights is not None else WEIGHTS

    abs_changes = [abs(c["change_rate"]) if c.get("change_rate") is not None else 0.0 for c in candidates]
    turnovers = [c["turnover"] if c.get("turnover") is not None else 0.0 for c in candidates]
    # 순위는 작을수록 좋음(1등이 최상위) -> 부호를 뒤집어 z-score화하면 "순위가
    # 좋을수록 z가 큼"이 되어 다른 요소들과 같은 방향으로 합산할 수 있다.
    inverted_ranks = [-float(c["value_rank"]) for c in candidates]

    z_change = _zscores(abs_changes)
    z_turnover = _zscores(turnovers)
    z_rank = _zscores(inverted_ranks)

    scored: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        in_attention = c["code"] in attention_codes
        score = (
            w["change"] * z_change[i]
            + w["turnover"] * z_turnover[i]
            + w["value_rank"] * z_rank[i]
            + w["attention"] * (ATTENTION_BONUS if in_attention else 0.0)
        )
        scored.append({**c, "in_attention_top": in_attention, "score": round(score, 3)})

    scored.sort(key=lambda r: (-r["score"], r["value_rank"]))
    return scored
