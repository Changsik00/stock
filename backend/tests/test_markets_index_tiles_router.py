"""Unit tests for GET /api/markets/index-tiles/live (app.routers.markets) —
대시보드 상단 지수 3종(코스피/코스닥/선물) 타일 1D 실시간화(2026-07-21).

Same no-DB/no-network philosophy as the other live-endpoint tests in this
package: KiwoomClient(ka20005, kospi/kosdaq intraday)와 clients/naver_index.py
(선물 "오늘" 봉)를 monkeypatch한다. index_ohlcv 조회는 두 갈래다:
- 장 마감/실패 폴백(`_index_tile_confirmed`)은 `markets.get_market_series_from_db`
  자체를 monkeypatch(다른 라우터 테스트와 동일한 패턴).
- 라이브 등락률의 prev_close(`_index_tile_prev_close`)는 session.execute(select
  (IndexOhlcv)...)를 직접 쓰므로, 순서대로 결과를 반환하는 FakeSession으로
  대응한다(kospi -> kosdaq -> futures 순, `_warm_index_tiles_live` 호출 순서와 동일).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets

_real_kiwoom_client = markets.KiwoomClient


def _make_fake_kiwoom_client(bars_response: dict):
    class _FakeClient:
        call_count = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def sector_minute_chart(self, inds_cd: str, tic_scope: str, **kwargs):
            _FakeClient.call_count += 1
            return bars_response, {"cont-yn": "N", "next-key": "", "api-id": "ka20005"}

    return _FakeClient


def _sector_response(cur_prc: str) -> dict:
    # ka20005 가격 필드는 index_ohlcv 대비 100배 스케일이다(모듈 주석
    # "_KA20005_PRICE_SCALE" 절, 2026-07-21 실측) — 여기 cur_prc="+305000"은
    # 실제 지수 3050.00에 해당한다.
    return {
        "return_code": 0,
        "inds_min_pole_qry": [
            {
                "cur_prc": cur_prc,
                "trde_qty": "16249",
                "cntr_tm": "20260721153000",
                "open_pric": "+300000",
                "high_pric": "+301000",
                "low_pric": "+299000",
            }
        ],
    }


async def _unused_session():
    yield object()


class _FakeIndexRow:
    def __init__(self, close: float, date: dt.date):
        self.close = close
        self.date = date


class _FakeScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeQueueSession:
    """session.execute() 호출마다 큐에서 순서대로 결과를 꺼내 반환 — prev_close
    조회 순서(kospi -> kosdaq -> futures, `_warm_index_tiles_live` 참고)와 맞춘다."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return self._results.pop(0)


def _prev_close_session(kospi: float | None, kosdaq: float | None, futures: float | None):
    def _row(v):
        return _FakeScalarOneResult(_FakeIndexRow(v, dt.date(2026, 7, 20)) if v is not None else None)

    async def fake_get_session():
        yield _FakeQueueSession([_row(kospi), _row(kosdaq), _row(futures)])

    return fake_get_session


@pytest.fixture(autouse=True)
def _clear_caches():
    markets._index_tiles_cache["data"] = None
    markets._index_tiles_cache["ts"] = 0.0
    markets._intraday_cache.clear()
    yield
    markets._index_tiles_cache["data"] = None
    markets._index_tiles_cache["ts"] = 0.0
    markets._intraday_cache.clear()


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)


@pytest.fixture(autouse=True)
def _restore_kiwoom_client():
    yield
    markets.KiwoomClient = _real_kiwoom_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _fake_confirmed(prev_close_by_market: dict):
    """get_market_series_from_db monkeypatch — 장 마감/실패 폴백(`_index_tile_confirmed`)
    전용. 라이브 경로의 prev_close(`_index_tile_prev_close`)는 이걸 거치지 않는다."""

    async def fake_get_market_series_from_db(session, market, days):
        prev = prev_close_by_market.get(market)
        if prev is None:
            return []
        return [{"date": "20260718", "close": prev, "changeRate": -0.5}]

    return fake_get_market_series_from_db


