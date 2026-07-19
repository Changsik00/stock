"""정규장 개장 여부 추정 — routers/markets.py와 collectors/live_refresh.py가 공유한다.

2026-07-20, 서버 측 능동 60초 갱신 작업(PLAN.md)에서 원래 routers/markets.py에만
있던 ``_market_closed_kst``/``KST``를 여기로 순수 이동했다(동작 변경 없음) — 새로
생기는 collectors/live_refresh.py도 "장중에만 워밍한다" 판정에 동일 로직이
필요해서 중복 정의를 피하려고 공유 위치로 뺐다.

주말이거나 정규장 시간(09:00~15:30 KST) 밖이면 closed로 판정한다. 정밀 개장일력이
아니라 공휴일은 구분하지 못한다 — 공휴일 오검은 남지만, 그 경우에도 호출부가
잠정치 라벨을 붙이거나(routers/markets.py) 갱신을 건너뛸 뿐(collectors/live_refresh.py)
데이터 자체가 틀어지지는 않는다.
"""

from __future__ import annotations

import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))

MARKET_OPEN_TIME_KST = dt.time(9, 0)
MARKET_CLOSE_TIME_KST = dt.time(15, 30)


def is_market_closed(now_kst: dt.datetime) -> bool:
    if now_kst.weekday() >= 5:  # 토(5)/일(6)
        return True
    return not (MARKET_OPEN_TIME_KST <= now_kst.time() < MARKET_CLOSE_TIME_KST)
