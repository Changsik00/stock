"""전일 대비 오늘 수급 소화 체크리스트 — 범용 종목 (PLAN.md §5.80).

사용자가 알테오젠 신주 상장 관련 유튜브 영상(yt-dlp로 자막 확보해 실제 내용
확인 후 진행)을 보고 "이걸 참고해서 전략을 만들어보자"고 제안해 만들었다.
영상의 핵심은 고정된 수치 규칙이 아니라 **"어제 큰 물량 이벤트(신주 상장 등)
이후, 오늘 가격·거래량·수급이 그 물량을 실제로 소화했는지 판정하는
체크리스트"**였다 — 신주 상장에 국한하지 않고 "전일 대비 오늘 수급 소화
여부"라는 더 일반적인 질문으로 설계한다(신주 상장이 가장 자연스러운 사용
사례일 뿐, 큰 하락 다음날/대량 매물 출회 다음날 등에도 그대로 적용 가능).

**이 모듈은 판단 문구를 만들지 않는다**(house rule §5) — 영상이 설명한
4분면 이름("매도세 약화"/"물량 부담 지속" 등)을 그대로 서술형 라벨로만
쓸 뿐, "사라/팔라" 지시는 없다. 이 도구가 만드는 어떤 값도 자동매매 주문
경로와 연결되지 않는다.

## 포함하지 않는 것 — 오전/오후 수급 분리

영상은 "외국인 순매수가 오전에 들어온 뒤 오후에도 누적이 유지되는지"까지
보라고 하지만, 이 프로젝트엔 신뢰 가능한 시간대별(장중) 투자자별 수급
데이터가 없다 — `clients/kiwoom.py::intraday_investor_trading`(ka10063)은
실호출은 됐지만 `invsr`(투자자구분) 코드의 실제 의미가 공식 문서로
확인되지 않은 미확정 상태다(그 클라이언트 메서드 docstring 참고). 신뢰할
수 없는 값을 조용히 계산해 숫자로 내놓느니 `limitations`에 명시하고
빼는 쪽을 택했다 — 이 대화에서 사용자에게 직접 "확인 불가"라고 답했던
것과 같은 태도.

## 업종 동반 여부도 자동 판별하지 않음

`GroupSnapshot`(업종/테마 일별 스냅샷)은 업종 단위 집계치만 있고 종목
멤버십 매핑이 없어 "이 종목이 어느 업종인지" DB로 알 수 없다 — 호출부가
`peer_changes`(비교하고 싶은 종목들의 오늘 등락률)를 직접 넘기면 그것과
비교해주는 정도로 범위를 좁혔다.
"""

from __future__ import annotations

from typing import Any

# 영상이 명시적으로 강조한 두 투자자 카테고리 — 나머지도 flows에 있으면
# investor_flow_continuity에 전부 포함하되, 이 둘을 항상 먼저 계산해 둔다.
_PRIMARY_INVESTORS = ("외국인", "기관계")


def _sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _direction_label(sign: int) -> str:
    return {1: "up", -1: "down", 0: "flat"}[sign]


def _prev_high_breakout(prev: dict, today: dict) -> dict:
    prev_high = prev.get("high")
    today_high = today.get("high")
    today_close = today.get("close")
    if prev_high is None or today_high is None or today_close is None:
        return {"broken": None, "holding_above": None, "prev_high": prev_high, "today_high": today_high}
    broken = today_high > prev_high
    holding_above = (today_close > prev_high) if broken else None
    return {
        "prev_high": prev_high,
        "today_high": today_high,
        "today_close": today_close,
        "broken": broken,
        "holding_above": holding_above,
    }


def _prev_close_battleground(prev: dict, today: dict) -> dict:
    prev_close = prev.get("close")
    today_close = today.get("close")
    today_low = today.get("low")
    today_high = today.get("high")
    if prev_close is None or today_close is None:
        return {"prev_close": prev_close, "today_close": today_close, "pct_from_prev_close": None}
    pct = round((today_close - prev_close) / prev_close * 100, 4) if prev_close else None
    range_crossed = None
    if today_low is not None and today_high is not None:
        range_crossed = today_low <= prev_close <= today_high
    return {
        "prev_close": prev_close,
        "today_close": today_close,
        "pct_from_prev_close": pct,
        "range_crossed_prev_close": range_crossed,
    }


# 영상이 설명한 4분면 그대로 — "이 관점에서는"이라는 서술이지 단정이 아니다.
_QUADRANT_LABELS = {
    ("down", "up"): "매도세 약화",
    ("up", "down"): "물량 부담 지속",
    ("down", "down"): "매수세 실종 우려",
    ("up", "up"): "강한 매수세 유입(변동성 자체가 큰 날일 수도 있음)",
}


