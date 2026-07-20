"""Tests for GET /api/stocks/search and GET /api/stocks/{code}/series
(PLAN.md §5.3/§6 Phase 3.7-2).

Search runs against the real dev Postgres (docker-compose `db` service, must be
running) — same rationale as tests/test_groups_router.py: this repo has no sqlite
test harness, and search's LIKE/ILIKE behavior is only meaningfully exercised
against a real SQL backend. A single throwaway stock row is seeded/torn down per
test with a code/name unlikely to collide with real `stocks` master data.

Series tests also hit the real DB (to exercise the actual cache upsert/read SQL),
but monkeypatch the two external calls (clients.naver_index.fetch_stock_series,
routers.stocks.KiwoomClient) — no real network. This lets us assert the "second
request hits DB cache, doesn't call the external API again" contract precisely
(PLAN.md §6 Phase 3.7-2 point 3).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.clients import naver_index
from app.clients.kiwoom import KiwoomAPIError
from app.db import async_session_factory, engine
from app.main import app
from app.models import Stock, StockFlow, StockOhlcv
from app.routers import stocks

TEST_CODE = "999999"
TEST_NAME = "테스트리서치전자"


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """pytest-asyncio gives each test its own event loop, but app.db.engine is a
    module-level singleton bound to whichever loop first used it — dispose after
    every test to force a fresh connection on the next one (same fixture as
    tests/test_groups_router.py)."""
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(StockFlow).where(StockFlow.code == TEST_CODE))
        await session.execute(delete(StockOhlcv).where(StockOhlcv.code == TEST_CODE))
        await session.execute(delete(Stock).where(Stock.code == TEST_CODE))
        await session.commit()


@pytest.fixture
async def seeded_stock():
    await _clear_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=TEST_CODE, name=TEST_NAME, market="KOSPI", is_etf=False))
        await session.commit()
    yield
    await _clear_test_rows()


def _fake_ka10059_response(d0: dt.date, d1: dt.date) -> dict:
    """2 trading days x 13 investor fields, shaped like the real ka10059 response
    observed via a one-off probe call on 2026-07-19 (005930) — descending date
    order like the real API (most recent first)."""

    def _row(date_str: str, ind: int, frgnr: int, samo: int) -> dict:
        orgn = frgnr + samo  # 단순화: 기관계 = 외국인만큼의 반대 + 사모(테스트용 임의 값)
        return {
            "dt": date_str,
            "ind_invsr": str(ind),
            "frgnr_invsr": str(frgnr),
            "orgn": str(orgn),
            "fnnc_invt": "0",
            "insrnc": "0",
            "invtrt": "0",
            "etc_fnnc": "0",
            "bank": "0",
            "penfnd_etc": "0",
            "samo_fund": str(samo),
            "natn": "0",
            "etc_corp": "0",
            "natfor": "0",
        }

    return {
        "return_code": 0,
        "return_msg": "",
        "stk_invsr_orgn": [
            _row(d0.strftime("%Y%m%d"), 1000, -500, -200),
            _row(d1.strftime("%Y%m%d"), 800, -400, -200),
        ],
    }


def _make_fake_kiwoom_client(calls: dict, response_or_exc):
    class _FakeKiwoomClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def stock_investor_daily(self, code: str):
            calls["n"] = calls.get("n", 0) + 1
            calls["code"] = code
            if isinstance(response_or_exc, Exception):
                raise response_or_exc
            return response_or_exc, {"cont-yn": "N", "next-key": "", "api-id": "ka10059"}

    return _FakeKiwoomClient


# -- 검색 -----------------------------------------------------------------------


async def test_search_matches_name_substring_case_insensitive(seeded_stock):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/search", params={"q": "리서치전자"})

    assert resp.status_code == 200
    body = resp.json()
    assert {"code": TEST_CODE, "name": TEST_NAME, "market": "KOSPI", "is_etf": False} in body


async def test_search_matches_code_prefix(seeded_stock):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/search", params={"q": "9999"})

    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()}
    assert TEST_CODE in codes


async def test_search_does_not_match_code_in_middle(seeded_stock):
    """코드는 전방일치만 — "999"로 시작하지 않는 검색어는 code로 안 걸려야 한다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/search", params={"q": "9999999999"})

    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()}
    assert TEST_CODE not in codes


async def test_search_respects_limit(seeded_stock):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/stocks/search", params={"q": "리서치전자", "limit": 1}
        )

    assert resp.status_code == 200
    assert len(resp.json()) <= 1


async def test_search_blank_query_returns_empty_list_without_hitting_db():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/search", params={"q": "   "})

    assert resp.status_code == 200
    assert resp.json() == []


