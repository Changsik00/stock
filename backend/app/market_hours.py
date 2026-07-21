"""정규장/NXT 확장세션 개장 여부 추정 — routers/*.py와 collectors/live_refresh.py가
공유한다.

2026-07-20, 서버 측 능동 60초 갱신 작업(PLAN.md)에서 원래 routers/markets.py에만
있던 ``_market_closed_kst``/``KST``를 여기로 순수 이동했다(동작 변경 없음) — 새로
생기는 collectors/live_refresh.py도 "장중에만 워밍한다" 판정에 동일 로직이
필요해서 중복 정의를 피하려고 공유 위치로 뺐다.

``is_market_closed``: 주말이거나 KRX 정규장 시간(09:00~15:30 KST) 밖이면 closed로
판정한다. 정밀 개장일력이 아니라 공휴일은 구분하지 못한다 — 공휴일 오검은 남지만,
그 경우에도 호출부가 잠정치 라벨을 붙이거나(routers/markets.py) 갱신을
건너뛸 뿐(collectors/live_refresh.py) 데이터 자체가 틀어지지는 않는다.

## ``is_nxt_closed`` — NXT(넥스트레이드) 확장세션 (2026-07-21 추가)

사용자 지적으로 발견: 개별 종목은 NXT ATS에서 **08:00~20:00** 거래되는데(사용자
확인), 이 모듈은 원래 KRX 정규장(09:00~15:30) 하나만 알고 있어서 15:30 이후
개별 종목 라이브 조회가 전부 "장 마감"으로 막혀 있었다. 실측(2026-07-21 18:36
KST)으로 두 부류가 다르게 움직임을 확인:

- **지수/집계 통계**(KOSPI·KOSDAQ 지수, 업종/테마 지수, K200 베이시스, 시장전체
  투자자별 수급, 등락종목수)는 키움 ka20005(지수 분봉) 마지막 봉이 정확히
  15:30:00에서 끊겨 있어 — **KRX 정규장 마감(15:30)에 그대로 고정**된다.
  이런 소스는 계속 ``is_market_closed``(정규장 창)를 쓴다.
- **개별 종목 시세**(관심순위 ka00198, 종목별 거래대금 순위 등 네이버 개별
  종목 목록)는 18:36에도 라이브로 계속 바뀌고 있었다(예: 삼성전자 ka00198
  응답 ``tm=183600`` 실시간, 삼천당제약 거래대금·등락률이 15시대 캡처와
  18시대 재조회 사이 실제로 변동) — **NXT 세션(08:00~20:00) 동안 계속
  움직인다.** 이런 소스는 ``is_nxt_closed``(확장 창)를 써야 한다.

환율(FX)은 이 구분과 무관하다(KRX/NXT 어느 쪽도 아닌 별도 시장) — 아직
``is_market_closed``를 쓰고 있어 저녁 시간대엔 여전히 과도하게 막힐 수 있는
알려진 한계다(PLAN.md 참고, 이번 수정 범위 밖).
"""

from __future__ import annotations

import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))

MARKET_OPEN_TIME_KST = dt.time(9, 0)
MARKET_CLOSE_TIME_KST = dt.time(15, 30)

# NXT(넥스트레이드) 확장세션 — 개별 종목 전용, 위 모듈 docstring 참고.
NXT_OPEN_TIME_KST = dt.time(8, 0)
NXT_CLOSE_TIME_KST = dt.time(20, 0)


def is_market_closed(now_kst: dt.datetime) -> bool:
    """KRX 정규장(지수/집계 통계 기준) — 09:00~15:30."""
    if now_kst.weekday() >= 5:  # 토(5)/일(6)
        return True
    return not (MARKET_OPEN_TIME_KST <= now_kst.time() < MARKET_CLOSE_TIME_KST)


def is_nxt_closed(now_kst: dt.datetime) -> bool:
    """NXT 확장세션(개별 종목 시세 기준) — 08:00~20:00."""
    if now_kst.weekday() >= 5:  # 토(5)/일(6)
        return True
    return not (NXT_OPEN_TIME_KST <= now_kst.time() < NXT_CLOSE_TIME_KST)
