"""코스닥150 레버리지/인버스 ETF 쌍 괴리 기록기 (PLAN.md §5.88-1)
— 관찰 로그, 매매 신호 아님.

`quant/etf_pair_metrics.PAIR_ETFS`의 ETF 2종(233740 레버리지 +2X / 251340 인버스
-1X)의 현재가·전일 기준가를 키움 ka10001(종목기본정보)로, 기초지수(코스닥150,
업종코드 150)의 레벨·등락률을 ka20001(업종현재가)로,
NAV를 네이버 ETF 목록(`clients/naver_etf.fetch_etf_list`, 1회 호출에 전체 반환)으로
읽어, 분 단위로 `etf_pair_sample` 테이블에 쌓는다. 목적은 "괴리율/본주 이론가
대비 시차가 장중에 어떤 분포로 나오는지"를 나중에 보기 위한 표본 축적이며,
auto_trader/auto_trade_state는 건드리지 않는다.

원본값(price/base_price/nav/본주 가격·등락률)과 파생값(theory/premium/lag)을
둘 다 저장한다 — 나중에 공식을 바꿔도 재계산할 수 있게 하기 위함이다.

## 시간 게이트: `is_market_closed`(KRX 정규장 09:00~15:30)

`is_nxt_closed`(08:00~20:00)가 아니라 정규장 게이트를 쓴다. NXT/시간외 구간의
ETF·본주 가격은 NAV·이론가 산식의 전제(같은 시각의 정규장 가격)와 어긋나 괴리가
왜곡되므로 기록 대상에서 뺀다. 공휴일은 market_hours가 구분하지 못해(평일이면
open) 휴장일에도 호출이 한 번씩 나가지만, 이 경우 본주 값이 비어 있거나 전일과
같아 다음 분에 같은 (time, code)가 덮어써질 뿐 해롭지 않다.

## 관대함

ETF 하나가 실패하면 그 종목만 건너뛰고 나머지는 기록한다(positioning_snapshot과
동일). NAV 조회 실패는 nav/premium_pct만 NULL로 둔다. 본주 조회가 실패하면
이론가를 계산할 수 없으므로 아무것도 쓰지 않는다. 같은 분에 다시 실행하면
`ON CONFLICT DO UPDATE`로 덮어쓴다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_etf
from ..clients.kiwoom import KiwoomClient
from ..market_hours import KST, is_market_closed
from ..models import EtfPairSample
from ..quant.etf_pair_metrics import (
    PAIR_ETFS,
    UNDERLYING_CODE,
    compute_lag_pct,
    compute_premium_pct,
    compute_theory_price,
)

logger = logging.getLogger(__name__)


def _abs_number(raw: Any) -> float | None:
    """ka10001 가격/등락률 문자열("+7055", "-7290")의 부호 접두를 방향 표시로
    소비하고 절대값을 돌려준다(가격은 항상 양수 — clients/kiwoom.py의 "가격 필드
    부호 인코딩 주의" 절). 파싱 불가/누락이면 None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").lstrip("+-")
    if not text:
        return None
    try:
        return abs(float(text))
    except ValueError:
        return None


def _signed_rate(raw: Any) -> float | None:
    """등락률 문자열("+10.66", "-21.40")을 부호를 유지한 채 float로 파싱한다."""
    if raw is None:
        return None
    try:
        return float(str(raw).strip().replace(",", ""))
    except ValueError:
        return None


async def _fetch_quote(client: KiwoomClient, code: str) -> dict[str, float | None] | None:
    """ka10001 한 콜로 현재가·기준가·등락률을 읽는다. 현재가/기준가가 없으면 None."""
    data = await client.stock_info(code)
    price = _abs_number(data.get("cur_prc"))
    base = _abs_number(data.get("base_pric"))
    if price is None or base is None:
        return None
    return {"price": price, "base_price": base, "change_rate": _signed_rate(data.get("flu_rt"))}


async def _fetch_underlying(client: KiwoomClient) -> dict[str, float | None] | None:
    """ka20001 업종현재가로 기초지수 레벨·등락률을 읽는다. 큰 시계열 배열
    (inds_cur_prc_tm)은 쓰지 않는다. 응답 오류/레벨 없음이면 None."""
    data, _ = await client.sector_current_price(UNDERLYING_CODE)
    if str(data.get("return_code", 0)) not in ("0", ""):
        return None
    price = _abs_number(data.get("cur_prc"))
    if price is None:
        return None
    return {"price": price, "change_rate": _signed_rate(data.get("flu_rt"))}


async def _fetch_navs() -> dict[str, float]:
    """네이버 ETF 목록에서 대상 ETF의 NAV를 읽는다. 실패하면 빈 dict(호출부가
    NAV를 NULL로 둔다)."""
    try:
        items = await asyncio.to_thread(naver_etf.fetch_etf_list)
    except Exception as e:  # noqa: BLE001 - NAV 실패가 나머지 기록을 막지 않도록
        logger.warning("etf-pair: NAV 조회 실패: %s", e)
        return {}
    navs: dict[str, float] = {}
    for it in items:
        if it.get("code") in PAIR_ETFS and it.get("nav"):
            navs[it["code"]] = float(it["nav"])
    return navs


async def record_pair_sample(session: AsyncSession, now: dt.datetime | None = None) -> int:
    """기초지수+ETF 쌍 1분 표본을 기록하고 upsert한 행 수를 돌려준다. 정규장 밖이거나
    본주 값을 못 얻으면 0(아무것도 쓰지 않음)."""
    now_kst = (now or dt.datetime.now(KST)).astimezone(KST)
    if is_market_closed(now_kst):
        return 0
    sample_time = now_kst.replace(second=0, microsecond=0)

    quotes: dict[str, dict[str, float | None]] = {}
    async with KiwoomClient() as client:
        try:
            underlying = await _fetch_underlying(client)
        except Exception as e:  # noqa: BLE001
            logger.warning("etf-pair: 본주 조회 실패: %s", e)
            return 0
        if underlying is None or underlying["change_rate"] is None:
            return 0
        for code in PAIR_ETFS:
            try:
                quote = await _fetch_quote(client, code)
            except Exception as e:  # noqa: BLE001 - ETF 하나 실패가 나머지를 막지 않도록
                logger.warning("etf-pair: %s 조회 실패: %s", code, e)
                continue
            if quote is not None:
                quotes[code] = quote
    if not quotes:
        return 0

    navs = await _fetch_navs()
    rate = underlying["change_rate"]
    for code, quote in quotes.items():
        multiple = PAIR_ETFS[code]
        nav = navs.get(code)
        theory = compute_theory_price(quote["base_price"], multiple, rate)
        values = dict(
            time=sample_time,
            code=code,
            multiple=multiple,
            price=quote["price"],
            base_price=quote["base_price"],
            nav=nav,
            underlying_price=underlying["price"],
            underlying_change_rate=rate,
            theory_price=theory,
            premium_pct=compute_premium_pct(quote["price"], nav),
            lag_pct=compute_lag_pct(quote["price"], theory),
        )
        stmt = pg_insert(EtfPairSample).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[EtfPairSample.time, EtfPairSample.code],
            set_={k: stmt.excluded[k] for k in values if k not in ("time", "code")},
        )
        await session.execute(stmt)
    await session.commit()
    return len(quotes)