async def test_search_no_match_returns_empty_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/stocks/search", params={"q": "존재할리없는이름절대"}
        )

    assert resp.status_code == 200
    assert resp.json() == []


# -- ka10059 파서 (순수 함수, DB/네트워크 없음) -----------------------------------


def test_parse_ka10059_rows_maps_all_13_investor_fields():
    data = _fake_ka10059_response(dt.date(2026, 7, 17), dt.date(2026, 7, 16))
    rows = stocks._parse_ka10059_rows(data)

    assert len(rows) == 26  # 2 dates x 13 investors
    assert {r["investor"] for r in rows} == set(stocks.KA10059_FIELD_TO_INVESTOR.values())
    assert all(r["net_volume"] is None for r in rows)

    by_date_investor = {(r["date"], r["investor"]): r["net_value"] for r in rows}
    assert by_date_investor[(dt.date(2026, 7, 17), "개인")] == 1000
    assert by_date_investor[(dt.date(2026, 7, 17), "외국인")] == -500
    assert by_date_investor[(dt.date(2026, 7, 17), "사모")] == -200
    assert by_date_investor[(dt.date(2026, 7, 17), "국가")] == 0
    assert by_date_investor[(dt.date(2026, 7, 16), "개인")] == 800


def test_parse_ka10059_rows_handles_missing_array():
    assert stocks._parse_ka10059_rows({"return_code": 0}) == []


def test_parse_signed_int_handles_signs_commas_and_junk():
    assert stocks._parse_signed_int("-1,234") == -1234
    assert stocks._parse_signed_int("+500") == 500
    assert stocks._parse_signed_int("0") == 0
    assert stocks._parse_signed_int(None) is None
    assert stocks._parse_signed_int("") is None
    assert stocks._parse_signed_int("abc") is None


# -- 종목 상세 (캔들+수급, 캐시 히트/미스) -----------------------------------------


