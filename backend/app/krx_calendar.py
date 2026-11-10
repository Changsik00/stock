"""KRX 휴장일 판정 (Phase 5.102) — 레포에 개장일력이 없어 최소한으로 둔다.

키움 ka10051은 비영업일 `base_dt`를 주면 에러 없이 **직전 확정치를 그대로**
돌려주고, 응답에도 실제 기준일 필드가 없어 요청일과 비교할 수 없다. 그래서
수집기가 휴장일 날짜로 전 거래일 값을 저장하는 '유령 행'이 생겼다. 이 모듈은 그
날짜를 적재 전에 걸러내는 1차 판정이다.

판정 3단:
1. 토/일.
2. 매년 날짜가 고정인 휴장일(신정·삼일절·근로자의 날·어린이날·현충일·광복절·
   개천절·한글날·성탄절·연말 폐장일 12/31). 주말과 겹치는 해의 대체휴일은
   해마다 달라 여기서 못 잡는다.
3. 실측으로 확인된 비고정 휴장일(설·추석·대체휴일·임시공휴일·선거일 등).
   2023-08~2026-10 구간은 market_flow 유령 행(백업 테이블
   market_flow_ghost_backup_20261110)의 날짜 60일 그대로다.

**한계**: 2026-11 이후의 설·추석·대체휴일은 이 목록에 없다. 매년 말 다음 해
휴장일을 KRX 공시 기준으로 `_KNOWN_CLOSED`에 추가해야 한다. 누락분은
collectors/market_flow.py의 '직전 거래일과 값 완전 동일' 가드가 2차로 막는다.
"""

from __future__ import annotations

import datetime as dt

# (월, 일) 고정 휴장일.
_FIXED_CLOSED_MD = frozenset(
    {
        (1, 1),
        (3, 1),
        (5, 1),
        (5, 5),
        (6, 6),
        (8, 15),
        (10, 3),
        (10, 9),
        (12, 25),
        (12, 31),
    }
)

# 실측 확인된 휴장일(고정일과 겹치는 날 포함 — 중복은 무해).
_KNOWN_CLOSED = frozenset(
    {
    dt.date(2023, 8, 15), dt.date(2023, 9, 28), dt.date(2023, 9, 29), dt.date(2023, 10, 2),
    dt.date(2023, 10, 3), dt.date(2023, 10, 9), dt.date(2023, 12, 25), dt.date(2023, 12, 29),
    dt.date(2024, 1, 1), dt.date(2024, 2, 9), dt.date(2024, 2, 12), dt.date(2024, 3, 1),
    dt.date(2024, 4, 10), dt.date(2024, 5, 1), dt.date(2024, 5, 6), dt.date(2024, 5, 15),
    dt.date(2024, 6, 6), dt.date(2024, 8, 15), dt.date(2024, 9, 16), dt.date(2024, 9, 17),
    dt.date(2024, 9, 18), dt.date(2024, 10, 1), dt.date(2024, 10, 3), dt.date(2024, 10, 9),
    dt.date(2024, 12, 25), dt.date(2024, 12, 31), dt.date(2025, 1, 1), dt.date(2025, 1, 27),
    dt.date(2025, 1, 28), dt.date(2025, 1, 29), dt.date(2025, 1, 30), dt.date(2025, 3, 3),
    dt.date(2025, 5, 1), dt.date(2025, 5, 5), dt.date(2025, 5, 6), dt.date(2025, 6, 3),
    dt.date(2025, 6, 6), dt.date(2025, 8, 15), dt.date(2025, 10, 3), dt.date(2025, 10, 6),
    dt.date(2025, 10, 7), dt.date(2025, 10, 8), dt.date(2025, 10, 9), dt.date(2025, 12, 25),
    dt.date(2025, 12, 31), dt.date(2026, 1, 1), dt.date(2026, 2, 16), dt.date(2026, 2, 17),
    dt.date(2026, 2, 18), dt.date(2026, 3, 2), dt.date(2026, 5, 1), dt.date(2026, 5, 5),
    dt.date(2026, 5, 25), dt.date(2026, 6, 3), dt.date(2026, 7, 17), dt.date(2026, 8, 17),
    dt.date(2026, 9, 24), dt.date(2026, 9, 25), dt.date(2026, 10, 5), dt.date(2026, 10, 9),
    }
)


def is_krx_closed(day: dt.date) -> bool:
    """day가 주말이거나 알려진 KRX 휴장일이면 True."""
    if day.weekday() >= 5:
        return True
    if (day.month, day.day) in _FIXED_CLOSED_MD:
        return True
    return day in _KNOWN_CLOSED
