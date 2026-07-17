"""pykrx (data.krx.co.kr 크롤링) client — 시장(코스피/코스닥) 단위 일별
투자자별 순매수(거래대금·거래량). PLAN.md §2/§6 Phase 1-4.

**중요 — PLAN.md 작성 시점(2026-07-15) 이후 확인된 사실**: PLAN.md §2는 pykrx를
"무인증·무료" 소스로 분류했지만, 실제로는 2026-02 KRX data 포털 개편 이후
data.krx.co.kr의 통계 JSON 엔드포인트(`comm/bldAttendant/getJsonData.cmd`)가
**전부** 로그인 세션을 요구한다. 세션 쿠키 없이 보내는 요청은 워밍업(홈페이지 GET →
JSESSIONID 획득) 여부와 무관하게 예외 없이 `HTTP 400 "LOGOUT"`으로 거부되는 것을
실제로 재현해 확인했다(이 저장소의 이전 pykrx 0.x/1.0 계열도 동일 — KRX 서버 자체가
막고 있어서 클라이언트 버전 문제가 아님).

pykrx>=1.2(현재 핀 버전 1.2.8)는 이를 위해 `KRX_ID`/`KRX_PW` 환경변수(데이터.krx.co.kr
**무료 회원가입** 로그인 — 증권사 실계좌 아님)로 로그인 세션을 자동 생성/갱신하는
기능을 내장하고 있다. 이 두 값은 config.py의 `krx_id`/`krx_pw`로 로드되며, 이
모듈이 import되는 시점에 `os.environ`에 주입된다 — pykrx가 세션을 **모듈 최초
import 시점**에 한 번만 만들기 때문에 `from pykrx import stock`보다 먼저 실행돼야
한다.

`KRX_ID`/`KRX_PW`가 없으면 아래 함수들은 예외를 던지지 않고 빈 리스트를 반환한다
(휴장일과 구분이 안 되지만, pykrx 자체가 HTTP 오류를 삼키고 빈 DataFrame으로
변환하는 decorator를 쓰고 있어서 이쪽에서도 예외로 승격시키지 않는 것이 일관적이다.
대신 모듈 로드 시 한 번, 그리고 매 빈 응답마다 로그를 남긴다).

pykrx는 동기(requests 기반) 라이브러리이므로 공개 함수는 항상 `asyncio.to_thread`로
감싼 async 함수다. KRX IP 차단을 피하기 위해 마지막 pykrx HTTP 호출로부터 최소
`MIN_CALL_INTERVAL_SEC`초가 지나도록 프로세스 전역으로 쓰로틀한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import threading
import time

from ..config import get_settings

logger = logging.getLogger(__name__)

# pykrx는 `pykrx.website.comm.webio` 모듈이 처음 import될 때 전역 KRX 로그인
# 세션을 생성하며, 그때 os.environ에서 KRX_ID/KRX_PW를 읽는다. 따라서 이 값들은
# `from pykrx import stock`가 실행되기 *전에* 환경변수로 설정돼 있어야 한다.
_settings = get_settings()
if _settings.krx_id and _settings.krx_pw:
    os.environ.setdefault("KRX_ID", _settings.krx_id)
    os.environ.setdefault("KRX_PW", _settings.krx_pw)
else:
    logger.warning(
        "KRX_ID/KRX_PW not set (.env) — pykrx will have no login session, and "
        "data.krx.co.kr will reject every request with HTTP 400 'LOGOUT'. "
        "KRX's Feb-2026 data portal overhaul requires a free data.krx.co.kr "
        "member login (not a brokerage account) for pykrx to work at all. "
        "See this module's docstring / PLAN.md §2 for details."
    )

from pykrx import stock  # noqa: E402  (must import after KRX_ID/KRX_PW are set)

MIN_CALL_INTERVAL_SEC = 0.5

_MARKET_TO_PYKRX = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

# pykrx의 get_market_trading_{value,volume}_by_date(..., detail=True) 원본 컬럼
# 그대로(순매수 기준). "전체"는 시장 합계 검산용 컬럼이라 투자자 분류로 취급하지 않는다.
_RAW_INVESTOR_COLUMNS = (
    "금융투자",
    "보험",
    "투신",
    "사모",
    "은행",
    "기타금융",
    "연기금",
    "기타법인",
    "개인",
    "외국인",
    "기타외국인",
)
# "기관계"는 detail=True 응답에 원본 컬럼으로 없다 (detail=False 응답에서만
# "기관합계"라는 이름의 사전 집계 컬럼으로 나옴). models.py에 문서화된 investor
# 분류 집합(기관계 포함, PLAN.md §5.2)에 맞추기 위해 7개 기관 세부분류를 합산해
# 직접 파생시킨다.
_INSTITUTIONAL_COLUMNS = ("금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금")


def _throttle_factory():
    lock = threading.Lock()
    state = {"last_call_ts": 0.0}

    def throttle() -> None:
        """마지막 pykrx HTTP 호출로부터 MIN_CALL_INTERVAL_SEC초가 지날 때까지
        현재 스레드를 블록한다. pykrx의 세션은 프로세스 전역이므로 여러
        asyncio.to_thread 워커가 동시에 호출해도 이 쓰로틀은 프로세스 전체
        기준으로 걸린다."""
        with lock:
            wait = MIN_CALL_INTERVAL_SEC - (time.monotonic() - state["last_call_ts"])
            if wait > 0:
                time.sleep(wait)
            state["last_call_ts"] = time.monotonic()

    return throttle


_throttle = _throttle_factory()


def _fetch_sync(market: str, target_date: dt.date) -> list[dict]:
    """블로킹 pykrx 호출 — 반드시 asyncio.to_thread 안에서만 실행할 것."""
    pykrx_market = _MARKET_TO_PYKRX[market]
    ymd = target_date.strftime("%Y%m%d")

    _throttle()
    value_df = stock.get_market_trading_value_by_date(ymd, ymd, pykrx_market, detail=True)
    _throttle()
    volume_df = stock.get_market_trading_volume_by_date(ymd, ymd, pykrx_market, detail=True)

    if value_df.empty or volume_df.empty:
        logger.info(
            "pykrx returned no data for market=%s date=%s (holiday, or "
            "missing/invalid KRX_ID/KRX_PW login — pykrx swallows HTTP "
            "errors into an empty DataFrame instead of raising)",
            market,
            ymd,
        )
        return []

    value_row = value_df.iloc[0]
    volume_row = volume_df.iloc[0]

    out: list[dict] = []
    for investor in _RAW_INVESTOR_COLUMNS:
        out.append(
            {
                "investor": investor,
                "net_value": int(value_row[investor]),
                "net_volume": int(volume_row[investor]),
            }
        )

    out.append(
        {
            "investor": "기관계",
            "net_value": int(sum(value_row[c] for c in _INSTITUTIONAL_COLUMNS)),
            "net_volume": int(sum(volume_row[c] for c in _INSTITUTIONAL_COLUMNS)),
        }
    )

    return out


async def get_market_investor_flow(market: str, target_date: dt.date) -> list[dict]:
    """코스피/코스닥 특정일 투자자별 순매수(거래대금·거래량) — pykrx 크롤링.

    Args:
        market: ``"kospi"`` 또는 ``"kosdaq"``.
        target_date: 조회할 단일 거래일.

    Returns:
        ``[{"investor": str, "net_value": int, "net_volume": int}, ...]`` — 12개
        투자자 분류(금융투자/보험/투신/사모/은행/기타금융/연기금/기관계/기타법인/
        개인/외국인/기타외국인). net_value/net_volume은 pykrx가 반환하는 원본 값을
        그대로 저장한다(단위 변환 없음 — 기존 index_ohlcv/stock_ohlcv도 원본 KRX API
        값을 그대로 저장하는 방식을 따름, backend/app/services.py 참고).
        휴장일이거나 KRX_ID/KRX_PW 로그인 세션이 없으면 빈 리스트를 반환한다
        (예외 아님 — collectors/market_flow.py가 "0행 적재"로 처리한다).
    """
    if market not in _MARKET_TO_PYKRX:
        raise ValueError(
            f"unsupported market {market!r}, expected one of {sorted(_MARKET_TO_PYKRX)}"
        )
    return await asyncio.to_thread(_fetch_sync, market, target_date)