async def test_series_first_request_fetches_and_caches_then_second_hits_cache(
    monkeypatch, seeded_stock
):
    target_end = stocks._latest_trading_day()
    prev_day = target_end - dt.timedelta(days=1)

    candle_calls = {"n": 0}

    def fake_fetch_stock_series(code, start, end):
        candle_calls["n"] += 1
        assert code == TEST_CODE
        return [
            {
                "date": prev_day,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": 1000,
            },
            {
                "date": target_end,
                "open": 105.0,
                "high": 115.0,
                "low": 100.0,
                "close": 110.0,
                "volume": 2000,
            },
        ]

    monkeypatch.setattr(stocks.naver_index, "fetch_stock_series", fake_fetch_stock_series)

    flow_calls: dict = {}
    fake_response = _fake_ka10059_response(target_end, prev_day)
    monkeypatch.setattr(
        stocks, "KiwoomClient", _make_fake_kiwoom_client(flow_calls, fake_response)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp1 = await client.get(f"/api/stocks/{TEST_CODE}/series", params={"days": 30})

    assert resp1.status_code == 200
    body1 = resp1.json()
    assert candle_calls["n"] == 1
    assert flow_calls["n"] == 1
    assert flow_calls["code"] == TEST_CODE

    assert body1["code"] == TEST_CODE
    assert body1["name"] == TEST_NAME
    assert body1["market"] == "KOSPI"
    assert body1["is_etf"] is False
    assert body1["meta"] == {}

    assert [p["date"] for p in body1["prices"]] == [
        prev_day.strftime("%Y%m%d"),
        target_end.strftime("%Y%m%d"),
    ]
    assert body1["prices"][-1]["close"] == 110.0
    assert body1["prices"][-1]["changeRate"] == pytest.approx(
        round((110.0 - 105.0) / 105.0 * 100, 4)
    )

    assert "개인" in body1["flows"]
    personal = {f["date"]: f for f in body1["flows"]["개인"]}
    assert personal[prev_day.strftime("%Y%m%d")]["net_value"] == 800
    assert personal[target_end.strftime("%Y%m%d")]["net_value"] == 1000
    # 누적순매수: 창(window) 왼쪽 끝부터 누적.
    assert personal[prev_day.strftime("%Y%m%d")]["cum_net_value"] == 800
    assert personal[target_end.strftime("%Y%m%d")]["cum_net_value"] == 1800

    # -- 두 번째 요청: DB 캐시가 최신이므로 외부 호출이 없어야 한다 --
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp2 = await client.get(f"/api/stocks/{TEST_CODE}/series", params={"days": 30})

    assert resp2.status_code == 200
    assert candle_calls["n"] == 1  # unchanged -> cache hit
    assert flow_calls["n"] == 1  # unchanged -> cache hit
    assert resp2.json()["prices"] == body1["prices"]
    assert resp2.json()["flows"] == body1["flows"]


async def test_series_candle_fetch_failure_returns_502(monkeypatch, seeded_stock):
    def fake_fetch_stock_series(code, start, end):
        raise naver_index.NaverIndexError("no rows parsed (probe)")

    monkeypatch.setattr(stocks.naver_index, "fetch_stock_series", fake_fetch_stock_series)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/stocks/{TEST_CODE}/series")

    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"]["source"] == "naver_fchart"
    assert "detail" in body["detail"]


# -- 분봉 (GET /{code}/intraday, PLAN.md §5.1) -----------------------------------


def _fake_intraday_kiwoom_client(calls: dict, response_or_exc):
    class _FakeKiwoomClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def stock_minute_chart(self, code: str, tic_scope: str, **kwargs):
            calls["n"] = calls.get("n", 0) + 1
            calls["code"] = code
            calls["tic_scope"] = tic_scope
            if isinstance(response_or_exc, Exception):
                raise response_or_exc
            return response_or_exc, {"cont-yn": "N", "next-key": "", "api-id": "ka10080"}

    return _FakeKiwoomClient


@pytest.fixture(autouse=True)
def _clear_intraday_cache():
    stocks._intraday_cache.clear()
    yield
    stocks._intraday_cache.clear()


async def test_intraday_valid_interval_returns_ascending_bars(monkeypatch):
    fake_response = {
        "return_code": 0,
        "stk_min_pole_chart_qry": [
            {"cur_prc": "-244000", "trde_qty": "100", "cntr_tm": "20260720090500",
             "open_pric": "-244000", "high_pric": "-244000", "low_pric": "-244000"},
            {"cur_prc": "-243000", "trde_qty": "200", "cntr_tm": "20260720090000",
             "open_pric": "-243000", "high_pric": "-243000", "low_pric": "-243000"},
        ],
    }
    calls: dict = {}
    monkeypatch.setattr(stocks, "KiwoomClient", _fake_intraday_kiwoom_client(calls, fake_response))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/stocks/{TEST_CODE}/intraday", params={"interval": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == TEST_CODE
    assert body["interval"] == 5
    assert body["date"] == "20260720"
    assert [b["time"] for b in body["bars"]] == ["0900", "0905"]  # 오름차순
    assert calls["tic_scope"] == "5"


async def test_intraday_invalid_interval_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/stocks/{TEST_CODE}/intraday", params={"interval": 7})

    assert resp.status_code == 400


async def test_intraday_kiwoom_failure_returns_502(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(
        stocks,
        "KiwoomClient",
        _fake_intraday_kiwoom_client(calls, KiwoomAPIError(3, "존재하지 않는 종목코드입니다")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/stocks/{TEST_CODE}/intraday", params={"interval": 1})

    assert resp.status_code == 502
    assert resp.json()["detail"]["source"] == "kiwoom_ka10080"


async def test_intraday_second_request_within_ttl_hits_cache(monkeypatch):
    fake_response = {
        "return_code": 0,
        "stk_min_pole_chart_qry": [
            {"cur_prc": "-244000", "trde_qty": "100", "cntr_tm": "20260720090000",
             "open_pric": "-244000", "high_pric": "-244000", "low_pric": "-244000"},
        ],
    }
    calls: dict = {}
    monkeypatch.setattr(stocks, "KiwoomClient", _fake_intraday_kiwoom_client(calls, fake_response))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get(f"/api/stocks/{TEST_CODE}/intraday", params={"interval": 60})
        r2 = await client.get(f"/api/stocks/{TEST_CODE}/intraday", params={"interval": 60})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert calls["n"] == 1  # 두 번째 요청은 캐시 히트
    assert r1.json() == r2.json()


async def test_series_flow_fetch_failure_is_partial_success_200(monkeypatch, seeded_stock):
    target_end = stocks._latest_trading_day()

    def fake_fetch_stock_series(code, start, end):
        return [
            {
                "date": target_end,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
            }
        ]

    monkeypatch.setattr(stocks.naver_index, "fetch_stock_series", fake_fetch_stock_series)
    monkeypatch.setattr(
        stocks,
        "KiwoomClient",
        _make_fake_kiwoom_client({}, KiwoomAPIError(3, "존재하지 않는 종목코드입니다")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/stocks/{TEST_CODE}/series")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["prices"]) == 1  # 캔들은 정상
    assert body["flows"] == {}
    assert "flows_error" in body["meta"]
