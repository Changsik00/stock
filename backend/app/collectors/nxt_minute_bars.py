"""NXT(넥스트레이드) 5분봉 + KRX 정규장 종가 적재기 (PLAN.md §5.107) — 참고용 데이터, 매매 입력 아님.

**목적**: "밤사이 NQ로 오늘 프리마켓 시작점 기대"(`quant/premarket_outlook.py`)의 학습 데이터.
키움 분봉 TR은 과거를 무한정 주지 않으므로 지금부터 쌓는다.

**소스**(키움 ka10080 주식분봉차트, 읽기 전용 시세 TR만):
- NXT 5분봉: ``stk_cd = 종목코드 + "_NX"``, ``tic_scope="5"`` -> 08:00~19:55 -> ``nxt_intraday_bar``.
  ``cntr_tm``은 봉 시작 시각, 가격은 ``+``/``-`` 부호 접두를 파서가 뗀다. ETF(069500, 0167A0)는 NXT 미상장이라
  빈 응답이라 대상이 아니다.
- KRX 정규장 종가: 같은 TR을 종목코드 그대로(통합 시세) 부르면 15:30 봉(종가 단일가 체결)의 종가가 KRX 종가다
  -> ``krx_regular_close``. 일봉(ka10081/네이버)의 종가는 NXT 애프터마켓(~20:00) 마지막 체결가까지 포함해
  장 마감 후에도 계속 바뀌므로 기준가로 쓰지 않는다(models.KrxRegularClose 참고).

**페이지네이션**: 한 페이지 900행(약 6거래일). 평소에는 종목당 1페이지만 upsert(진행 중 봉 덮어쓰기),
테이블이 비었거나 공백이 있으면 ``cont-yn``/``next-key``로 ``BACKFILL_FLOOR``까지(상한 12페이지) 거슬러 올라간다.

**스케줄**: scheduler.py 전용 크론(평일 KST 08:12 프리 시가 확보용 / 08:55 / 20:05). 18:00 본배치 제외.
collect_log는 ``run_job``이 기록한다(이 함수는 commit하지 않는다 — 종목·단계별 SAVEPOINT로 실패를 격리).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import KiwoomClient
from ..krx_calendar import is_krx_closed
from ..models import KrxRegularClose, NxtIntradayBar
from ..quant.premarket_outlook import OUTLOOK_CODES, prev_open_day
from .base import REGISTRY
from .market_minute_bars import _upsert, parse_all_days, to_rows

logger = logging.getLogger(__name__)

NXT_SUFFIX = "_NX"
BACKFILL_FLOOR = dt.date(2026, 8, 26)  # 사전 분석(NQ 샘플 적재 시작 2026-08-27)과 맞춘 소급 하한
BACKFILL_MAX_PAGES = 12
NXT_FIRST_TIME = "0800"
NXT_LAST_TIME = "2000"  # 마지막 봉 시작은 19:55
KRX_CLOSE_BAR_TIME = "1530"  # KRX 종가 단일가가 체결되는 5분봉 시작


def parse_nxt_rows(code: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """ka10080 `_NX` 응답 한 페이지 -> nxt_intraday_bar 행(전 일자). 부호 접두는 파서가 제거.
    NXT 시간대(08:00~19:55) 밖이거나 시가가 없는 봉은 버린다."""
    rows = to_rows(code, parse_all_days(data, "ka10080"))
    return [
        r
        for r in rows
        if NXT_FIRST_TIME <= r["time"] < NXT_LAST_TIME and r.get("open") and r["open"] > 0
    ]


def parse_krx_close_rows(code: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """종목코드 그대로(통합 시세) 5분봉 응답 -> 일자별 15:30 봉 종가(KRX 정규장 종가) 행."""
    out = []
    for b in parse_all_days(data, "ka10080"):
        if b.get("time") == KRX_CLOSE_BAR_TIME and b.get("close") and b["close"] > 0:
            out.append(
                {"code": code, "date": dt.datetime.strptime(b["date"], "%Y%m%d").date(), "close": int(b["close"])}
            )
    return out


def plan_pages(latest: dt.date | None, target_date: dt.date) -> tuple[int, dt.date | None]:
    """(최대 페이지 수, 이 일자에 닿으면 멈춤). 비었으면 하한까지 백필, 직전 거래일까지 차 있으면 1페이지,
    공백이 있으면 DB 최신 일자에 닿을 때까지."""
    if latest is None:
        return BACKFILL_MAX_PAGES, BACKFILL_FLOOR
    if latest >= prev_open_day(target_date, is_krx_closed):
        return 1, None
    return BACKFILL_MAX_PAGES, max(latest, BACKFILL_FLOOR)


async def _walk_pages(client: KiwoomClient, stk_cd: str, max_pages: int, stop_date: dt.date | None, handler) -> int:
    """5분봉 페이지를 최신->과거로 넘기며 handler(data)->행 수 를 호출. 가장 오래된 봉 일자가 stop_date 이하이거나
    연속 키가 없으면 종료. 반환=handler 합."""
    total, cont_yn, next_key = 0, None, None
    for _page in range(max_pages):
        data, headers = await client.stock_minute_chart(stk_cd, "5", cont_yn=cont_yn, next_key=next_key)
        bars = parse_all_days(data, "ka10080")
        if not bars:
            break
        total += await handler(data)
        oldest = min(dt.datetime.strptime(b["date"], "%Y%m%d").date() for b in bars)
        if stop_date is not None and oldest <= stop_date:
            break
        if headers.get("cont-yn") != "Y" or not headers.get("next-key"):
            break
        cont_yn, next_key = "Y", headers["next-key"]
    return total


async def _upsert_krx_close(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = pg_insert(KrxRegularClose).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[KrxRegularClose.code, KrxRegularClose.date], set_={"close": stmt.excluded.close}
    )
    await session.execute(stmt)


async def collect_nxt_minute_bars(session: AsyncSession, target_date: dt.date | None = None) -> tuple[int, str | None]:
    """대상 5종목의 NXT 5분봉 + KRX 정규장 종가 upsert. 반환=(쓴 행 수, 실패 메모).
    종목·단계별 실패는 SAVEPOINT로 격리하고, 전부 실패하면 예외를 올려 collect_log에 fail로 남긴다."""
    target_date = target_date or dt.date.today()
    total, fails, units = 0, [], 0
    async with KiwoomClient() as client:
        for code in OUTLOOK_CODES:
            # 1) NXT 5분봉
            units += 1
            try:
                async with session.begin_nested():
                    latest = (
                        await session.execute(select(func.max(NxtIntradayBar.date)).where(NxtIntradayBar.code == code))
                    ).scalar_one()
                    max_pages, stop = plan_pages(latest, target_date)

                    async def _h(data, _code=code):
                        rows = parse_nxt_rows(_code, data)
                        await _upsert(session, NxtIntradayBar, rows)
                        return len(rows)

                    n = await _walk_pages(client, code + NXT_SUFFIX, max_pages, stop, _h)
                total += n
                logger.info("nxt_minute_bars: %s NXT %d행 upsert", code, n)
            except Exception as e:  # noqa: BLE001 - 종목 하나의 실패가 나머지에 번지지 않게
                fails.append(f"{code} nxt: {e}")
                logger.warning("nxt_minute_bars: %s NXT 실패(무시): %s", code, e)
            # 2) KRX 정규장 종가(통합 시세 5분봉의 15:30 봉)
            units += 1
            try:
                async with session.begin_nested():
                    latest = (
                        await session.execute(select(func.max(KrxRegularClose.date)).where(KrxRegularClose.code == code))
                    ).scalar_one()
                    max_pages, stop = plan_pages(latest, target_date)

                    async def _hk(data, _code=code):
                        rows = parse_krx_close_rows(_code, data)
                        await _upsert_krx_close(session, rows)
                        return len(rows)

                    n = await _walk_pages(client, code, max_pages, stop, _hk)
                total += n
                logger.info("nxt_minute_bars: %s KRX종가 %d행 upsert", code, n)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{code} krx_close: {e}")
                logger.warning("nxt_minute_bars: %s KRX종가 실패(무시): %s", code, e)
    if fails and len(fails) >= units:
        raise RuntimeError("nxt_minute_bars 전부 실패: " + "; ".join(fails)[:400])
    return total, ("일부 실패: " + "; ".join(fails))[:500] if fails else None


REGISTRY["nxt_minute_bars"] = collect_nxt_minute_bars
