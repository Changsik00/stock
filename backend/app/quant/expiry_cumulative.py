"""K200 선물 만기 사이클 누적 수급 (PLAN.md Phase 5.100, 2026-11-10 사용자 요청).

**무엇을 보여주나**: K200 선물 만기(둘째 목요일) 사이클 기준으로 외국인·기관계·
개인의 **선물 순매수**(``market_flow`` market='k200_futures', source='naver')를
누적한다. 옵션 수급은 수집하지 않으므로 이 모듈은 "선물 기준"이다(UI에도 명시).

**사이클 경계**: 한 사이클 = (직전 만기일 다음 날) ~ (이번 만기일, 당일 포함).
만기일 당일은 그 만기의 사이클에 포함되고, 다음 날부터 새 사이클이다. 현재
사이클은 "직전 만기(오늘보다 앞선 가장 최근 만기) 익일 ~ 오늘"이다. 오늘이
만기일이면 오늘은 아직 끝나지 않은 사이클의 마지막 날로 현재 사이클에 들어간다.

**부분 사이클 제외**: 데이터 시작(2023-07-20) 직후 사이클은 시작 구간이 잘려
있으므로, 첫 데이터일이 사이클 시작일보다 ``MAX_START_GAP_DAYS`` 넘게 늦으면
완료 사이클 목록에서 뺀다. 데이터가 하나도 없는 사이클도 뺀다(0으로 채우지 않음).

**관찰 통계이지 예측이 아니다**: §5.15/§5.42와 동일한 house rule. 누적 수급이
이후 방향을 예고한다는 검증은 없으며, 이 모듈은 판정 없이 숫자만 반환한다.
금액 단위는 DB 그대로 백만원.

**순수 함수 + 세션 래퍼**: ``aggregate_expiry_cumulative``는 (date, investor,
net_value) 튜플만 받아 단위테스트가 DB 없이 돈다(``expiry_pattern.py`` 동일 패턴).
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..derivatives import expiries_between, next_futures_expiry
from ..market_hours import KST
from ..models import IndexOhlcv, MarketFlow

FUTURES_MARKET = "k200_futures"
SOURCE = "naver"
# DB investor 명 -> 응답 키
INVESTORS = {"외국인": "foreign", "기관계": "institution", "개인": "individual"}
DEFAULT_CYCLES = 6
STATS_CYCLES = 30  # 흡수율 분포·부호 전환 계산용으로 내부에서 보는 완료 사이클 수
# 현물(Phase 5.101): market_flow market in (kospi, kosdaq), source='kiwoom'.
SPOT_SOURCE = "kiwoom"
SPOT_MARKETS = ("kospi", "kosdaq")
# 기간 모드(Phase 5.101): cycle(기본) / recent(최근 N거래일) / peak(고점 이후)
MODES = ("cycle", "recent", "peak")
DEFAULT_RECENT_DAYS = 60
MAX_RECENT_DAYS = 500
PEAK_LOOKBACK_DAYS = 120  # 고점 탐색 구간(최근 N거래일)
# 사이클 시작일보다 첫 데이터일이 이만큼(달력일) 넘게 늦으면 부분 사이클로 보고 제외.
MAX_START_GAP_DAYS = 7


def _zero() -> dict[str, int]:
    return {k: 0 for k in INVESTORS.values()}


def _sign(v: int | float | None) -> int:
    """부호(-1/0/1). None은 0(판단 불가)으로 본다."""
    if v is None or v == 0:
        return 0
    return 1 if v > 0 else -1


def compute_absorption(spot_totals: dict[str, int] | None) -> dict[str, Any] | None:
    """외국인 현물 누적이 순매도(<0)일 때 기관·개인이 받아낸 비율(Phase 5.103).

    흡수율(%) = (기관 현물 + 개인 현물) / |외인 현물| x 100. 기관·개인 각각의 기여도도
    같은 분모로 계산한다(부호 유지: 기관이 순매도면 기여도는 음수, 흡수율을 깎는다).
    외인이 순매수(또는 0)이거나 현물 데이터가 없으면 None(해당없음, 0으로 채우지 않음).
    관찰용 비율일 뿐 인과·예측이 아니다."""
    if not spot_totals:
        return None
    f = spot_totals.get("foreign")
    if f is None or f >= 0:
        return None
    inst = spot_totals.get("institution", 0)
    indv = spot_totals.get("individual", 0)
    denom = abs(f)
    return {
        "foreign": f,
        "institution": inst,
        "individual": indv,
        "pct": round((inst + indv) / denom * 100.0, 1),
        "institution_pct": round(inst / denom * 100.0, 1),
        "individual_pct": round(indv / denom * 100.0, 1),
    }


PAIR_LABELS = {
    (-1, -1): "동반 매도",
    (-1, 1): "현물 매도 + 선물 매수(엇갈림)",
    (1, 1): "동반 매수",
    (1, -1): "현물 매수 + 선물 매도(엇갈림)",
}


def pair_label(spot_totals: dict[str, int] | None, totals: dict[str, int] | None) -> str | None:
    """외인 현물·선물 누적 부호 조합의 관찰형 라벨. 한쪽이 없거나 0이면 None."""
    s = _sign((spot_totals or {}).get("foreign"))
    f = _sign((totals or {}).get("foreign"))
    if s == 0 or f == 0:
        return None
    return PAIR_LABELS[(s, f)]


def inst_flip(prev_spot: dict[str, int] | None, cur_spot: dict[str, int] | None) -> dict[str, Any] | None:
    """기관 현물 누적 부호가 직전 완료 사이클 대비 바뀌었는지. 어느 쪽이든 없거나 0이면 None."""
    p = _sign((prev_spot or {}).get("institution"))
    c = _sign((cur_spot or {}).get("institution"))
    if p == 0 or c == 0:
        return None
    name = {1: "순매수", -1: "순매도"}
    return {
        "flipped": p != c,
        "prev": "buy" if p > 0 else "sell",
        "cur": "buy" if c > 0 else "sell",
        "label": (
            f"기관 현물 누적 {name[p]} → {name[c]} 전환"
            if p != c
            else f"기관 현물 누적 {name[c]} 유지"
        ),
    }


def absorption_stats(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """완료 사이클들의 흡수율(%) 분포. 해당없음(None) 사이클은 표본에서 제외하고 n으로 밝힌다."""
    vals = sorted(
        it["absorption"]["pct"] for it in items if it.get("absorption") is not None
    )
    if not vals:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {
        "n": len(vals),
        "min": vals[0],
        "median": round(statistics.median(vals), 1),
        "max": vals[-1],
    }


def _index_pct(closes: dict[dt.date, float], base: float | None, d: dt.date) -> float | None:
    """기준 종가 대비 ``d`` 종가 등락률(%). 값이 없으면 None(0으로 채우지 않음)."""
    c = closes.get(d)
    if base is None or c is None or base == 0:
        return None
    return round((c / base - 1.0) * 100.0, 3)


def _base_close(closes: dict[dt.date, float], start: dt.date) -> float | None:
    """``start`` 직전 거래일 종가(구간 등락률의 기준). 없으면 None."""
    prior = [d for d in closes if d < start]
    return closes[max(prior)] if prior else None


def _group(
    rows: Iterable[tuple[dt.date, str, int | None]] | None, today: dt.date
) -> dict[dt.date, dict[str, int]]:
    """(일자, 투자자, 값) -> {일자: {키: 합}}. 미지 투자자·None·미래 일자는 제외."""
    by_date: dict[dt.date, dict[str, int]] = {}
    for d, inv, val in rows or ():
        key = INVESTORS.get(inv)
        if key is None or val is None or d > today:
            continue
        by_date.setdefault(d, _zero())[key] += int(val)
    return by_date


def _build_cycle(
    by_date: dict[dt.date, dict[str, int]],
    start: dt.date,
    end: dt.date,
    expiry: dt.date | None,
    spot_by_date: dict[dt.date, dict[str, int]] | None = None,
    closes: dict[dt.date, float] | None = None,
    base: float | None = None,
    require_futures: bool = True,
) -> dict[str, Any] | None:
    """``[start, end]`` 구간의 일자별 누적 곡선(선물 + 선택적으로 현물·지수 등락률).
    구간에 데이터가 없으면 None. ``require_futures``이면 선물 데이터가 있는 날만
    거래일로 센다(Phase 5.100 동작 유지). 아니면 선물·현물 일자의 합집합.
    지수 등락률 기준은 ``base``(없으면 start 직전 종가)."""
    spot_by_date = spot_by_date or {}
    closes = closes or {}
    pool = set(by_date) if require_futures else set(by_date) | set(spot_by_date)
    days = sorted(d for d in pool if start <= d <= end)
    if not days:
        return None
    if base is None:
        base = _base_close(closes, start)
    running = _zero()
    spot_running = _zero()
    has_spot = False
    curve: list[dict[str, Any]] = []
    n = len(days)
    for i, d in enumerate(days):
        for k, v in by_date.get(d, {}).items():
            running[k] += v
        if d in spot_by_date:
            has_spot = True
            for k, v in spot_by_date[d].items():
                spot_running[k] += v
        curve.append(
            {
                "date": d.isoformat(),
                "day_n": i + 1,
                "d_day": i - (n - 1),  # 마지막 거래일=0, 이전일수록 음수
                **running,
                **{f"spot_{k}": v for k, v in spot_running.items()},
                "index_pct": _index_pct(closes, base, d),
            }
        )
    spot_totals = dict(spot_running) if has_spot else None
    return {
        "expiry": expiry.isoformat() if expiry else None,
        "absorption": compute_absorption(spot_totals),
        "pair_label": pair_label(spot_totals, running),
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "trading_days": n,
        "totals": dict(running),
        "spot_totals": spot_totals,
        "index_pct": next(
            (p["index_pct"] for p in reversed(curve) if p["index_pct"] is not None), None
        ),
        "curve": curve,
    }


def build_recent_window(
    by_date: dict[dt.date, dict[str, int]],
    spot_by_date: dict[dt.date, dict[str, int]],
    closes: dict[dt.date, float],
    today: dt.date,
    n: int,
) -> dict[str, Any] | None:
    """최근 ``n``거래일(선물·현물 일자 합집합 기준) 누적. 데이터가 없으면 None."""
    n = max(1, min(int(n), MAX_RECENT_DAYS))
    days = sorted(d for d in (set(by_date) | set(spot_by_date)) if d <= today)
    if not days:
        return None
    start = days[-n:][0]
    item = _build_cycle(
        by_date, start, today, None, spot_by_date, closes, require_futures=False
    )
    if item is not None:
        item["mode"] = "recent"
        item["days"] = n
    return item


def build_peak_window(
    by_date: dict[dt.date, dict[str, int]],
    spot_by_date: dict[dt.date, dict[str, int]],
    closes: dict[dt.date, float],
    today: dt.date,
    lookback: int = PEAK_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """"고점 이후": 최근 ``lookback``거래일 안에서 지수 종가 최고일(동률이면 가장
    늦은 날)의 다음 날부터 오늘까지 누적. 등락률 기준은 고점 종가.

    Returns ``{"peak": {date, close, index_pct, lookback}|None, "window": {...}|None}``.
    종가가 없으면 peak=None. 고점이 마지막 거래일이면 window=None(고점 이후 일자 없음)."""
    cl = sorted(d for d, c in closes.items() if d <= today and c is not None)[-lookback:]
    if not cl:
        return {"peak": None, "window": None}
    peak_date = max(cl, key=lambda d: (closes[d], d))
    peak_close = closes[peak_date]
    window = _build_cycle(
        by_date,
        peak_date + dt.timedelta(days=1),
        today,
        None,
        spot_by_date,
        closes,
        base=peak_close,
        require_futures=False,
    )
    if window is not None:
        window["mode"] = "peak"
    last_close = closes[cl[-1]]
    peak = {
        "date": peak_date.isoformat(),
        "close": peak_close,
        "index_pct": round((last_close / peak_close - 1.0) * 100.0, 3) if peak_close else None,
        "lookback": lookback,
    }
    return {"peak": peak, "window": window}


def aggregate_expiry_cumulative(
    rows: Iterable[tuple[dt.date, str, int | None]],
    expiries: list[dt.date],
    today: dt.date,
    cycles: int = DEFAULT_CYCLES,
    spot_rows: Iterable[tuple[dt.date, str, int | None]] | None = None,
    closes: dict[dt.date, float] | None = None,
    mode: str = "cycle",
    days: int = DEFAULT_RECENT_DAYS,
) -> dict[str, Any]:
    """만기 사이클별 투자자 선물 순매수 누적(순수 함수). Phase 5.101부터 현물·지수 등락률과
    기간 모드를 함께 처리한다(``spot_rows``/``closes`` 생략 시 5.100 동작 그대로).

    Args:
        rows: (일자, 투자자명, net_value 백만원) 선물. INVESTORS에 없는 투자자는 무시.
        expiries: 만기일 목록(``expiries_between``). 오름차순 아님이어도 됨.
        today: 기준일. 오늘 이상의 데이터는 현재 사이클에 포함(미래 일자는 제외).
        cycles: 반환할 완료 사이클 수(최근 순).
        spot_rows: 같은 형식의 현물(kospi/kosdaq) 순매수. 사이클 항목에 ``spot_totals``와
            곡선의 ``spot_*`` 키로 붙는다.
        closes: {일자: 지수 종가}. 곡선의 ``index_pct``(구간 기준 대비 %)에 쓴다.
        mode: cycle | recent | peak. recent/peak이면 ``window`` 키를 추가한다.
        days: recent 모드의 거래일 수.

    Returns:
        ``{"current": {...}|None, "completed": [...오래된 순], "next_expiry",
        "investors", "unit": "백만원", "basis": "선물 순매수 기준(옵션 아님)",
        "date_from", "date_to"}`` (+ ``mode``, ``window``, 고점 모드는 ``peak``).
    """
    by_date = _group(rows, today)
    spot_by_date = _group(spot_rows, today)
    closes = {d: float(c) for d, c in (closes or {}).items() if d <= today and c is not None}

    all_days = sorted(by_date)
    first_day = all_days[0] if all_days else None
    last_day = all_days[-1] if all_days else None

    past = sorted(e for e in set(expiries) if e < today)
    next_expiry = next_futures_expiry(today)

    current = None
    if past:
        prev_expiry = past[-1]
        current = _build_cycle(
            by_date,
            prev_expiry + dt.timedelta(days=1),
            today,
            next_expiry,
            spot_by_date,
            closes,
        )
        if current is not None:
            current["prev_expiry"] = prev_expiry.isoformat()

    completed: list[dict[str, Any]] = []
    # past[-1]의 사이클은 현재 사이클의 시작점이므로 완료 사이클은 past[-1] 이하 만기.
    for i in range(len(past) - 1, 0, -1):
        if len(completed) >= max(cycles, STATS_CYCLES):
            break
        start = past[i - 1] + dt.timedelta(days=1)
        item = _build_cycle(by_date, start, past[i], past[i], spot_by_date, closes)
        if item is None:
            continue
        if (dt.date.fromisoformat(item["start"]) - start).days > MAX_START_GAP_DAYS and (
            first_day is not None and first_day > start
        ):
            continue  # 데이터 시작 이전에 걸친 부분 사이클
        item["prev_expiry"] = past[i - 1].isoformat()
        completed.append(item)
    completed.reverse()
    # 기관 현물 부호 전환(직전 완료 사이클 대비) — 잘라내기 전에 계산해 가장 오래된 표시분도 비교 가능
    for j, item in enumerate(completed):
        item["inst_flip"] = (
            inst_flip(completed[j - 1]["spot_totals"], item["spot_totals"]) if j > 0 else None
        )
    if current is not None:
        current["inst_flip"] = (
            inst_flip(completed[-1]["spot_totals"], current["spot_totals"]) if completed else None
        )
    stats = absorption_stats(completed)
    completed = completed[-cycles:] if cycles > 0 else []

    out: dict[str, Any] = {
        "current": current,
        "completed": completed,
        "next_expiry": next_expiry.isoformat(),
        "investors": list(INVESTORS.values()),
        "unit": "백만원",
        "basis": "선물 순매수 기준(옵션 아님)",
        "date_from": first_day.isoformat() if first_day else None,
        "date_to": last_day.isoformat() if last_day else None,
        "mode": mode,
        "absorption_stats": stats,
    }
    if mode == "recent":
        out["window"] = build_recent_window(by_date, spot_by_date, closes, today, days)
    elif mode == "peak":
        out.update(build_peak_window(by_date, spot_by_date, closes, today))
    return out


async def compute_expiry_cumulative(
    session: AsyncSession,
    cycles: int = DEFAULT_CYCLES,
    today: dt.date | None = None,
    market: str = "kospi",
    mode: str = "cycle",
    days: int = DEFAULT_RECENT_DAYS,
) -> dict[str, Any]:
    """``market_flow``에서 k200_futures(naver) + 현물(``market``, kiwoom) 외국인/기관계/개인과
    ``index_ohlcv`` 종가를 읽어 집계한다. DB 읽기 전용, 새 수집 없음."""
    today = today or dt.datetime.now(KST).date()
    market = market if market in SPOT_MARKETS else "kospi"
    mode = mode if mode in MODES else "cycle"

    async def _flow(mkt: str, source: str):
        stmt = select(MarketFlow.date, MarketFlow.investor, MarketFlow.net_value).where(
            MarketFlow.market == mkt,
            MarketFlow.source == source,
            MarketFlow.investor.in_(list(INVESTORS)),
        )
        return [(d, inv, v) for d, inv, v in (await session.execute(stmt)).all()]

    rows = await _flow(FUTURES_MARKET, SOURCE)
    spot_rows = await _flow(market, SPOT_SOURCE)
    cl = await session.execute(
        select(IndexOhlcv.date, IndexOhlcv.close).where(
            IndexOhlcv.market == market, IndexOhlcv.close.is_not(None)
        )
    )
    closes = {d: float(c) for d, c in cl.all()}
    if not rows:
        expiries: list[dt.date] = []
    else:
        first = min(r[0] for r in rows)
        # 데이터 시작 직전 만기부터 포함해야 첫 사이클의 시작 경계를 판단할 수 있다.
        expiries = expiries_between(first - dt.timedelta(days=40), today + dt.timedelta(days=40))
    out = aggregate_expiry_cumulative(
        rows, expiries, today, cycles, spot_rows, closes, mode, days
    )
    out["market"] = market
    out["spot_basis"] = "현물 순매수(키움 시장 합계)"
    return out
