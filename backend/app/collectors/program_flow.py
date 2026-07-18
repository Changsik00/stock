"""프로그램매매(차익/비차익) 추이 수집 → macro_series upsert (source='kiwoom').

PLAN.md §4.5-4. REGISTRY["program_flow"]로 등록된다 (macro_series를 그대로
재사용하므로 새 테이블/마이그레이션 없이 collectors/macro.py의
`upsert_series_rows` 헬퍼를 그대로 가져다 쓴다).

**TR**: `ka90010`(프로그램매매추이요청 일자별, `clients/kiwoom.py`의
`program_trading_by_date`). 이 TR은 다른 키움 TR과 달리 "하루 1콜=하루 1행"이
아니라 **한 번 호출에 최근 ~100거래일치가 한꺼번에 온다**(요청 `date` 이하로
최신순 정렬, `cont-yn`/`next-key`로 더 과거까지 연속조회 가능 — 2026-07-19
실호출로 2016년까지 페이지네이션 가능함을 확인). 그래서 이 모듈의
`collect(session, target_date)`는 시장당 API 1콜만으로 target_date를 포함한
최근 100거래일 전체를 upsert한다(자연스러운 "최근 구간 자가치유" 효과 —
직전 배치가 실패했던 날짜도 다음날 배치가 알아서 다시 채운다).

**시장 코드 확정 (2026-07-19 실호출, 공식 문서 오타 정정)**: `ka90010`의
`mrkt_tp`는 문서상 코스피 "P001_AL01"(KRX+NXT 통합), 코스닥 "P001_AL02"로
표기되어 있으나(여러 독립 소스가 동일 오타를 그대로 베낌 — 원본 PDF 자체의
오타로 추정), **실제로 "P001_AL02"를 호출하면 코스피(P001_AL01)와 완전히
동일한 응답이 돌아온다**(같은 날 같은 값 — `mrkt_tp` 접두사 "P001"만 읽고
코스피로 처리하는 것으로 보임). 코스닥 값 1("P10102")·값 2("P101_NX02")와
같은 접두사 패턴을 따르는 **"P101_AL02"로 바꿔 호출하면 코스피와 다른,
코스닥 규모에 맞는 별개 데이터가 돌아옴**을 확인했다 — 이 모듈은
문서값이 아니라 이 실측 정정값(P101_AL02)을 쓴다. (참고: 같은 실호출에서
코스피/코스닥 각각 "거래소구분값 1(KRX만)"과 "3(통합)"이 사실상 동일한
숫자를 반환했다 — 현재 NXT 거래 비중이 프로그램매매 통계에 사실상 영향이
없는 것으로 보이나, 통합 코드가 더 미래 안전하므로 그대로 유지한다.)

**단위**: `amt_qty_tp="1"`(금액, 백만원) 고정 — PLAN.md §4.5-4가 요구하는
"백만원" 단위와 일치. 수량(`amt_qty_tp="2"`)은 호출 예산 절약을 위해 받지
않는다(다른 macro_series 시리즈처럼 금액만 적재).

**series 키 설계 (macro_series.series는 VARCHAR(20) — DB 실측 확인,
마이그레이션 금지 제약과 맞물려 PLAN.md 초안의 `program_arb_kospi` 등은
20자를 넘겨(`program_nonarb_kosdaq`=21자) 그대로 쓸 수 없었다. 접두사를
`prog_`로 줄여 전부 20자 이내로 맞춘다):
    prog_arb_kospi (14), prog_arb_kosdaq (15),
    prog_nonarb_kospi (17), prog_nonarb_kosdaq (18)
값은 순매수(차익=`dfrt_trde_netprps`, 비차익=`ndiffpro_trde_netprps`),
부호 포함 백만원, source='kiwoom'.

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
run_job이 전담한다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import KiwoomClient
from .base import REGISTRY
from .macro import upsert_series_rows

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

SOURCE = "kiwoom"

# mrkt_tp: ka90010 요청 파라미터. "AL" 접미사가 거래소구분값 3(KRX+NXT 통합).
# 코스닥 값은 문서(P001_AL02)가 아니라 실호출로 정정한 값(P101_AL02) —
# 모듈 docstring "시장 코드 확정" 절 참고.
MARKET_TO_MRKT_TP = {"kospi": "P001_AL01", "kosdaq": "P101_AL02"}

SERIES_ARB = {"kospi": "prog_arb_kospi", "kosdaq": "prog_arb_kosdaq"}
SERIES_NONARB = {"kospi": "prog_nonarb_kospi", "kosdaq": "prog_nonarb_kosdaq"}


def _parse_int(raw: object) -> int | None:
    """ka90010 숫자 필드는 부호(+/-) 포함 문자열로 온다(예: "+512", "-0"). int()가
    선행 '+'를 그대로 처리하므로 콤마만 제거하면 된다."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        logger.warning("program_flow: ka90010 숫자 필드 파싱 실패, None 처리: %r", raw)
        return None


async def _fetch_page(
    client: KiwoomClient,
    market: str,
    anchor_date: dt.date,
    cont_yn: str | None = None,
    next_key: str | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """ka90010을 한 번 호출해 market의 anchor_date 이하 최근 거래일들을
    {"date", "arb_net", "nonarb_net"} 딕셔너리 리스트로 변환한다.

    Returns:
        (파싱된 행 리스트 — anchor_date에 가까운 순으로, 응답 헤더).
    """
    data, headers = await client.program_trading_by_date(
        mrkt_tp=MARKET_TO_MRKT_TP[market],
        date=anchor_date,
        amt_qty_tp="1",
        cont_yn=cont_yn,
        next_key=next_key,
    )
    raw_rows = data.get("prm_trde_trnsn") or []

    parsed: list[dict] = []
    for row in raw_rows:
        cntr_tm = row.get("cntr_tm")
        if not cntr_tm or len(cntr_tm) < 8:
            continue
        try:
            row_date = dt.datetime.strptime(cntr_tm[:8], "%Y%m%d").date()
        except ValueError:
            logger.warning("program_flow: cntr_tm 파싱 실패, 건너뜀: %r", cntr_tm)
            continue
        parsed.append(
            {
                "date": row_date,
                "arb_net": _parse_int(row.get("dfrt_trde_netprps")),
                "nonarb_net": _parse_int(row.get("ndiffpro_trde_netprps")),
            }
        )
    return parsed, headers


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """kospi/kosdaq의 target_date 이하 최근 거래일들(시장당 API 1콜, ~100거래일)의
    차익/비차익 순매수를 macro_series에 upsert.

    Returns:
        적재(upsert)한 행 수 (시장 2개 x 최근 ~100거래일 x 시리즈 2개 = 최대
        약 400행/콜; 데이터가 비면 그만큼 적게 적재된다).
    """
    rows_written = 0
    async with KiwoomClient() as client:
        for market in MARKETS:
            parsed, _headers = await _fetch_page(client, market, target_date)
            if not parsed:
                logger.info("program_flow: no data for %s %s, skipping", market, target_date)
                continue

            arb_rows = [
                {"date": item["date"], "value": item["arb_net"], "source": SOURCE}
                for item in parsed
            ]
            nonarb_rows = [
                {"date": item["date"], "value": item["nonarb_net"], "source": SOURCE}
                for item in parsed
            ]
            rows_written += await upsert_series_rows(session, arb_rows, SERIES_ARB[market])
            rows_written += await upsert_series_rows(session, nonarb_rows, SERIES_NONARB[market])

    return rows_written


REGISTRY["program_flow"] = collect