def _volume_low_quadrant(prev: dict, today: dict, today_is_partial: bool) -> dict:
    prev_volume = prev.get("volume")
    today_volume = today.get("volume")
    prev_low = prev.get("low")
    today_low = today.get("low")
    if prev_volume is None or today_volume is None or prev_low is None or today_low is None:
        return {"quadrant": None, "label": None}

    volume_dir = _direction_label(_sign(today_volume - prev_volume))
    low_dir = _direction_label(_sign(today_low - prev_low))
    label = _QUADRANT_LABELS.get((volume_dir, low_dir), "거래량 또는 저가가 전일과 동일 — 판단 보류")

    return {
        "prev_volume": prev_volume,
        "today_volume": today_volume,
        "today_volume_is_partial": today_is_partial,
        "volume_direction": volume_dir,
        "prev_low": prev_low,
        "today_low": today_low,
        "low_direction": low_dir,
        "label": label,
    }


def _investor_flow_continuity(flows: dict[str, list[Any]]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    investors = list(_PRIMARY_INVESTORS) + [k for k in flows if k not in _PRIMARY_INVESTORS]
    for investor in investors:
        rows = flows.get(investor) or []
        if not rows:
            continue
        signs = [_sign(r.get("net_value")) for r in rows]
        current_sign = signs[-1]
        streak = 0
        if current_sign != 0:
            for s in reversed(signs):
                if s == current_sign:
                    streak += 1
                else:
                    break
        result[investor] = {
            "current_direction": _direction_label(current_sign),
            "consecutive_days_same_direction": streak,
            "recent_net_values": [r.get("net_value") for r in rows[-5:]],
        }
    return result


def _peer_comparison(today_change_rate: float | None, peer_changes: dict[str, float] | None) -> dict | None:
    if not peer_changes or today_change_rate is None:
        return None
    positive_peers = [c for c, v in peer_changes.items() if v > 0]
    label = (
        "동반 강세"
        if len(positive_peers) == len(peer_changes) and today_change_rate > 0
        else "개별 종목 수급에 가까움(peer 동반 아님)"
        if today_change_rate > 0
        else "peer도 약세 동반"
        if not positive_peers
        else "peer는 강세, 대상 종목만 약세"
    )
    return {
        "today_change_rate": today_change_rate,
        "peer_changes": peer_changes,
        "label": label,
    }


def evaluate_supply_absorption(
    prices: list[dict],
    flows: dict[str, list[dict]],
    peer_changes: dict[str, float] | None = None,
    market_is_open: bool = False,
) -> dict:
    """`prices`: `services.get_stock_series_from_db` 출력 그대로(오름차순,
    마지막 원소=오늘, 오늘이 장중이면 진행 중인 부분 봉). `flows`:
    `routers/stocks._read_flows` 출력 그대로(investor -> 날짜 오름차순 리스트).
    `peer_changes`: 비교하고 싶은 종목들의 ``{code: 오늘_등락률}``(선택).
    `market_is_open`: 호출 시점이 장중이면 True — 순수 함수라 시계에 직접
    접근하지 않고 호출부(`market_hours.is_market_closed`)가 판정해 넘긴다.
    오늘 거래량이 "아직 진행 중인 부분치"인지 표시하는 데만 쓰인다.

    prices가 2개 미만이면(오늘이 상장/재개 첫날 등, 비교할 "어제"가 없음)
    ``{"insufficient_data": True}``만 반환한다 — 정직한 실패
    (`quant/volume_profile.py`의 "표본 부족 시 정직한 실패" 관례와 동일).
    """
    if len(prices) < 2:
        return {"insufficient_data": True}

    prev, today = prices[-2], prices[-1]
    today_change_rate = today.get("changeRate")

    return {
        "insufficient_data": False,
        "prev_date": prev.get("date"),
        "today_date": today.get("date"),
        "prev_high_breakout": _prev_high_breakout(prev, today),
        "prev_close_battleground": _prev_close_battleground(prev, today),
        "volume_low_quadrant": _volume_low_quadrant(prev, today, today_is_partial=market_is_open),
        "investor_flow_continuity": _investor_flow_continuity(flows),
        "peer_comparison": _peer_comparison(today_change_rate, peer_changes),
        "limitations": [
            "오전/오후 투자자별 수급 분리는 신뢰 가능한 시간대별 데이터가 없어 계산하지 않음"
            "(intraday_investor_trading/ka10063의 투자자구분 코드 의미가 미확정)",
        ],
    }
