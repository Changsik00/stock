"""Unit tests for GET /api/markets/scalp-candidates (app.routers.scalp,
PLAN.md §5.2, extended for §5.20 flow scoring).

httpx.AsyncClient + ASGITransport against the real FastAPI app. The
value-rank/attention warm functions are monkeypatched (same "swap the
collaborator" style as test_markets_attention_router.py / test_value_rank_
live_router.py) since this router doesn't fetch those itself.

**2026-07-23 change (PLAN.md §5.20-2)**: ``_scored_candidates`` now also calls
``_stock_flow_lookup(session, codes)``, a real DB read against ``stock_flow``
— the dependency override can no longer yield ``None`` for the session (it did
before this phase, since nothing touched the DB). Tests now use a real session
from ``app.db.async_session_factory`` against the dev Postgres (same house
pattern as tests/test_stocks_router.py / test_scalp_tracker.py), and the
value-rank candidate codes were swapped from real KRX codes (000660/069500/
247540) to fake test codes (999801/999802/999803) that don't collide with
real market data, so seeding/clearing ``stock_flow`` rows for them is safe.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import async_session_factory, engine, get_session
from app.main import app
from app.market_hours import KST
from app.models import Stock, StockFlow
from app.routers import scalp

TEST_CODE_KOSPI = "999801"  # 069500 KODEX 200 자리를 대신하던 "SK하이닉스" 역할 -> 이제 개별주
TEST_CODE_ETF = "999802"
TEST_CODE_KOSDAQ = "999803"
TEST_CODES = [TEST_CODE_KOSPI, TEST_CODE_ETF, TEST_CODE_KOSDAQ]

VALUE_RANK_PAYLOAD = {
    "date": "2026-07-21",
    "market_closed": False,
    "cached_at": "2026-07-21T01:00:00+00:00",
    "rows": [
        {
            "rank": 1,
            "market": "kospi",
            "code": TEST_CODE_KOSPI,
            "name": "테스트반도체",
            "value": 500_000,
            "change_rate": 3.5,
            "is_etf": False,
            "turnover": 8.2,
        },
        {
            "rank": 2,
            "market": "kospi",
            "code": TEST_CODE_ETF,
            "name": "테스트KODEX",
            "value": 400_000,
            "change_rate": 0.5,
            "is_etf": True,  # ETF -> 후보에서 제외돼야 함
            "turnover": 12.0,
        },
        {
            "rank": 3,
            "market": "kosdaq",
            "code": TEST_CODE_KOSDAQ,
            "name": "테스트바이오",
            "value": 100_000,
            "change_rate": -6.1,
            "is_etf": False,
            "turnover": 15.4,
        },
    ],
}

ATTENTION_PAYLOAD = {
    "rows": [
        {
            "rank": 1,
            "code": TEST_CODE_KOSDAQ,
            "name": "테스트바이오",
            "change_rate": -6.1,
            "is_etf": False,
            "market": "kosdaq",
        }
    ],
    "qry_tp": "4",
    "queried_at": "2026-07-21T01:00:05+00:00",
    "market_closed": False,
}


async def _fake_warm_value_rank_live():
    return VALUE_RANK_PAYLOAD


async def _fake_warm_attention(session):
    return ATTENTION_PAYLOAD


@pytest.fixture(autouse=True)
def _patch_warm_functions(monkeypatch):
    monkeypatch.setattr(scalp, "_warm_value_rank_live", _fake_warm_value_rank_live)
    monkeypatch.setattr(scalp, "_warm_attention", _fake_warm_attention)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """app.db.engine이 이벤트 루프에 바인딩된 모듈 전역 싱글턴이라(pytest-asyncio가
    테스트마다 새 루프를 준다) 매 테스트 뒤 dispose — test_stocks_router.py와 동일한
    안전장치."""
    yield
    await engine.dispose()


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        await session.execute(delete(Stock).where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_stocks():
    """value-rank 후보 3개가 FK(``stock_flow.code -> stocks.code``)를 만족하도록
    최소한의 Stock 마스터 행을 심어 둔다 — 실제 flow 값 유무와 무관하게 이
    파일의 모든 테스트가 의존한다(seed 없이 StockFlow를 넣으면 FK 위반)."""
    await _clear_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=TEST_CODE_KOSPI, name="테스트반도체", market="KOSPI", is_etf=False))
        session.add(Stock(code=TEST_CODE_ETF, name="테스트KODEX", market="KOSPI", is_etf=True))
        session.add(Stock(code=TEST_CODE_KOSDAQ, name="테스트바이오", market="KOSDAQ", is_etf=False))
        await session.commit()
    yield
    await _clear_test_rows()


async def _get_session_override():
    async with async_session_factory() as session:
        yield session


def _apply_session_override():
    app.dependency_overrides[get_session] = _get_session_override


async def test_scalp_candidates_excludes_etf_and_marks_attention(seeded_stocks):
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["market_closed"] is False
    codes = [r["code"] for r in body["rows"]]
    assert TEST_CODE_ETF not in codes  # ETF 제외
    assert set(codes) == {TEST_CODE_KOSPI, TEST_CODE_KOSDAQ}

    by_code = {r["code"]: r for r in body["rows"]}
    assert by_code[TEST_CODE_KOSDAQ]["in_attention_top"] is True
    assert by_code[TEST_CODE_KOSPI]["in_attention_top"] is False
    assert by_code[TEST_CODE_KOSDAQ]["value_rank_position"] == 3
    assert by_code[TEST_CODE_KOSPI]["value_rank_position"] == 1
    assert by_code[TEST_CODE_KOSDAQ]["turnover"] == 15.4
    assert by_code[TEST_CODE_KOSPI]["change_rate"] == 3.5
    # 아직 stock_flow 스윕이 안 돈 상태(seeded_stocks는 StockFlow를 안 심음) -> null.
    assert by_code[TEST_CODE_KOSPI]["flow_net_value"] is None
    assert by_code[TEST_CODE_KOSDAQ]["flow_net_value"] is None
    # score 내림차순 정렬 확인
    scores = [r["score"] for r in body["rows"]]
    assert scores == sorted(scores, reverse=True)


async def test_scalp_candidates_limit_param(seeded_stocks):
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates?limit=1")

    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 1


async def test_scalp_candidates_empty_rows_when_no_value_rank_data(monkeypatch):
    async def _empty_value_rank():
        return {"date": None, "market_closed": False, "cached_at": None, "rows": []}

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _empty_value_rank)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    assert resp.json()["rows"] == []


async def test_scalp_candidates_market_closed_reflected_from_value_rank(seeded_stocks, monkeypatch):
    async def _closed_value_rank():
        return {**VALUE_RANK_PAYLOAD, "market_closed": True}

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _closed_value_rank)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    assert resp.json()["market_closed"] is True


# -- _stock_flow_lookup / flow_net_value (PLAN.md §5.20-2) -------------------


async def _seed_flow_rows(today: dt.date) -> None:
    async with async_session_factory() as session:
        session.add_all(
            [
                StockFlow(code=TEST_CODE_KOSPI, date=today, investor="외국인", net_value=3000),
                StockFlow(code=TEST_CODE_KOSPI, date=today, investor="기관계", net_value=2000),
                # 개인은 합계에서 제외돼야 한다 — 아무리 커도 결과에 영향 없어야 함.
                StockFlow(code=TEST_CODE_KOSPI, date=today, investor="개인", net_value=999_999),
                StockFlow(code=TEST_CODE_KOSDAQ, date=today, investor="외국인", net_value=-500),
            ]
        )
        await session.commit()


async def test_stock_flow_lookup_sums_foreign_and_institution_excludes_individual(seeded_stocks):
    today = dt.datetime.now(KST).date()
    await _seed_flow_rows(today)

    async with async_session_factory() as session:
        result = await scalp._stock_flow_lookup(session, [TEST_CODE_KOSPI, TEST_CODE_KOSDAQ])

    assert result[TEST_CODE_KOSPI] == 5000  # 3000(외국인) + 2000(기관계), 개인 999999 제외
    assert result[TEST_CODE_KOSDAQ] == -500


async def test_stock_flow_lookup_omits_codes_with_no_data(seeded_stocks):
    async with async_session_factory() as session:
        result = await scalp._stock_flow_lookup(session, [TEST_CODE_KOSPI, TEST_CODE_KOSDAQ])

    assert result == {}


async def test_stock_flow_lookup_empty_codes_returns_empty_dict_without_query():
    async with async_session_factory() as session:
        result = await scalp._stock_flow_lookup(session, [])

    assert result == {}


async def test_flow_net_value_appears_in_scalp_candidates_response(seeded_stocks):
    today = dt.datetime.now(KST).date()
    await _seed_flow_rows(today)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    by_code = {r["code"]: r for r in resp.json()["rows"]}
    assert by_code[TEST_CODE_KOSPI]["flow_net_value"] == 5000
    assert by_code[TEST_CODE_KOSDAQ]["flow_net_value"] == -500


# -- 가격제한폭 근접 배제 / at_risk 플래그 (PLAN.md §5.27) --------------------


async def test_near_price_limit_candidate_excluded_from_response(seeded_stocks, monkeypatch):
    """PLAN.md §5.27 실측 사례("일승" +29.86%) 그대로 재현 — 가격제한폭 근접
    종목은 rows에서 아예 사라져야 한다(스코어 조정이 아니라 구조적 배제)."""

    async def _near_limit_value_rank():
        payload = {**VALUE_RANK_PAYLOAD, "rows": [dict(r) for r in VALUE_RANK_PAYLOAD["rows"]]}
        payload["rows"][0] = {**payload["rows"][0], "change_rate": 29.86}
        return payload

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _near_limit_value_rank)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    codes = [r["code"] for r in resp.json()["rows"]]
    assert TEST_CODE_KOSPI not in codes


async def test_price_limit_threshold_boundary(seeded_stocks, monkeypatch):
    """경계값 확인: 정확히 PRICE_LIMIT_THRESHOLD_PCT(29.5)는 제외, 29.4는 포함
    (§5.27-1 — abs(change_rate) >= 29.5가 배제 조건)."""

    async def _boundary_value_rank():
        payload = {**VALUE_RANK_PAYLOAD, "rows": [dict(r) for r in VALUE_RANK_PAYLOAD["rows"]]}
        payload["rows"][0] = {**payload["rows"][0], "change_rate": 29.5}
        return payload

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _boundary_value_rank)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    codes = [r["code"] for r in resp.json()["rows"]]
    assert TEST_CODE_KOSPI not in codes  # 29.5는 배제

    async def _just_under_value_rank():
        payload = {**VALUE_RANK_PAYLOAD, "rows": [dict(r) for r in VALUE_RANK_PAYLOAD["rows"]]}
        payload["rows"][0] = {**payload["rows"][0], "change_rate": 29.4}
        return payload

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _just_under_value_rank)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    codes = [r["code"] for r in resp.json()["rows"]]
    assert TEST_CODE_KOSPI in codes  # 29.4는 포함


async def test_large_decline_candidate_flagged_at_risk_but_not_excluded(seeded_stocks, monkeypatch):
    """가격제한폭(-30%)까지는 아니지만 큰 폭 하락(-20%, <= -15.0 임계값)인
    종목은 배제되지 않고 정상적으로 스코어가 계산되며 at_risk: true만 붙는다
    (§5.27-2 — 배제와 플래그는 서로 다른 처리)."""

    async def _large_decline_value_rank():
        payload = {**VALUE_RANK_PAYLOAD, "rows": [dict(r) for r in VALUE_RANK_PAYLOAD["rows"]]}
        payload["rows"][0] = {**payload["rows"][0], "change_rate": -20.0}
        return payload

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _large_decline_value_rank)
    _apply_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    by_code = {r["code"]: r for r in resp.json()["rows"]}
    assert TEST_CODE_KOSPI in by_code  # 배제되지 않음
    assert by_code[TEST_CODE_KOSPI]["at_risk"] is True
    assert by_code[TEST_CODE_KOSPI]["change_rate"] == -20.0
    assert by_code[TEST_CODE_KOSPI]["score"] is not None
    # 다른 후보(-6.1%)는 임계값 아래라 at_risk가 아니어야 한다.
    assert by_code[TEST_CODE_KOSDAQ]["at_risk"] is False
