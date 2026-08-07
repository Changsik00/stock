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

재료는 전부 이미 수집·적재된 것만 쓴다(§5.2 지시 "신규 수집 불필요") — 아래
5개 요소로 단순하게(과설계 금지):

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
5. ``flow_net_value`` — **PLAN.md §5.20 추가(2026-07-23)**: 오늘 외국인+기관계
   순매수 합(``routers/stocks.py``의 ka10059 파서를 재사용해 ``collectors/
   live_refresh.py::_run_stock_flow_scan``이 10분마다 후보군 전체를 순회 조회,
   ``stock_flow`` 테이블에 upsert). 사용자가 "알테오젠 수급 들어오는 거 같은데
   이런 종목 찾는 법 없나"라고 물은 게 이 요소의 직접 계기다 — §5.15~§5.19가
   시장 전체 수급은 공들여 분석해 왔는데 정작 이 스켈핑 스코어엔 개별 종목
   수급이 전혀 반영되지 않고 있었다(등락률·회전율·거래대금 순위·관심순위 4개
   전부 "가격/관심"만 보고 "누가 사는지"는 안 봄). **1~3번과 달리 abs()를 쓰지
   않고 부호 있는 값 그대로 z-score화한다** — 등락률은 스켈핑 관점에서 상승이든
   하락이든 "변동성 자체"가 기회라 방향이 무의미하지만, 수급은 "누가 사는지
   판다"의 방향 자체가 관찰하려는 대상이라(외국인/기관 매수 유입 vs 유출은
   반대 신호) 부호를 죽이면 안 된다 — 순매도가 큰 종목이 순매수가 큰 종목과
   똑같이 높은 점수를 받는 걸 막기 위함.

1~3번, 5번은 스케일이 서로 달라(등락률 %, 회전율 %, 순위 1..N, 수급 백만원 단위)
그대로 더할 수 없으므로 후보군(candidates) 안에서 각각 z-score(평균 0,
표준편차 1로 표준화)로 정규화한 뒤 가중합한다. 4번(불리언)은 z-score가 아니라
"대략 1 표준편차에 해당하는" 고정 가산점(``ATTENTION_BONUS``)을 더한다 — 다른
요소들의 z-score 분포와 크기 자릿수를 맞추기 위함(별도 정규화 없이도 과도하게
튀지 않도록).

가중치는 사용자가 반복 강조한 우선순위(수급이 이번 요청의 핵심 재료)를 반영해
``flow``를 가장 높게 두고 나머지를 재배분했다:
``{"change": 0.20, "turnover": 0.20, "value_rank": 0.10, "attention": 0.10,
"flow": 0.40}`` (합 1.0). **이 가중치는 검증된 값이 아니라 첫 배정이다** —
§5.15의 시장 단위 가중치는 백테스트로 정했지만, 종목 단위 "수급 유입 →
이후 수익률" 관계는 아직 실증하지 않았다. 이미 있는 ``scalp_pick``/
``scalp_tracker``(§5.7 — 진입 시점·이후 5/15/30/60분·EOD change_rate를 그대로
기록) 메커니즘이 그대로 이 새 가중치의 사후 검증 근거가 된다 — 표본이 쌓이면
재검토한다. 최종 점수는 상대 순위용이라 절대 스케일에 의미를 두지 않는다 —
후보군이 바뀌면(장중 재조회) 같은 종목도 값이 달라질 수 있다.

