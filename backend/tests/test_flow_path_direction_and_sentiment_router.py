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
from app.models import EtfStat, FlowPath, FlowRank, MarketBreadth, MarketFlow
from app.routers import flow_rank, markets
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
        await session.execute(MarketFlow.__table__.delete().where(MarketFlow.date == TEST_DATE))
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
        # §5.43 신규 futures 요소의 EOD 폴백(market_flow market='k200_futures')용 시드.
        # flow_live_score(-200, 800, -100) = (800-100)/(200+800+100)*100 = 63.6363... -> 63.6.
        for investor, net_value in [("개인", -200), ("외국인", 800), ("기관계", -100)]:
            await session.execute(
                pg_insert(MarketFlow).values(
                    market="k200_futures",
                    date=TEST_DATE,
                    investor=investor,
                    net_value=net_value,
                    net_volume=None,
                    source="naver",
                )
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


async def test_sentiment_combines_all_components_when_market_closed(seeded_sentiment_inputs, monkeypatch):
    # PLAN.md §5.5-4/§5.43부터 market_sentiment의 breadth/flow/futures 세 요소는
    # 각각 live 우선 시도한다 — 이 테스트는 seeded_sentiment_inputs로 심어둔 EOD
    # 픽스처(MarketBreadth/FlowRank/EtfStat/MarketFlow, TEST_DATE=2099-01-01) 경로를
    # 검증하려는 것이므로, 장을 강제로 "마감" 상태로 만들어 세 라이브 호출 모두
    # 건너뛰게 한다(breadth/flow/futures 라이브 게이트가 모두 같은 전역
    # `_market_closed_kst`를 참조하므로 patch 하나로 셋 다 막힌다).
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
    futures = body["components"]["futures"]
    etf = body["components"]["etf"]

    # breadth: adv=1000, dec=700, flat=300 (kospi+kosdaq 합산) -> (1000-700)/2000*100 = 15.0
    assert breadth["date"] == TEST_DATE.isoformat()
    assert breadth["adv"] == 1000
    assert breadth["dec"] == 700
    assert breadth["flat"] == 300
    assert breadth["score"] == 15.0
    assert breadth["source"] == "eod"

    # flow: market_closed=True -> live 게이트가 None -> 옛 flow_rank 랭킹 근사치로 폴백.
    # buy_sum=1500, sell_sum=500 -> (1500-500)/2000*100 = 50.0
    assert flow["date"] == TEST_DATE.isoformat()
    assert flow["buy_sum"] == 1500
    assert flow["sell_sum"] == 500
    assert flow["score"] == 50.0
    assert flow["source"] == "eod"

    # futures(§5.43 신규): market_closed=True -> live 게이트가 None -> market_flow
    # (market='k200_futures') EOD 확정치로 폴백. flow_live_score(-200, 800, -100)
    # = (800-100)/(200+800+100)*100 = 63.6363... -> 63.6
    assert futures["date"] == TEST_DATE.isoformat()
    assert futures["individual"] == -200
    assert futures["foreign"] == 800
    assert futures["institution"] == -100
    assert futures["score"] == 63.6
    assert futures["source"] == "eod"

    # etf: net_inflow_sum=2000, aum_sum=100000 -> 2000/100000*100 = 2.0 (항상 EOD)
    assert etf["date"] == TEST_DATE.isoformat()
    assert etf["net_inflow_sum"] == 2000
    assert etf["aum_sum"] == 100000
    assert etf["score"] == 2.0
    assert etf["source"] == "eod"

    # weighted: 15*0.35 + 50*0.20 + 63.6*0.20 + 2*0.25 = 5.25 + 10 + 12.72 + 0.5 = 28.47 -> 28.5
    assert body["score"] == 28.5
    assert breadth["weight"] == 0.35
    assert flow["weight"] == 0.20
    assert futures["weight"] == 0.20
    assert etf["weight"] == 0.25

    # 반환된 score/weight가 app.sentiment.compute_sentiment의 재정규화 로직과 일치하는지도
    # (여긴 순수 로직 자체는 tests/test_sentiment.py가 커버 — 여기서는 라우터가 그 결과를
    # 그대로 전달하는지만 재확인) 간단히 크로스체크한다.
    from app.sentiment import compute_sentiment  # noqa: PLC0415

    expected_score, expected_weights = compute_sentiment(15.0, 50.0, 63.6, 2.0)
    assert body["score"] == expected_score
    assert breadth["weight"] == expected_weights["breadth"]


async def test_sentiment_prefers_live_breadth_flow_futures_when_market_open(
    seeded_sentiment_inputs, monkeypatch
):
    """PLAN.md §5.5-4/§5.43 — 장중이고 breadth/flow/futures 각각의 live 조회가
    성공하면 세 요소 모두 그 라이브 값을 쓴다(EOD 픽스처가 아니라). etf는 라이브
    소스가 없어 이번에도 EOD(seeded_sentiment_inputs) 그대로다 — "breadth만 완전
    대체가 아니라 우선순위 추가"라는 기존 설계를 flow/futures로 확장 검증한다.
    실네트워크 호출을 막기 위해 각 소스의 blocking/fetch 함수를 가짜 데이터로
    monkeypatch한다(test_markets_breadth_router.py/test_markets_flow_live_router.py와
    동일한 패턴)."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    live_kospi = {"adv": 900, "dec": 100, "flat": 50, "limit_up": 3, "limit_down": 0}
    live_kosdaq = {"adv": 700, "dec": 200, "flat": 30, "limit_up": 1, "limit_down": 0}

    def fake_fetch_breadth(market: str) -> dict:
        return live_kospi if market == "kospi" else live_kosdaq

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch_breadth)

    fake_kospi_flows = [
        {"investor": "개인", "net_value": -1000, "net_volume": None},
        {"investor": "외국인", "net_value": 5000, "net_volume": None},
        {"investor": "기관계", "net_value": -2000, "net_volume": None},
    ]
    fake_kosdaq_flows = [
        {"investor": "개인", "net_value": -500, "net_volume": None},
        {"investor": "외국인", "net_value": 1500, "net_volume": None},
        {"investor": "기관계", "net_value": -300, "net_volume": None},
    ]

    async def fake_fetch_live_flow(client, market, target_date):
        return fake_kospi_flows if market == "kospi" else fake_kosdaq_flows

    monkeypatch.setattr(markets, "fetch_live_flow", fake_fetch_live_flow)

    fake_futures_flows = [
        {"investor": "개인", "net_value": 300, "net_volume": None},
        {"investor": "외국인", "net_value": -1200, "net_volume": None},
        {"investor": "기관계", "net_value": 900, "net_volume": None},
    ]

    def fake_fetch_futures_flow_blocking(target_date):
        return {"flows": fake_futures_flows, "date": target_date}

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", fake_fetch_futures_flow_blocking)

    # 모듈 전역 캐시가 이전 테스트(장 마감 케이스)의 값을 들고 있을 수 있으므로
    # 비워서 이 테스트가 실제로 위 fake 경로를 타도록 강제한다.
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0
    markets._flow_live_cache["data"] = None
    markets._flow_live_cache["ts"] = 0.0
    markets._futures_flow_live_cache["data"] = None
    markets._futures_flow_live_cache["ts"] = 0.0

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/sentiment")

    assert resp.status_code == 200
    body = resp.json()
    breadth = body["components"]["breadth"]
    flow = body["components"]["flow"]
    futures = body["components"]["futures"]

    # adv=900+700=1600, dec=100+200=300, flat=50+30=80 -> 라이브 소스, EOD(1000/700/300) 아님.
    assert breadth["source"] == "live"
    assert breadth["adv"] == 1600
    assert breadth["dec"] == 300
    assert breadth["flat"] == 80
    assert breadth["date"] == dt.datetime.now(markets.KST).date().isoformat()

    # flow(§5.43): 코스피+코스닥 investors 합산 -> individual=-1500, foreign=6500,
    # institution=-2300 -> (6500-2300)/(1500+6500+2300)*100 = 40.7766... -> 40.8
    assert flow["source"] == "live"
    assert flow["individual"] == -1500
    assert flow["foreign"] == 6500
    assert flow["institution"] == -2300
    assert flow["score"] == 40.8
    assert flow["date"] == dt.datetime.now(markets.KST).date().isoformat()

    # futures(§5.43 신규): individual=300, foreign=-1200, institution=900
    # -> (-1200+900)/(300+1200+900)*100 = -12.5
    assert futures["source"] == "live"
    assert futures["individual"] == 300
    assert futures["foreign"] == -1200
    assert futures["institution"] == 900
    assert futures["score"] == -12.5

    # etf는 라이브 소스가 없다(§5.43 설계 노트) — 여전히 seeded EOD(TEST_DATE) 그대로.
    assert body["components"]["etf"]["date"] == TEST_DATE.isoformat()
    assert body["components"]["etf"]["source"] == "eod"


async def test_sentiment_falls_back_to_flow_rank_when_flow_live_totally_fails(
    seeded_sentiment_inputs, monkeypatch
):
    """실제 장애 재현(2026-07-31, CI export_static.py 크래시) — 장중인데 키움
    라이브 호출도 실패하고(예: 8050 지정단말기 인증 실패, CI 러너처럼 키움에
    등록 안 된 IP) market_flow DB에 kospi/kosdaq 확정치도 없으면(예: CI 빌드
    DB가 아직 그 배치를 한 번도 못 돌린 경우), `_warm_flow_live`가
    `HTTPException(502)`를 던진다. 이 실패가 `_load_flow_component_live`를
    뚫고 `market_sentiment` 전체를 502로 죽이면 안 된다 — breadth/futures/etf
    세 요소만으로도 정상 응답해야 하고, flow는 옛 flow_rank 랭킹 근사치
    (seeded_sentiment_inputs) EOD 폴백으로 조용히 넘어가야 한다."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    live_kospi = {"adv": 900, "dec": 100, "flat": 50, "limit_up": 3, "limit_down": 0}
    live_kosdaq = {"adv": 700, "dec": 200, "flat": 30, "limit_up": 1, "limit_down": 0}

    def fake_fetch_breadth(market: str) -> dict:
        return live_kospi if market == "kospi" else live_kosdaq

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch_breadth)

    from fastapi import HTTPException  # noqa: PLC0415

    async def fake_warm_flow_live_raises(session):
        raise HTTPException(502, "market flow live fetch failed: {'kospi': '...8050...', 'kosdaq': '...8050...'}")

    # flow_rank.py가 `from .markets import _warm_flow_live`로 이름을 직접
    # 가져다 쓰므로(재바인딩), markets 모듈의 속성만 바꿔선 flow_rank.py 안의
    # 이름에는 영향이 없다 — flow_rank 자신의 네임스페이스를 patch해야 한다.
    monkeypatch.setattr(flow_rank, "_warm_flow_live", fake_warm_flow_live_raises)

    fake_futures_flows = [
        {"investor": "개인", "net_value": 300, "net_volume": None},
        {"investor": "외국인", "net_value": -1200, "net_volume": None},
        {"investor": "기관계", "net_value": 900, "net_volume": None},
    ]

    def fake_fetch_futures_flow_blocking(target_date):
        return {"flows": fake_futures_flows, "date": target_date}

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", fake_fetch_futures_flow_blocking)

    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0
    markets._flow_live_cache["data"] = None
    markets._flow_live_cache["ts"] = 0.0
    markets._futures_flow_live_cache["data"] = None
    markets._futures_flow_live_cache["ts"] = 0.0

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/sentiment")

    assert resp.status_code == 200
    body = resp.json()
    breadth = body["components"]["breadth"]
    flow = body["components"]["flow"]
    futures = body["components"]["futures"]

    assert breadth["source"] == "live"
    assert futures["source"] == "live"

    # flow만 live가 예외로 실패 -> None -> 옛 flow_rank 랭킹 근사치 EOD로 폴백.
    # buy_sum=1500, sell_sum=500 -> (1500-500)/2000*100 = 50.0 (market_closed 테스트와 동일 시드값)
    assert flow["source"] == "eod"
    assert flow["buy_sum"] == 1500
    assert flow["sell_sum"] == 500
    assert flow["score"] == 50.0
    assert "by_market" not in flow


# 참고: "세 요소 전부 데이터 없음 -> score None" 케이스는 이 파일에서 통합테스트로
# 검증하지 않는다 — 이 라우터는 오늘 날짜 기준 lookback으로 실데이터를 함께 조회하므로
# (dev DB에 실제로 오늘자 breadth/flow_rank/etf_stats가 이미 적재돼 있을 수 있음),
# "전부 없음"을 재현하려면 실데이터까지 지워야 해 위험하다. 그 동작 자체(None 처리·
# 재정규화)는 tests/test_sentiment.py의 순수 단위테스트가 이미 커버한다.
