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
- flow-rank-{foreign,institution}.json: GET /api/markets/flow-rank?investor=X&days=30 와
  동일 ({"investor", "days", "dates"}). days는 라우터가 le=30으로 제한하므로(PLAN.md
  §4.5 — flow_rank는 소스 제약상 배치를 반복 실행한 날짜만 누적된다) 다른 시리즈처럼
  DEFAULT_DAYS(1095)를 쓰지 않고 30으로 고정한다.
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
from app.routers.flow_rank import flow_rank_series  # noqa: E402
from app.routers.macro import macro_series  # noqa: E402
from app.routers.markets import _build_flows, _build_prices  # noqa: E402

MARKETS = ("kospi", "kosdaq", "futures")
MACRO_IDS = (
    "usdkrw,wti,brent,investor_deposit,credit_loan_kospi,"
    "credit_loan_kosdaq,lending_balance"
)
DEFAULT_DAYS = 1095
FLOW_RANK_DAYS = 30


def _write_json(path: Path, data: dict) -> int:
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
            flow_result = await flow_rank_series(investor=investor, days=FLOW_RANK_DAYS, session=session)
            flow_path = out_path / f"flow-rank-{investor}.json"
            size = _write_json(flow_path, flow_result)
            logger.info(
                "flow-rank(%s): %d개 날짜 -> %s (%d bytes)",
                investor,
                len(flow_result["dates"]),
                flow_path,
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
