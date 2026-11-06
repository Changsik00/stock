"""나스닥선물(NQ=F) 5분봉 변화율 계산 — 섀도(관찰) 전용 순수 함수 (PLAN.md §5.91).

**실거래 판정(`auto_trade_rules.py`)의 입력으로 쓰지 않는다.** 백테스트
(`scripts/nq_trail_validate.py`)와 `auto_trade_log.signal_snapshot['nq_shadow']`
기록에서만 쓴다. 두 곳이 같은 계산을 쓰도록 순수 함수 한 곳에 둔다.

봉 시각은 yfinance 규약대로 "봉 시작 시각"이다. 봉은 시작+5분에 완성되므로
``asof`` 시점에 쓸 수 있는 건 ``start + 5분 <= asof``인 봉뿐이다(미래 참조 방지).
"""

from __future__ import annotations

import bisect
import datetime as dt
from typing import Sequence

NQ_BAR_SECONDS = 300
NQ_SERIES_KEY = "nq_futures"
# asof 직전 완성봉이 이보다 오래됐으면(휴장·수집 중단) 변화율을 None으로 돌린다.
DEFAULT_MAX_STALE_MIN = 20


class NqIndex:
    """봉 종료시각 기준 조회 인덱스 — 백테스트처럼 같은 시리즈를 수만 번 조회할 때
    O(log n)로 쓰기 위한 것(``nq_change_pct``도 이걸 쓴다)."""

    def __init__(self, bars: Sequence[tuple[dt.datetime, float]]):
        step = dt.timedelta(seconds=NQ_BAR_SECONDS)
        self._close = {t + step: c for t, c in bars}
        self._ends = sorted(self._close)

    def last_complete(self, asof: dt.datetime) -> tuple[dt.datetime, float] | None:
        i = bisect.bisect_right(self._ends, asof)
        if i == 0:
            return None
        end = self._ends[i - 1]
        return end, self._close[end]

    def change_pct(
        self, asof: dt.datetime, minutes: int, max_stale_min: int = DEFAULT_MAX_STALE_MIN
    ) -> float | None:
        """``asof`` 기준 최근 완성봉 종가가 ``minutes``분 전(종료시각 기준 정확히 일치하는
        봉) 종가 대비 몇 %인지. 기준 봉이 없거나(휴장 갭) 최근 완성봉이 ``max_stale_min``분
        넘게 오래됐으면 None."""
        last = self.last_complete(asof)
        if last is None:
            return None
        end, close = last
        if asof - end > dt.timedelta(minutes=max_stale_min):
            return None
        prev = self._close.get(end - dt.timedelta(minutes=minutes))
        if prev is None or prev <= 0:
            return None
        return round((close - prev) / prev * 100, 4)


def nq_change_pct(
    bars: Sequence[tuple[dt.datetime, float]],
    asof: dt.datetime,
    minutes: int,
    max_stale_min: int = DEFAULT_MAX_STALE_MIN,
) -> float | None:
    """``NqIndex.change_pct``의 일회성 래퍼(``bars``=오름차순 ``(봉 시작시각, 종가)``)."""
    return NqIndex(bars).change_pct(asof, minutes, max_stale_min)


def build_nq_shadow(bars: Sequence[tuple[dt.datetime, float]], asof: dt.datetime) -> dict:
    """signal_snapshot['nq_shadow'] 값. 값이 없으면 각 필드 None."""
    idx = NqIndex(bars)
    last = idx.last_complete(asof)
    return {
        "nq_change_15m_pct": idx.change_pct(asof, 15),
        "nq_change_30m_pct": idx.change_pct(asof, 30),
        "nq_last": last[1] if last else None,
        "ts": last[0].isoformat() if last else None,
    }


def merge_nq_shadow(snapshot: dict | None, shadow: dict, json_dumps, limit: int = 2000) -> dict | None:
    """snapshot에 ``nq_shadow`` 키를 **추가만** 한다(기존 키 보존). 추가 후 직렬화
    길이가 ``limit``을 넘으면 추가하지 않고 원본을 그대로 돌려준다(2000자 절단으로
    JSON이 깨지는 것 방지). snapshot이 None이면 None."""
    if snapshot is None:
        return None
    merged = {**snapshot, "nq_shadow": shadow}
    if len(json_dumps(merged)) > limit:
        return snapshot
    return merged