후보군 자체(어떤 종목을 스코어링 대상으로 삼을지)는 이 모듈이 정하지 않는다
— 호출부가 거래대금 상위 스냅샷(value-rank/live)에서 이미 "돈이 몰리는 곳"
상위 종목만 추려 넘겨준다는 전제다(ETF 제외는 호출부 책임, §5.2 "ETF는
제외(개별주만)"). **PLAN.md §5.27(2026-07-24)** — 가격제한폭 근접 종목의
구조적 배제(``abs(change_rate) >= 29.5``) 역시 같은 이유로 호출부
(``routers/scalp.py``의 ``PRICE_LIMIT_THRESHOLD_PCT``)의 책임이다. 이 모듈은
그 필터를 통과한 후보만 받는다는 전제라 여기엔 배제 로직이 없다.

## at_risk 플래그 (PLAN.md §5.27-2, 2026-07-24)

큰 폭 하락(``change_rate <= LARGE_DECLINE_WARNING_PCT``, 기본 -15.0%)인 후보는
결과에 ``at_risk: True``가 붙는다. 사용자 지적("하한가 혹은 마이너스 폭이 큰
녀석은 위험한 상태로 볼 수 있음")대로, 상승과 하락은 "변동성 크기"(1번 요소가
``abs()``로 다루는 것)는 같아도 리스크 성격이 다르다 — 그렇다고 스코어 산식
자체를 방향에 따라 다시 설계하면 과설계고, 이미 검증되지 않은 §5.20 가중치를
또 건드리는 셈이다. 그래서 **``score`` 계산에는 전혀 관여하지 않는 순수
정보성 필드**로만 추가한다 — ``flow_net_value``/``turnover``가 스코어 산식에
녹아들면서도 동시에 원본 값 그대로 응답에 노출돼 사용자가 근거를 직접 보는
것과 같은 위치다. 다만 ``flow_net_value``와 달리 ``at_risk``는 스코어에
반영되지 않는 순수 파생 불리언이라는 점이 다르다.
"""

from __future__ import annotations

from typing import Any, TypedDict

# 가중치 합은 1.0이어야 한다(문서화된 산식과 일치 — compute_scalp_scores가
# 이를 전제로 검산하지는 않지만, 값을 바꿀 때는 합을 유지해야 한다).
# PLAN.md §5.20(2026-07-23) — flow(종목별 당일 외국인+기관 순매수) 추가, 가중치 재배분.
WEIGHTS: dict[str, float] = {
    "change": 0.20,
    "turnover": 0.20,
    "value_rank": 0.10,
    "attention": 0.10,
    "flow": 0.40,
}

# 관심순위 편입 가산점 — z-score 단위(대략 1 표준편차)에 맞춘 고정값.
ATTENTION_BONUS = 1.0

# PLAN.md §5.27-2(2026-07-24) — 이 값 이하(큰 폭 하락)면 at_risk 플래그만
# 추가하고 score 계산에는 관여하지 않는다. routers/scalp.py에도 동일 값의
# LARGE_DECLINE_WARNING_PCT 상수가 있다(의도적 중복) — 이 모듈은 DB/네트워크는
# 물론 다른 앱 모듈에도 의존하지 않는 순수 계산부라는 게 위 모듈 docstring이
# 명시한 설계 원칙이라, screener.py가 routers.scalp를 import하는 방향은 상위
# 계층을 하위 계층이 참조하는 역전이라 피한다. 두 값이 어긋나면 안 되므로
# 값을 바꿀 때는 항상 두 파일을 함께 수정해야 한다.
LARGE_DECLINE_WARNING_PCT = -15.0


class ScalpCandidate(TypedDict, total=False):
    code: str
    name: str
    market: str | None
    change_rate: float | None
    turnover: float | None
    value_rank: int
    flow_net_value: int | None


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
    ``flow_net_value``도 None이면(그 종목이 아직 ``_run_stock_flow_scan`` 스윕을
    못 돈 경우) 0.0 중립으로 취급한다 — 동일한 관례(PLAN.md §5.20).

    반환 원소는 입력 dict를 그대로 복사한 뒤 ``in_attention_top``(bool),
    ``score``(round 3자리), ``at_risk``(bool, PLAN.md §5.27-2 — change_rate가
    ``LARGE_DECLINE_WARNING_PCT`` 이하인 큰 폭 하락 종목 표시, score에는 영향
    없는 순수 정보성 필드)를 추가한 것 — 정렬은 score 내림차순, 동점이면
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
    # PLAN.md §5.20 — abs()를 쓰지 않는다(모듈 docstring "스코어 산식" 5번 참고):
    # 수급은 방향(순매수 vs 순매도)이 관찰 대상 그 자체라 부호를 죽이면 안 된다.
    flow_values = [c["flow_net_value"] if c.get("flow_net_value") is not None else 0.0 for c in candidates]

    z_change = _zscores(abs_changes)
    z_turnover = _zscores(turnovers)
    z_rank = _zscores(inverted_ranks)
    z_flow = _zscores(flow_values)

    scored: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        in_attention = c["code"] in attention_codes
        score = (
            w["change"] * z_change[i]
            + w["turnover"] * z_turnover[i]
            + w["value_rank"] * z_rank[i]
            + w["attention"] * (ATTENTION_BONUS if in_attention else 0.0)
            + w["flow"] * z_flow[i]
        )
        # PLAN.md §5.27-2 — score 계산과 무관한 순수 정보성 플래그. change_rate가
        # None(데이터 없음)이면 "위험"과 다른 의미이므로 False로 둔다(제한폭
        # 배제 필터의 None 처리와 동일한 관례, routers/scalp.py 참고).
        at_risk = c.get("change_rate") is not None and c["change_rate"] <= LARGE_DECLINE_WARNING_PCT
        scored.append(
            {**c, "in_attention_top": in_attention, "score": round(score, 3), "at_risk": at_risk}
        )

    scored.sort(key=lambda r: (-r["score"], r["value_rank"]))
    return scored


# -- 개인 매도 / 외국인·기관 전환 매집 관찰 패턴 --------------------------------
#
# 사용자가 유한양행(000100) 실 데이터에서 발견한 패턴이 계기다: 개인이 계속
# 순매도하는데 외국인/기관이 순매수로 전환하며 가격이 급등 없이 조용히
# 우상향하는 종목들("개인은 모르고 파는데 프로그램/외국인·기관이 미리 걸린
# 매도 물량을 받아먹는" 패턴) — 1~2% 스윙을 위한 "관찰용 스크리너"다(매매
# 신호 아님, 자동매매 아님). house rule(§5) 그대로: "판단"을 새로 만들지
# 않고 이미 수집 중인 데이터를 명시적 숫자 규칙에 매핑할 뿐이다.
#
# 아래 4개 상수는 첫 배정값이며(이 모듈의 WEIGHTS/LARGE_DECLINE_WARNING_PCT와
# 동일한 관례로) 이름 붙은 상수로 유지한다 — 검증된 값이 아니라 관찰을 시작하기
# 위한 초기 컷오프다.
LOOKBACK_TRADING_DAYS = 10
RECENT_HALF_DAYS = 5  # 최근 절반(전환/가속 판정용) — 나머지 5일이 "직전 절반"
PRICE_QUIET_RISE_MIN_PCT = 2.0
PRICE_QUIET_RISE_MAX_PCT = 15.0
MAX_SINGLE_DAY_ABS_MOVE_PCT = 5.0


def evaluate_accumulation_pattern(
    individual_net_10d: float,
    foreign_inst_net_recent5d: float,
    foreign_inst_net_prior5d: float,
    price_return_10d_pct: float,
    max_abs_daily_return_10d_pct: float,
) -> dict:
    """관찰용 패턴 판정 — 매매 신호 아님. 아래 3개 조건을 전부 충족해야
    ``matched=True``:

    1. 개인 10거래일 누적 순매도(``individual_net_10d < 0``).
    2. 외국인+기관계 최근 5거래일 순매수(``foreign_inst_net_recent5d > 0``)이면서
       그 전 5거래일보다 개선(``foreign_inst_net_recent5d > foreign_inst_net_prior5d``)
       — 전환/가속 판정.
    3. 10거래일 누적 등락률이 완만한 상승 구간(``PRICE_QUIET_RISE_MIN_PCT`` 이상
       ``PRICE_QUIET_RISE_MAX_PCT`` 이하, 양쪽 경계 포함)이면서 그 구간 어떤
       하루도 급등락이 없음(``max_abs_daily_return_10d_pct`` <=
       ``MAX_SINGLE_DAY_ABS_MOVE_PCT``).

    인자 단위는 호출부(collectors/accumulation_screener.py)가 넘기는 그대로다
    — ``*_net_*`` 3개는 stock_flow.net_value와 동일한 백만원 단위,
    ``*_pct`` 2개는 %.

    Returns ``{"matched": bool, "reason": str}`` — ``reason``은 조건 3개 각각의
    충족/불충족 여부와 실제 관측값을 그대로 서술한 문장이다(§5 house rule
    "관찰 기록에 좋다/추천 같은 평가 문구를 쓰지 않는다" — 평가 문구 없음).
    """
    cond1 = individual_net_10d < 0
    cond2 = foreign_inst_net_recent5d > 0 and foreign_inst_net_recent5d > foreign_inst_net_prior5d
    cond3 = (
        PRICE_QUIET_RISE_MIN_PCT <= price_return_10d_pct <= PRICE_QUIET_RISE_MAX_PCT
        and max_abs_daily_return_10d_pct <= MAX_SINGLE_DAY_ABS_MOVE_PCT
    )
    matched = cond1 and cond2 and cond3

    reason = (
        f"개인 10일 순매도 {individual_net_10d:,.0f}백만"
        f"({'조건1 충족' if cond1 else '조건1 불충족'}: individual_net_10d < 0). "
        f"외국인+기관계 최근5일 순매수 {foreign_inst_net_recent5d:,.0f}백만, "
        f"직전5일 {foreign_inst_net_prior5d:,.0f}백만"
        f"({'조건2 충족' if cond2 else '조건2 불충족'}: 최근5일 > 0 AND 최근5일 > 직전5일). "
        f"10일 누적 등락률 {price_return_10d_pct:.2f}%, 일중 최대 등락률(절대값) "
        f"{max_abs_daily_return_10d_pct:.2f}%"
        f"({'조건3 충족' if cond3 else '조건3 불충족'}: "
        f"{PRICE_QUIET_RISE_MIN_PCT}~{PRICE_QUIET_RISE_MAX_PCT}% 구간, "
        f"일중 최대 등락폭 {MAX_SINGLE_DAY_ABS_MOVE_PCT}% 이하)."
    )
    return {"matched": matched, "reason": reason}
