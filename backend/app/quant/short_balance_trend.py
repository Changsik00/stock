"""공매도 잔고 추이(숏커버링 감지) — 범용 종목 (PLAN.md §5.81).

사용자가 유튜브 영상(youtu.be/cqY8ajo6T-I, yt-dlp로 자막 확보해 실제 내용
확인)에서 본 분석 방식 중 채널 홍보는 배제하고 방법론만 가져왔다 — 영상은
"자사주 매입 잔여 실탄 vs 공매도 잔고 추이"를 함께 봤지만, 자사주 매입
공시는 DART 연동(신규 API 키 필요)이 필요해 이번 범위에서 뺐다(§5.81
AskUserQuestion으로 확인). 이 모듈은 그중 이미 데이터가 있는 **공매도 잔고
추이** 절반만 다룬다.

**이 모듈도 판단 문구를 만들지 않는다**(house rule §5) — "숏커버링 국면일
수 있음" 같은 서술은 영상이 쓴 용어를 그대로 관찰 라벨로만 쓸 뿐, "사라/
팔라" 지시는 없다.

## 중요한 제약 — 순보유잔고는 다수 종목에 아예 없다

`ShortSellingStock.balance_qty`(모델 docstring 참고)는 "보고의무 발생
종목만 T+2 지연으로 채워지고, 그 외엔 NULL"이다 — 즉 데이터가 없다고
"공매도가 없다"는 뜻이 아니라 "이 종목이 보고 대상이 아니다"일 뿐이다.
이 모듈은 유효한 값이 2개 미만이면 정직하게 `insufficient_data`를
반환한다(`quant/volume_profile.py`의 "표본 부족 시 정직한 실패" 관례와
동일).
"""

from __future__ import annotations

from typing import Any


def _sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _direction_label(sign: int) -> str:
    return {1: "up", -1: "down", 0: "flat"}[sign]


def evaluate_short_balance_trend(balance_history: list[dict]) -> dict[str, Any]:
    """`balance_history`: `routers/stocks.py::_read_short_selling` 출력 그대로
    (오름차순, ``{"date","volume","value","balance_qty","balance_value"}``).

    Returns 유효 데이터 2개 미만이면 ``{"insufficient_data": True, "reason": ...}``.
    그 외엔 ``{"insufficient_data": False, "current_direction",
    "consecutive_days_same_direction", "peak", "days_since_peak",
    "pct_off_peak", "phase", "limitations"}``.
    """
    rows = [r for r in balance_history if r.get("balance_qty") is not None]
    if len(rows) < 2:
        return {
            "insufficient_data": True,
            "reason": "보고의무 발생 종목이 아니거나(순보유잔고 비공개) 유효 데이터가 부족함",
        }

    diffs = [_sign(rows[i]["balance_qty"] - rows[i - 1]["balance_qty"]) for i in range(1, len(rows))]
    current_sign = diffs[-1]
    streak = 0
    if current_sign != 0:
        for s in reversed(diffs):
            if s == current_sign:
                streak += 1
            else:
                break

    peak_row = max(rows, key=lambda r: r["balance_qty"])
    peak_index = rows.index(peak_row)
    today_row = rows[-1]

    days_since_peak = len(rows) - 1 - peak_index
    pct_off_peak = None
    if peak_row["balance_qty"]:
        pct_off_peak = round(
            (today_row["balance_qty"] - peak_row["balance_qty"]) / peak_row["balance_qty"] * 100, 4
        )

    direction = _direction_label(current_sign)
    off_peak_meaningfully = pct_off_peak is not None and pct_off_peak < -1.0 and days_since_peak > 0
    if direction == "up" and streak >= 2:
        phase = "공매도 잔고 확대 중(신규 공매도 유입 가능성)"
    elif direction == "down" and streak >= 2 and off_peak_meaningfully:
        phase = "공매도 잔고 감소 중(숏커버링 국면일 수 있음)"
    elif direction == "down" and streak >= 2:
        phase = "공매도 잔고 소폭 감소 중"
    else:
        phase = "방향 불분명(횡보 또는 데이터 변동 미미)"

    return {
        "insufficient_data": False,
        "today_date": today_row["date"],
        "today_balance_qty": today_row["balance_qty"],
        "current_direction": direction,
        "consecutive_days_same_direction": streak,
        "peak": {"date": peak_row["date"], "balance_qty": peak_row["balance_qty"]},
        "days_since_peak": days_since_peak,
        "pct_off_peak": pct_off_peak,
        "phase": phase,
        "limitations": [
            "순보유잔고(balance_qty)는 보고의무 발생 종목만 T+2 지연으로 공개됨 — "
            "이 값이 없다고 공매도가 없다는 뜻은 아님",
        ],
    }
