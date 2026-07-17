"""DB에 적재된 시세/매크로 데이터를 정적 JSON 스냅샷으로 내보낸다 (GitHub Pages 배포용).

CI가 DB로 데이터를 수집한 뒤, 이 스크립트로 정적 JSON 파일을 생성하면 정적 빌드된
프런트가 `/api/*` 대신 이 파일들을 fetch한다. 스키마를 라이브 API와 byte-for-byte
동일하게 맞추기 위해 쿼리 로직을 재구현하지 않고 실제 라우터 함수(`app.routers.markets`,
`app.routers.macro`)를 그대로 재사용한다 — 컴포넌트 변경 없이 프런트가 그대로 동작하게
하는 것이 핵심이므로, 반환 dict를 조립하는 방식도 각 라우터 path 함수와 동일하게 맞춘다.

- markets-{kospi,kosdaq,futures}.json: GET /api/markets/{market}/series?days=1095 와 동일
  ({"market", "days", "prices", "flows"}). market_flow가 0행(KRX 로그인 미설정)이면
  flows는 빈 dict — 정상 동작.
- macro.json: GET /api/macro/series?ids=...&days=1095 와 동일 ({"days", "series"}).
- flow-rank-{foreign,institution}.json: GET /api/markets/flow-rank?investor=X&side=buy&days=30
  와 동일 ({"investor", "side", "days", "dates"}) — side=buy가 기본값이라 파일명은
  기존 그대로 유지한다(하위호환, PLAN.md §6 3.5-2b). days는 라우터가 le=30으로
  제한하므로(PLAN.md §4.5 — flow_rank는 소스 제약상 배치를 반복 실행한 날짜만
  누적된다) 다른 시리즈처럼 DEFAULT_DAYS(1095)를 쓰지 않고 30으로 고정한다.
- flow-rank-{foreign,institution}-sell.json: 위와 동일하되 side=sell (§6 3.5-2b
  순매도 확장). 프런트(api.js fetchFlowRank)가 side에 따라 파일명을 고른다.
- flow-path.json: GET /api/markets/flow-path?days=30&limit=50&direction=in 과 동일
  ({"date", "days", "direction", "rows"}) — flow_path도 flow_rank와 같은 이유로 날짜
  누적이 느리므로 FLOW_RANK_DAYS(30)를 그대로 재사용한다. 프런트(api.js
  fetchFlowPath)는 정적 모드에서 direction="in"일 때 이 파일을 그대로 반환한다
  (서버처럼 "최신 날짜 1개"만 담겨 있으므로 추가 슬라이싱이 필요 없음).
- flow-path-out.json: GET /api/markets/flow-path?days=30&limit=50&direction=out 과
  동일 — ETF 경유 유출(via_etf_net 오름차순, 가장 큰 유출이 1등) 상위 (§4.6 3.6-4).
  프런트는 direction="out"일 때 이 파일을 쓴다.
- sentiment.json: GET /api/markets/sentiment 와 동일 ({"score", "approx",
  "components": {"breadth", "flow", "etf"}}) — 시장 종합 매수세/매도세 게이지
  (§4.6 3.6-4). days 파라미터가 없다(요소별로 라우터가 자체 lookback을 쓴다).
- value-rank.json: GET /api/markets/value-rank?market=all&days=30 과 동일
  ({"market", "date", "days", "rows"}) — market=all 하나만 덤프하고 kospi/kosdaq
  필터는 프런트(api.js fetchValueRank)가 rows.market으로 걸러낸다 (PLAN.md §4.6
  3.6-1). value_rank도 flow_rank처럼 날짜 누적형 스냅샷이라 FLOW_RANK_DAYS(30)를
  재사용한다.
- breadth-{kospi,kosdaq}.json: GET /api/markets/{market}/breadth?days=400 과 동일
  ({"market", "days", "series"}) — 일별 시계열 (§3.5/§4.6 3.6-2). 라우터 상한이
  le=400이라 DEFAULT_DAYS(1095) 대신 400을 쓴다(소스 제약상 배치 반복 실행으로만
  누적되므로 당분간은 어차피 수십 행 이하). 정적 모드의 "장중" 배지는 이 파일의
  마지막 행으로 대체된다(api.js fetchBreadthLive).
- groups-{upjong,theme}.json: GET /api/groups?type=X 와 동일 (list[{name,
  change_rate, value, market_sum}], 최신 날짜 스냅샷 — PLAN.md §4.6 3.6-3 트리맵).
- meta.json: {"generated_at": <UTC ISO8601>} — 생성 시각만 기록.

Usage: python -m scripts.export_static --out-dir ../frontend/public/data [--days 1095]
"""

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("export_static")

from app.db import async_session_factory  # noqa: E402
from app.routers.flow_rank import INVESTORS as FLOW_RANK_INVESTORS  # noqa: E402
from app.routers.flow_rank import flow_path_top  # noqa: E402
from app.routers.flow_rank import flow_rank_series  # noqa: E402
from app.routers.flow_rank import market_sentiment  # noqa: E402
from app.routers.flow_rank import value_rank_top  # noqa: E402
from app.routers.groups import GROUP_TYPES  # noqa: E402
from app.routers.groups import group_snapshot_list  # noqa: E402
from app.routers.macro import macro_series  # noqa: E402
from app.routers.markets import BREADTH_MARKETS  # noqa: E402
from app.routers.markets import _build_flows, _build_prices, market_breadth_series  # noqa: E402

