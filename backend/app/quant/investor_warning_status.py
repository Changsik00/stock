"""종목의 현재 투자주의/투자경고/투자위험 지정 상태 판정 (PLAN.md §5.39).

**§5.36 risk_alert.py와 왜 분리했는가(설계 판단, 문서화)**: risk_alert.py의
``classify_market_risk``는 코스피/코스닥/선물 "지수" 3개를 입력받아 시장
전체 배너 하나를 만드는 구조다. 이번 지정 제도는 서킷브레이커/사이드카와
같은 "KRX 공식 제도"라는 점은 같지만, 판정 단위가 지수가 아니라 **개별
종목**이라 그 함수 시그니처(지수 3개 dict)에 끼워 넣을 자리가 없다 — 전체
시장 상단 배너에 "지금 이 순간 지정 상태인 개별 종목 목록"을 나열하는 건
그 배너의 원래 목적(시장 전체 급변 경보)과 다른 성격의 정보이고, 사용자
입장에서도 "내가 지금 보고 있는 종목"에 대한 배지로 보는 게 더 자연스럽다.
그래서 이 모듈은 risk_alert.py와 별도로 두고, 소비처도 시장 배너가 아니라
종목 상세(``routers/stocks.py::stock_series``)로 잡았다(PLAN.md §5.39 작업
지시의 "판단해서 결정, 문서화" 요청에 대한 답).

**정직성 원칙은 §5.36과 동일하게 유지한다**: 이 지정들은 KRX가 실제로
발표한 공식 사실이므로 있는 그대로 서술("현재 투자경고종목으로 지정되어
있음")하되, 향후 주가 방향에 대한 어떤 암시도 하지 않는다 — "위험하니
팔아라/피해라" 같은 매매 신호로 읽히지 않게, 소비하는 라우터/프런트 문구도
사실 서술에 그쳐야 한다.

**tier별 "활성" 정의가 다르다(클라이언트 모듈 docstring과 동일한 근거)**:
투자경고/투자위험은 "지정 -> 유지 -> 해제"의 기간을 갖는 상태라
``released_date is None``이면 현재도 지정 중이라고 판단할 수 있다. 반면
투자주의는 "그날 하루" 통보라 해제 개념이 없어(항상 ``released_date is
None``) 같은 규칙을 적용하면 몇 달 전 지정도 "활성"으로 잘못 판정하게 된다
— 그래서 caution은 "이 종목의 가장 최근 caution 지정일이, DB에 있는 caution
데이터 전체의 가장 최근 날짜(=사실상 최신 수집일)와 같은가"로 판정한다
(``caution_as_of`` 인자, 호출자가 전역 MAX(designated_date)를 구해 넘긴다
— 이 모듈은 DB를 모르는 순수 함수라 그 값을 받기만 한다).

**우선순위**: risk > warning > caution — 셋 다 활성이면 가장 심각한 tier
하나만 헤드라인으로 반환한다(risk_alert.py가 alerts를 severity로 정렬해
topAlert 하나를 헤드라인으로 쓰는 것과 같은 취지, 다만 여긴 애초에 종목 하나당
동시 활성 tier가 실무적으로 겹치는 일이 드물다 — KRX 운영상 위험 지정되면
경고 지정은 보통 해제된다).
"""

from __future__ import annotations

import datetime as dt

_TIER_PRIORITY = ("risk", "warning", "caution")
_TIER_LABEL = {"risk": "투자위험종목", "warning": "투자경고종목", "caution": "투자주의종목"}


def _latest_dated_row(rows: list[dict], tier: str) -> dict | None:
    """tier의 released_date is None인 행 중 designated_date가 가장 최근인 것.
    (warning/risk 전용 — "현재 지정 유지 중"인 행을 고른다.)"""
    candidates = [
        r for r in rows if r["tier"] == tier and r.get("released_date") is None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r["designated_date"])


def _latest_caution_row(rows: list[dict], caution_as_of: dt.date | None) -> dict | None:
    """caution 전용 — 이 종목의 최근 caution 지정일이 caution_as_of(전역 최신
    caution 데이터 날짜)와 같을 때만 "지금도 유효"로 본다(모듈 docstring
    "tier별 '활성' 정의가 다르다" 절 참고). caution_as_of가 None(caution 데이터
    자체가 아직 없음)이면 판정 불가로 취급한다."""
    if caution_as_of is None:
        return None
    candidates = [r for r in rows if r["tier"] == "caution"]
    if not candidates:
        return None
    latest = max(candidates, key=lambda r: r["designated_date"])
    if latest["designated_date"] != caution_as_of:
        return None
    return latest


def classify_investor_warning_status(
    rows: list[dict], caution_as_of: dt.date | None = None
) -> dict:
    """한 종목의 investor_warning_event 행 목록(``[{"tier", "market",
    "warning_type", "notice_date", "designated_date", "released_date"}, ...]``,
    이미 DB에서 이 종목 것만 걸러 온 것)으로 "지금" 상태를 판정한다. DB 접근
    없는 순수 함수(§5.17 compute/collect 분리 관례).

    Returns::

        {"active_tier": "risk"|"warning"|"caution"|None,
         "label": str|None,             # 활성일 때만 "투자위험종목" 등
         "market": str|None,
         "designated_date": date|None,
         "warning_type": str|None,      # active_tier="caution"일 때만
         "evaluable": bool}             # rows 자체가 비어 있으면 False

    ``rows``가 빈 리스트면(이 종목이 investor_warning_event에 한 번도 등장한
    적 없음) ``evaluable=False``, ``active_tier=None``을 반환한다 — "지정된
    적이 한 번도 없어 항상 정상"과 "데이터가 아예 없어 판정 불가"를 구분하지
    않는 것처럼 보일 수 있지만, 이 소스는 수집기가 매일 갱신하는 전수 스캔이라
    "행이 없다 = 최근 lookback 구간에 지정 이력이 없다"가 곧 "정상"과 사실상
    동일하다(§5.36의 kospi/kosdaq처럼 라이브 호출 실패로 데이터가 통째로
    비는 경우와는 다른 성격) — 그래도 필드 이름 자체는 §5 house rule에 맞춰
    ``evaluable``로 남겨 향후 호출부가 필요하면 구분할 수 있게 한다.
    """
    if not rows:
        return {
            "active_tier": None,
            "label": None,
            "market": None,
            "designated_date": None,
            "warning_type": None,
            "evaluable": False,
        }

    for tier in _TIER_PRIORITY:
        if tier == "caution":
            row = _latest_caution_row(rows, caution_as_of)
        else:
            row = _latest_dated_row(rows, tier)
        if row is not None:
            return {
                "active_tier": tier,
                "label": _TIER_LABEL[tier],
                "market": row.get("market"),
                "designated_date": row["designated_date"],
                "warning_type": row.get("warning_type") if tier == "caution" else None,
                "evaluable": True,
            }

    return {
        "active_tier": None,
        "label": None,
        "market": None,
        "designated_date": None,
        "warning_type": None,
        "evaluable": True,
    }
