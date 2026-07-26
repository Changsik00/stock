"""종목별 프로그램매매(ka90013) 파싱 — PLAN.md §5.29.

**REGISTRY에 등록하지 않는다(의도)** — `collectors/` 아래 다른 모듈들과 달리 이
모듈은 주기 배치 잡이 아니라 **순수 파싱 함수만** 제공한다. 실제 조회/캐시는
`routers/stocks.py`의 `_ensure_program_trade_cached`가 담당한다(같은 파일의
`_ensure_flows_cached`(종목별 수급, ka10059)와 완전히 동일한 온디맨드+DB캐시
철학 — "종목상세를 누가 열 때만 그 종목 데이터를 받아 캐싱"). 판단 근거(PLAN.md
§5.29-3 완료 기준 논의):

- `models.py`의 `ProgramTrade`는 이미 종목별(code, date) PK라 `stock_flow`와
  완전히 같은 모양이다 — `_ensure_flows_cached`가 이미 정확히 이 문제(종목별
  외부 데이터, DB 캐시, 장중 쿨다운 재조회)를 풀어 놓았으므로 그 패턴을 그대로
  재사용하는 것이 새 주기적 스윕 잡(`live_refresh.py::_run_stock_flow_scan`
  방식, PLAN.md §5.20)을 또 만드는 것보다 단순하다.
- `_run_stock_flow_scan`류 스윕 잡은 "후보군(관심종목/거래대금 상위) 전체를
  주기적으로 갱신해 대시보드 랭킹에 바로 반영"이 필요할 때 쓰는 패턴인데,
  프로그램매매는 아직 그런 랭킹/스크리닝 소비처가 없다(§5.29-4는 종목상세
  모달 섹션 하나뿐) — 온디맨드로 시작하고, 나중에 시장 전체 스크리닝(예: "오늘
  프로그램 순매수 상위 종목") 요구가 생기면 그때 스윕 잡으로 승격하면 된다.

collect_fn 계약(collectors/base.py의 REGISTRY 진입점)은 따르지 않는다 — 이
모듈의 함수들은 세션도 커밋도 모른다(순수 파싱), DB I/O는 호출측
(routers/stocks.py)이 전담한다.
"""

from __future__ import annotations

import datetime as dt
import logging

logger = logging.getLogger(__name__)


def _parse_signed_amount(raw: object) -> int | None:
    """ka90013 숫자 필드(`prm_netprps_amt` 등)는 음수일 때 부호가 두 번 겹치는
    버그가 있다(예: `"--676861"` = -676861, 양수는 `"+182002"`처럼 홑부호) —
    `clients/kiwoom.py` 모듈 docstring "ka90013 ... 부호 인코딩 버그" 절 참고.
    표준 `int()`는 이중 부호를 그대로 넘기면 `ValueError`를 던지므로, 선행
    부호 문자를 전부 읽어(개수 무관) "하나라도 '-'가 있으면 음수"로 판정한 뒤
    나머지 숫자에 부호를 적용한다."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    i = 0
    negative = False
    while i < len(text) and text[i] in "+-":
        if text[i] == "-":
            negative = True
        i += 1
    digits = text[i:]
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        logger.warning("program_stock: ka90013 숫자 필드 파싱 실패, None 처리: %r", raw)
        return None
    return -value if negative else value


def parse_program_trade_rows(data: dict, code: str) -> list[dict]:
    """ka90013 응답 body -> `ProgramTrade` upsert용 행 리스트.

    `clients/kiwoom.py`의 "ka90013 실측 확정" 절 근거: 이 TR은 차익/비차익
    분리 필드가 없고 `prm_netprps_amt`(순매수 총계) 하나만 준다 — 그래서
    `arb_net`/`non_arb_net`는 항상 `None`으로 채우고 `total_net`만 실값을
    채운다(스키마 변경 없이 기존 컬럼에 맞추는 PLAN.md §5.29 판단).

    Returns ``[{"code": str, "date": dt.date, "arb_net": None, "non_arb_net":
    None, "total_net": int|None}, ...]`` — 날짜 파싱에 실패한 행은 건너뛴다.
    """
    out: list[dict] = []
    for row in data.get("stk_daly_prm_trde_trnsn") or []:
        date_str = row.get("dt")
        if not date_str:
            continue
        try:
            row_date = dt.datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            logger.warning("program_stock: ka90013 dt 파싱 실패, 건너뜀: %r", date_str)
            continue
        out.append(
            {
                "code": code,
                "date": row_date,
                "arb_net": None,
                "non_arb_net": None,
                "total_net": _parse_signed_amount(row.get("prm_netprps_amt")),
            }
        )
    return out