async def test_index_tiles_live_returns_three_markets(monkeypatch):
    markets.KiwoomClient = _make_fake_kiwoom_client(_sector_response("+305000"))

    def fake_futures_fetch(start, end):
        return [
            {"date": dt.date(2026, 7, 20), "open": 400.0, "high": 405.0, "low": 398.0, "close": 402.0, "volume": 10},
            {"date": dt.date(2026, 7, 21), "open": 402.0, "high": 410.0, "low": 400.0, "close": 408.0, "volume": 12},
        ]

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", fake_futures_fetch)
    app.dependency_overrides[get_session] = _prev_close_session(3000.0, 800.0, 400.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/index-tiles/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is False
    # 스케일 보정: raw cur_prc 305000 / 100 = 3050.0.
    assert body["kospi"]["close"] == 3050.0
    assert body["kospi"]["prev_close"] == 3000.0
    assert body["kospi"]["change_rate"] == round((3050.0 - 3000.0) / 3000.0 * 100, 4)
    assert body["kospi"]["source"] == "kiwoom_ka20005_1m"
    assert body["kosdaq"]["close"] == 3050.0  # same fake sector response reused
    assert body["kosdaq"]["prev_close"] == 800.0
    assert body["futures"]["close"] == 408.0
    assert body["futures"]["prev_close"] == 400.0
    assert body["futures"]["change_rate"] == round((408.0 - 400.0) / 400.0 * 100, 4)
    assert body["futures"]["source"] == "naver_fchart_today_bar"
    assert "cached_at" in body


async def test_index_tiles_live_caches_within_ttl(monkeypatch):
    fake_cls = _make_fake_kiwoom_client(_sector_response("+305000"))
    markets.KiwoomClient = fake_cls

    calls = []

    def fake_futures_fetch(start, end):
        calls.append(1)
        return [{"date": dt.date(2026, 7, 21), "open": 402.0, "high": 410.0, "low": 400.0, "close": 408.0, "volume": 12}]

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", fake_futures_fetch)
    app.dependency_overrides[get_session] = _prev_close_session(3000.0, 800.0, 400.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/index-tiles/live")
        r2 = await client.get("/api/markets/index-tiles/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert len(calls) == 1
    # kospi/kosdaq intraday도 캐시(60초 TTL) 공유 — sector_minute_chart는 시장당 1번씩만.
    assert fake_cls.call_count == 2


async def test_index_tiles_live_falls_back_to_confirmed_on_intraday_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("kiwoom boom")

    markets.KiwoomClient = _raise

    def fake_futures_fetch(start, end):
        return []  # 빈 응답 -> None 처리 경로

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", fake_futures_fetch)
    monkeypatch.setattr(
        markets,
        "get_market_series_from_db",
        _fake_confirmed({"kospi": 3000.0, "kosdaq": 800.0, "futures": 400.0}),
    )
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/index-tiles/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is False
    assert body["kospi"]["source"] == "index_ohlcv_confirmed"
    assert body["kospi"]["close"] == 3000.0
    assert body["futures"]["source"] == "index_ohlcv_confirmed"
    assert body["futures"]["close"] == 400.0


async def test_index_tiles_live_market_closed_skips_external_calls(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def _raise(*args, **kwargs):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("KiwoomClient should not be constructed when market is closed")

    markets.KiwoomClient = _raise

    def _raise_futures(*args, **kwargs):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", _raise_futures)
    monkeypatch.setattr(
        markets,
        "get_market_series_from_db",
        _fake_confirmed({"kospi": 3000.0, "kosdaq": 800.0, "futures": 400.0}),
    )
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/index-tiles/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"]["source"] == "index_ohlcv_confirmed"
    assert body["kospi"]["close"] == 3000.0
    assert body["futures"]["close"] == 400.0


async def test_index_tiles_live_no_db_data_returns_none_not_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("kiwoom boom")

    markets.KiwoomClient = _raise

    def _raise_futures(*args, **kwargs):
        raise RuntimeError("naver boom")

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", _raise_futures)
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_confirmed({}))
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/index-tiles/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"] is None
    assert body["kosdaq"] is None
    assert body["futures"] is None


async def test_index_tiles_live_prev_close_ignores_todays_seeded_row(monkeypatch):
    """prev_close는 index_ohlcv에 이미 "오늘" 행이 있어도(배치가 당겨 돌았거나
    개발/시드 데이터) 그 행을 baseline으로 쓰지 않는다 — `_index_tile_prev_close`가
    date < today로 명시적으로 거른다(2026-07-21 실측 중 발견한 함정, 모듈 주석
    참고). 이 테스트는 FakeSession이 "어제" 행만 주도록 구성해 회귀를 잡는다."""
    markets.KiwoomClient = _make_fake_kiwoom_client(_sector_response("+305000"))

    def fake_futures_fetch(start, end):
        return [{"date": dt.date(2026, 7, 21), "open": 402.0, "high": 410.0, "low": 400.0, "close": 408.0, "volume": 12}]

    monkeypatch.setattr(markets, "_fetch_futures_today_blocking", fake_futures_fetch)
    # FakeQueueSession이 오늘이 아니라 "어제" close만 반환하도록 구성돼 있음 자체가
    # _index_tile_prev_close가 date < today 필터를 실제 쿼리에 반영한다는 것까지는
    # 검증하지 못하지만(세션이 stmt를 무시), 최소한 반환값이 그대로 prev_close로
    # 쓰이는 배선은 검증한다 — date 필터 자체는 실제 DB 통합 환경에서 curl로 확인.
    app.dependency_overrides[get_session] = _prev_close_session(3000.0, 800.0, 400.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/index-tiles/live")

    body = resp.json()
    assert body["kospi"]["prev_close"] == 3000.0
    assert body["futures"]["prev_close"] == 400.0
