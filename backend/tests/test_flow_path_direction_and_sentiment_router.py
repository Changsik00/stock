"""Integration tests for GET /api/markets/flow-path?direction= and
GET /api/markets/sentiment (PLAN.md §4.6 3.6-4).

Same pattern as tests/test_flow_rank_router.py: real dev Postgres via
app.db.async_session_factory, throwaway FastAPI app including only this router,
test rows dated 2099-* (far outside any real backfill) cleaned up in teardown.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session_factory, engine
from app.models import EtfStat, FlowPath, FlowRank, MarketBreadth
from app.routers import markets
from app.routers.flow_rank import router

TEST_DATE = dt.date(2099, 1, 1)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(FlowPath.__table__.delete().where(FlowPath.date == TEST_DATE))
        await session.execute(FlowRank.__table__.delete().where(FlowRank.date == TEST_DATE))
        await session.execute(
            MarketBreadth.__table__.delete().where(MarketBreadth.date == TEST_DATE)
        )
        await session.execute(EtfStat.__table__.delete().where(EtfStat.date == TEST_DATE))
        await session.commit()


@pytest.fixture
async def seeded_flow_path():
    await _clear_test_rows()
    async with async_session_factory() as session:
        rows = [
            # (code, direct_net, via_etf_net)
            ("900001", 100, 500),  # 유입 상위
            ("900002", None, 300),  # 유입
            ("900003", -50, -700),  # 유출 상위 1위 (가장 큰 음수)
            ("900004", None, -200),  # 유출 2위
        ]
        for code, direct_net, via_etf_net in rows:
            stmt = pg_insert(FlowPath).values(
                code=code, date=TEST_DATE, direct_net=direct_net, via_etf_net=via_etf_net, top_etfs=None
            )
            await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


@pytest.fixture
async def seeded_sentiment_inputs():
    await _clear_test_rows()
    async with async_session_factory() as session:
        await session.execute(
            pg_insert(MarketBreadth).values(
                market="kospi", date=TEST_DATE, adv=600, dec=300, flat=100, limit_up=0, limit_down=0
            )
        )
        await session.execute(
            pg_insert(MarketBreadth).values(
                market="kosdaq", date=TEST_DATE, adv=400, dec=400, flat=200, limit_up=0, limit_down=0
            )
        )
        for i, (investor, side, net_value) in enumerate(
            [
                ("foreign", "buy", 1000),
                ("institution", "buy", 500),
                ("foreign", "sell", 400),
                ("institution", "sell", 100),
            ]
        ):
            await session.execute(
                pg_insert(FlowRank).values(
                    date=TEST_DATE,
                    investor=investor,
                    side=side,
                    rank=i + 1,
                    code=f"90000{i}",
                    name=f"테스트{i}",
                    net_value=net_value,
                    quantity=1,
                    turnover=1.0,
                    is_etf=False,
                    market="kospi",
                )
            )
        await session.execute(
            pg_insert(EtfStat).values(code="069500", date=TEST_DATE, nav=10000.0, aum=100000, net_inflow=2000)
        )
        await session.commit()
    yield
    await _clear_test_rows()


async def test_flow_path_direction_in_matches_default_behavior(seeded_flow_path):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        default_resp = await client.get("/api/markets/flow-path", params={"days": 1, "limit": 10})
        explicit_resp = await client.get(
            "/api/markets/flow-path", params={"days": 1, "limit": 10, "direction": "in"}
        )

    assert default_resp.status_code == 200
    default_body = default_resp.json()
    explicit_body = explicit_resp.json()
    assert default_body["direction"] == "in"
    # direction 필드를 제외하면 하위호환 — 기본 호출과 direction=in 명시 호출이 동일.
    assert default_body["rows"] == explicit_body["rows"]
    codes_in_order = [r["code"] for r in default_body["rows"]]
    assert codes_in_order.index("900001") < codes_in_order.index("900003")


async def test_flow_path_direction_out_returns_only_negative_ascending(seeded_flow_path):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/markets/flow-path", params={"days": 1, "limit": 10, "direction": "out"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "out"
    assert [r["code"] for r in body["rows"]] == ["900003", "900004"]
    assert [r["via_etf_net"] for r in body["rows"]] == [-700, -200]


async def test_flow_path_rejects_unknown_direction():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/flow-path", params={"direction": "sideways"})

    assert resp.status_code == 400


async def test_sentiment_combines_breadth_flow_etf_components(seeded_sentiment_inputs, monkeypatch):
    # PLAN.md §5.5-4부터 market_sentiment의 breadth 요소는 breadth/live(실제 네이버
    # 호출)를 우선 시도한다 — 이 테스트는 seeded_sentiment_inputs로 심어둔 EOD
    # MarketBreadth(TEST_DATE=2099-01-01) 경로를 검증하려는 것이므로, 장을 강제로
    # "마감" 상태로 만들어 라이브 호출 자체를 건너뛰게 한다(test_markets_breadth_router.py
    # 의 `_force_market_open`과 반대 방향의 동일한 monkeypatch 패턴 — 실네트워크 호출 없이
    # EOD 폴백 경로만 결정적으로 재현).
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/sentiment")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approx"] is True

    breadth = body["components"]["breadth"]
    flow = body["components"]["flow"]
    etf = body["components"]["etf"]

    # breadth: adv=1000, dec=700, flat=300 (kospi+kosdaq 합산) -> (1000-700)/2000*100 = 15.0
    assert breadth["date"] == TEST_DATE.isoformat()
    assert breadth["adv"] == 1000
    assert breadth["dec"] == 700
    assert breadth["flat"] == 300
    assert breadth["score"] == 15.0
    assert breadth["source"] == "eod"

    # flow: buy_sum=1500, sell_sum=500 -> (1500-500)/2000*100 = 50.0
    assert flow["date"] == TEST_DATE.isoformat()
    assert flow["buy_sum"] == 1500
    assert flow["sell_sum"] == 500
    assert flow["score"] == 50.0

    # etf: net_inflow_sum=2000, aum_sum=100000 -> 2000/100000*100 = 2.0
    assert etf["date"] == TEST_DATE.isoformat()
    assert etf["net_inflow_sum"] == 2000
    assert etf["aum_sum"] == 100000
    assert etf["score"] == 2.0

    # weighted: 15*0.4 + 50*0.35 + 2*0.25 = 6 + 17.5 + 0.5 = 24.0
    assert body["score"] == 24.0
    assert breadth["weight"] == 0.4
    assert flow["weight"] == 0.35
    assert etf["weight"] == 0.25

    # 반환된 score/weight가 app.sentiment.compute_sentiment의 재정규화 로직과 일치하는지도
    # (여긴 순수 로직 자체는 tests/test_sentiment.py가 커버 — 여기서는 라우터가 그 결과를
    # 그대로 전달하는지만 재확인) 간단히 크로스체크한다.
    from app.sentiment import compute_sentiment  # noqa: PLC0415

    expected_score, expected_weights = compute_sentiment(15.0, 50.0, 2.0)
    assert body["score"] == expected_score
    assert breadth["weight"] == expected_weights["breadth"]


async def test_sentiment_prefers_live_breadth_when_market_open(seeded_sentiment_inputs, monkeypatch):
    """PLAN.md §5.5-4 — 장중이고 breadth/live 조회가 성공하면 breadth 요소는 그
    라이브 adv/dec/flat을 쓴다(EOD MarketBreadth에 심어둔 TEST_DATE 값이 아니라).
    flow/etf는 이번 범위 밖이라 여전히 EOD(seeded_sentiment_inputs) 그대로다 —
    "완전 대체가 아니라 우선순위 추가"라는 설계를 그대로 검증한다. 실네트워크
    호출을 막기 위해 `_fetch_breadth_blocking`을 가짜 데이터로 monkeypatch한다
    (test_markets_breadth_router.py와 동일한 패턴)."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    live_kospi = {"adv": 900, "dec": 100, "flat": 50, "limit_up": 3, "limit_down": 0}
    live_kosdaq = {"adv": 700, "dec": 200, "flat": 30, "limit_up": 1, "limit_down": 0}

    def fake_fetch(market: str) -> dict:
        return live_kospi if market == "kospi" else live_kosdaq

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)
    # 모듈 전역 캐시가 이전 테스트(장 마감 케이스)의 값을 들고 있을 수 있으므로
    # 비워서 이 테스트가 실제로 fake_fetch 경로를 타도록 강제한다.
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/sentiment")

    assert resp.status_code == 200
    body = resp.json()
    breadth = body["components"]["breadth"]

    # adv=900+700=1600, dec=100+200=300, flat=50+30=80 -> 라이브 소스, EOD(1000/700/300) 아님.
    assert breadth["source"] == "live"
    assert breadth["adv"] == 1600
    assert breadth["dec"] == 300
    assert breadth["flat"] == 80
    assert breadth["date"] == dt.datetime.now(markets.KST).date().isoformat()

    # flow/etf는 이번 범위 밖 — 여전히 seeded EOD(TEST_DATE) 그대로.
    assert body["components"]["flow"]["date"] == TEST_DATE.isoformat()
    assert body["components"]["etf"]["date"] == TEST_DATE.isoformat()


# 참고: "세 요소 전부 데이터 없음 -> score None" 케이스는 이 파일에서 통합테스트로
# 검증하지 않는다 — 이 라우터는 오늘 날짜 기준 lookback으로 실데이터를 함께 조회하므로
# (dev DB에 실제로 오늘자 breadth/flow_rank/etf_stats가 이미 적재돼 있을 수 있음),
# "전부 없음"을 재현하려면 실데이터까지 지워야 해 위험하다. 그 동작 자체(None 처리·
# 재정규화)는 tests/test_sentiment.py의 순수 단위테스트가 이미 커버한다.
