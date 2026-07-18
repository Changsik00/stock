"""K200 선물 만기일 유틸 — 순수 함수 (PLAN.md §4.5-3).

이 모듈은 DB/네트워크 무관 순수 함수만 담는다(단위테스트 대상, tests/test_derivatives.py)
— app/sentiment.py(§4.6 3.6-4)와 동일한 "순수 계산부 분리" 패턴이다. routers/basis.py가
이 함수들을 호출해 베이시스 API 응답의 ``expiry`` 필드를 채운다.

KOSPI200 선물의 만기일은 결제월의 **둘째 목요일**이다(한국거래소 규정, 공휴일로
둘째 목요일이 휴장이면 순연되지만 그 조정은 이 모듈이 다루지 않는다 — 공휴일 캘린더
데이터가 없어 반영 못 함, 알려진 한계). 3/6/9/12월物은 옵션 만기와 겹쳐 **네 마녀의
날**(quadruple witching day)이라 부른다.
"""

from __future__ import annotations

import datetime as dt

# 결제월 만기와 옵션 만기가 겹치는 분기월 (3월/6월/9월/12월) — 네 마녀의 날.
QUADRUPLE_WITCHING_MONTHS = (3, 6, 9, 12)

_THURSDAY = 3  # datetime.date.weekday(): Monday=0 ... Thursday=3, Sunday=6


def _second_thursday(year: int, month: int) -> dt.date:
    """해당 연/월의 둘째 목요일 날짜."""
    first_of_month = dt.date(year, month, 1)
    days_until_thursday = (_THURSDAY - first_of_month.weekday()) % 7
    first_thursday = first_of_month + dt.timedelta(days=days_until_thursday)
    return first_thursday + dt.timedelta(days=7)


def next_futures_expiry(date: dt.date) -> dt.date:
    """``date`` 기준 다음(또는 당일) K200 선물 만기일(그 달의 둘째 목요일).

    ``date``가 이번 달 둘째 목요일보다 이전이거나 정확히 그날이면 이번 달 만기를
    반환한다(만기 당일은 D-0으로 취급) — 그 이후면 다음 달 둘째 목요일을 반환한다.
    """
    this_month_expiry = _second_thursday(date.year, date.month)
    if date <= this_month_expiry:
        return this_month_expiry

    year, month = date.year, date.month + 1
    if month > 12:
        month = 1
        year += 1
    return _second_thursday(year, month)


def is_quadruple_witching(date: dt.date) -> bool:
    """``date``가 네 마녀의 날(3/6/9/12월 둘째 목요일)인지 여부.

    ``date`` 자체가 그 조건을 만족하는지 검사한다(만기일 후보를 넘겨 쓸 것 — 임의의
    날짜를 넣으면 대부분 False가 정상)."""
    return date.month in QUADRUPLE_WITCHING_MONTHS and date == _second_thursday(
        date.year, date.month
    )


def days_to_expiry(date: dt.date) -> int:
    """``date``에서 다음 만기일까지 남은 일수(D-day, 당일이면 0)."""
    return (next_futures_expiry(date) - date).days