MARKETS = ("kospi", "kosdaq", "futures")
BREADTH_DAYS = 400  # 라우터 상한(le=400) — docstring 참고
FLOW_RANK_SIDES = ("buy", "sell")
MACRO_IDS = (
    "usdkrw,wti,brent,investor_deposit,credit_loan_kospi,"
    "credit_loan_kosdaq,lending_balance"
)
DEFAULT_DAYS = 1095
FLOW_RANK_DAYS = 30
FLOW_PATH_LIMIT = 50


def _write_json(path: Path, data: dict | list) -> int:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path.stat().st_size


async def main(out_dir: str, days: int = DEFAULT_DAYS) -> None:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    async with async_session_factory() as session:
        for market in MARKETS:
            result = await _build_prices(market, days, session)
            result["prices"] = result.pop("series")
            result["flows"] = await _build_flows(market, days, session)

            file_path = out_path / f"markets-{market}.json"
            size = _write_json(file_path, result)
            logger.info(
                "%s: %d개 가격 행, %d개 flow 투자자 -> %s (%d bytes)",
                market,
                len(result["prices"]),
                len(result["flows"]),
                file_path,
                size,
            )

        for investor in sorted(FLOW_RANK_INVESTORS):
            for side in FLOW_RANK_SIDES:
                flow_result = await flow_rank_series(
                    investor=investor, side=side, days=FLOW_RANK_DAYS, session=session
                )
                suffix = "" if side == "buy" else f"-{side}"
                flow_path = out_path / f"flow-rank-{investor}{suffix}.json"
                size = _write_json(flow_path, flow_result)
                logger.info(
                    "flow-rank(%s/%s): %d개 날짜 -> %s (%d bytes)",
                    investor,
                    side,
                    len(flow_result["dates"]),
                    flow_path,
                    size,
                )

        flow_path_result = await flow_path_top(
            days=FLOW_RANK_DAYS, limit=FLOW_PATH_LIMIT, direction="in", session=session
        )
        flow_path_path = out_path / "flow-path.json"
        size = _write_json(flow_path_path, flow_path_result)
        logger.info(
            "flow-path(in): date=%s, %d개 종목 -> %s (%d bytes)",
            flow_path_result["date"],
            len(flow_path_result["rows"]),
            flow_path_path,
            size,
        )

        flow_path_out_result = await flow_path_top(
            days=FLOW_RANK_DAYS, limit=FLOW_PATH_LIMIT, direction="out", session=session
        )
        flow_path_out_path = out_path / "flow-path-out.json"
        size = _write_json(flow_path_out_path, flow_path_out_result)
        logger.info(
            "flow-path(out): date=%s, %d개 종목 -> %s (%d bytes)",
            flow_path_out_result["date"],
            len(flow_path_out_result["rows"]),
            flow_path_out_path,
            size,
        )

        sentiment_result = await market_sentiment(session=session)
        sentiment_path = out_path / "sentiment.json"
        size = _write_json(sentiment_path, sentiment_result)
        logger.info(
            "sentiment: score=%s -> %s (%d bytes)",
            sentiment_result["score"],
            sentiment_path,
            size,
        )

        value_rank_result = await value_rank_top(
            market="all", days=FLOW_RANK_DAYS, session=session
        )
        value_rank_path = out_path / "value-rank.json"
        size = _write_json(value_rank_path, value_rank_result)
        logger.info(
            "value-rank: date=%s, %d개 종목 -> %s (%d bytes)",
            value_rank_result["date"],
            len(value_rank_result["rows"]),
            value_rank_path,
            size,
        )

        for market in sorted(BREADTH_MARKETS):
            breadth_result = await market_breadth_series(
                market=market, days=BREADTH_DAYS, session=session
            )
            breadth_path = out_path / f"breadth-{market}.json"
            size = _write_json(breadth_path, breadth_result)
            logger.info(
                "breadth(%s): %d개 날짜 -> %s (%d bytes)",
                market,
                len(breadth_result["series"]),
                breadth_path,
                size,
            )

        for group_type in sorted(GROUP_TYPES):
            groups_result = await group_snapshot_list(type=group_type, date=None, session=session)
            groups_path = out_path / f"groups-{group_type}.json"
            size = _write_json(groups_path, groups_result)
            logger.info(
                "groups(%s): %d개 그룹 -> %s (%d bytes)",
                group_type,
                len(groups_result),
                groups_path,
                size,
            )

        macro_result = await macro_series(ids=MACRO_IDS, days=days, session=session)
        macro_path = out_path / "macro.json"
        size = _write_json(macro_path, macro_result)
        counts = {sid: len(rows) for sid, rows in macro_result["series"].items()}
        logger.info("macro: %s -> %s (%d bytes)", counts, macro_path, size)

        meta = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        meta_path = out_path / "meta.json"
        size = _write_json(meta_path, meta)
        logger.info("meta: %s -> %s (%d bytes)", meta, meta_path, size)

    logger.info("정적 JSON 내보내기 완료: %s", out_path.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default="../frontend/public/data",
        help="출력 디렉터리 (기본: ../frontend/public/data)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"조회할 기간(일) (기본: {DEFAULT_DAYS})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.out_dir, args.days))
