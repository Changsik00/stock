"""Unit tests for GET /api/markets/positioning/pair-view and
GET /api/markets/nasdaq-futures/live (app.routers.markets) — PLAN.md §5.50-2/
5.50-3/5.50-5: 본주+레버리지2X+인버스2X 호가·ETF 괴리율 통합 뷰, 나스닥선물
인트라데이 참고 타일.

Same no-DB/no-network philosophy as test_markets_hynix_relative_strength_router.py
and test_markets_attention_router.py: 재사용 함수들(`markets._warm_index_tiles_live`,
`markets._warm_stock_intraday`, `markets.get_stock_series_from_db`,
`markets.KiwoomClient`, `markets.naver_etf.fetch_etf_list`,
`us_indices.fetch_nasdaq_futures_intraday`)를 monkeypatch해서 조합
로직만 검증한다 — 실제 키움/네이버/yfinance 호출 없음.

**2026-08-07(PLAN.md §5.58)**: `_fetch_and_cache_nasdaq_futures_live`가 이제
`us_indices`를 함수 안에서 지연 import한다(모듈 레벨 import가 앱 기동/`--reload`
때마다 yfinance를 실제로 로드해 좀비 서브프로세스를 남기는 문제를 고쳤다) —
그래서 `markets.us_indices`가 아니라 `app.clients.us_indices` 모듈 객체를 직접
patch한다(같은 싱글턴 모듈 객체라 지연 import든 상단 import든 patch 효과는
동일하다).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients import us_indices
from app.db import get_session
from app.main import app
from app.routers import markets


async def _unused_session():
    yield object()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_pair_view_caches():
    """_etf_nav_cache/_nasdaq_futures_cache는 모듈 전역 상태라 테스트 간에 공유되면
    이전 테스트의 캐시 히트로 monkeypatch한 fake가 호출되지 않을 수 있다 —
    매 테스트 전후로 초기화한다(test_markets_attention_router.py의
    _reset_attention_cache와 동일한 관례)."""
    markets._etf_nav_cache["data"] = None
    markets._etf_nav_cache["ts"] = 0.0
    markets._nasdaq_futures_cache["data"] = None
    markets._nasdaq_futures_cache["ts"] = 0.0
    yield
    markets._etf_nav_cache["data"] = None
    markets._etf_nav_cache["ts"] = 0.0
    markets._nasdaq_futures_cache["data"] = None
    markets._nasdaq_futures_cache["ts"] = 0.0


def _fake_index_tiles(market_closed=False):
    async def fake_warm_index_tiles_live(session):
        return {
            "kospi": {"close": 3050.0, "change_rate": 1.0},
            "kosdaq": None,
            "futures": None,
            "market_closed": market_closed,
            "risk": None,
            "cached_at": "2026-08-03T00:00:00+00:00",
        }

    return fake_warm_index_tiles_live


def _make_fake_kiwoom_client(quote_by_code=None, raise_for_codes=None):
    """markets.KiwoomClient 대체용 fake — `async with KiwoomClient() as client:`로
    쓰이므로 클래스 자체가 async context manager여야 한다(test_markets_attention_router.py
    의 _make_fake_kiwoom_client와 동일한 관례). code별로 다른 raw dict를 반환하거나
    지정된 코드에서는 예외를 던진다."""
    quote_by_code = quote_by_code or {}
    raise_for_codes = raise_for_codes or set()

    class _FakeClient:
        calls: list[str] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def stock_quote(self, code):
            _FakeClient.calls.append(code)
            if code in raise_for_codes:
                raise RuntimeError(f"kiwoom boom ({code})")
            return quote_by_code.get(code, {})

    return _FakeClient


def _fake_intraday(close_by_code):
    async def fake_warm_stock_intraday(code, interval):
        assert interval == 1
        if code not in close_by_code:
            raise RuntimeError(f"intraday boom ({code})")
        return {"code": code, "interval": interval, "date": "20260803", "bars": [{"close": close_by_code[code]}]}

    return fake_warm_stock_intraday


def _fake_prev_close(prev_close_by_code):
    async def fake_get_stock_series_from_db(session, code, days):
        assert days == 5
        if code not in prev_close_by_code:
            return []
        return [{"date": "20260731", "close": prev_close_by_code[code]}]

    return fake_get_stock_series_from_db


# -- GET /api/markets/positioning/pair-view -----------------------------------------


async def test_pair_view_invalid_set_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning/pair-view", params={"set": "nvidia"})
    assert resp.status_code == 400


async def test_pair_view_market_closed_returns_all_null_and_skips_calls(monkeypatch):
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(market_closed=True))

    async def _raise_intraday(code, interval):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("_warm_stock_intraday should not be called when market is closed")

    monkeypatch.setattr(markets, "_warm_stock_intraday", _raise_intraday)

    def _raise_etf_list():  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_etf.fetch_etf_list should not be called when market is closed")

    monkeypatch.setattr(markets.naver_etf, "fetch_etf_list", _raise_etf_list)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning/pair-view", params={"set": "hynix"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["stock"] == {"code": "000660", "name": "SK하이닉스", "change_rate": None, "quote": None}
    assert body["leverage"]["now_value"] is None
    assert body["leverage"]["nav"] is None
    assert body["leverage"]["deviation_pct"] is None
    assert body["leverage"]["quote"] is None
    assert body["inverse"]["deviation_pct"] is None


async def test_pair_view_hynix_normal_case_combines_quote_change_rate_and_nav(monkeypatch):
    """정상 케이스 — 세 종목(본주/레버리지/인버스) 모두 호가+등락률/괴리율이
    채워져야 한다. 하이닉스 등락률: (205000-200000)/200000*100 = 2.5%. 레버리지
    괴리율: (9570-9616)/9616*100 ≈ -0.478%(PLAN.md §5.50-1 실측치 그대로 사용)."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(market_closed=False))
    monkeypatch.setattr(
        markets,
        "_warm_stock_intraday",
        _fake_intraday({"000660": 205000, "0193T0": 9570, "0197X0": 9305}),
    )
    monkeypatch.setattr(
        markets,
        "get_stock_series_from_db",
        _fake_prev_close({"000660": 200000.0, "0193T0": 9600.0, "0197X0": 9300.0}),
    )

    fake_client_cls = _make_fake_kiwoom_client(
        quote_by_code={
            "000660": {"sel_fpr_bid": "141000", "sel_fpr_req": "100", "buy_fpr_bid": "140900", "buy_fpr_req": "200"},
            "0193T0": {"sel_fpr_bid": "9575", "sel_fpr_req": "10", "buy_fpr_bid": "9570", "buy_fpr_req": "20"},
            "0197X0": {"sel_fpr_bid": "9310", "sel_fpr_req": "5", "buy_fpr_bid": "9305", "buy_fpr_req": "6"},
        }
    )
    monkeypatch.setattr(markets, "KiwoomClient", fake_client_cls)

    def fake_fetch_etf_list():
        return [
            {"code": "0193T0", "name": "KODEX SK하이닉스레버리지", "nav": 9616.0, "now_value": 9570.0},
            {"code": "0197X0", "name": "SOL SK하이닉스선물인버스2X", "nav": 9351.0, "now_value": 9305.0},
            {"code": "0193W0", "name": "KODEX 삼성전자레버리지", "nav": 10900.0, "now_value": 10840.0},
            {"code": "some-other-etf", "name": "무관한 ETF", "nav": 1.0, "now_value": 1.0},
        ]

    monkeypatch.setattr(markets.naver_etf, "fetch_etf_list", fake_fetch_etf_list)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning/pair-view", params={"set": "hynix"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["set"] == "hynix"
    assert body["market_closed"] is False

    assert body["stock"]["code"] == "000660"
    assert body["stock"]["change_rate"] == 2.5
    assert body["stock"]["quote"]["asks"][0] == {"level": 1, "price": 141000.0, "qty": 100.0}
    assert body["stock"]["quote"]["bids"][0] == {"level": 1, "price": 140900.0, "qty": 200.0}

    assert body["leverage"]["code"] == "0193T0"
    assert body["leverage"]["now_value"] == 9570.0
    assert body["leverage"]["nav"] == 9616.0
    assert body["leverage"]["deviation_pct"] == round((9570.0 - 9616.0) / 9616.0 * 100, 4)
    assert body["leverage"]["quote"]["asks"][0]["price"] == 9575.0

    assert body["inverse"]["code"] == "0197X0"
    assert body["inverse"]["deviation_pct"] == round((9305.0 - 9351.0) / 9351.0 * 100, 4)


async def test_pair_view_quote_failure_for_one_code_does_not_break_others(monkeypatch):
    """호가 조회가 한 종목에서만 실패해도(키움 502 등) 그 종목의 quote만 None이고
    나머지 필드(등락률/NAV/다른 종목 호가)는 그대로 채워져야 한다."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(market_closed=False))
    monkeypatch.setattr(markets, "_warm_stock_intraday", _fake_intraday({"000660": 205000}))
    monkeypatch.setattr(markets, "get_stock_series_from_db", _fake_prev_close({"000660": 200000.0}))

    fake_client_cls = _make_fake_kiwoom_client(raise_for_codes={"000660"})
    monkeypatch.setattr(markets, "KiwoomClient", fake_client_cls)

    def fake_fetch_etf_list():
        return []

    monkeypatch.setattr(markets.naver_etf, "fetch_etf_list", fake_fetch_etf_list)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning/pair-view", params={"set": "hynix"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["stock"]["quote"] is None
    assert body["stock"]["change_rate"] == 2.5  # 호가 실패와 무관하게 등락률은 살아있음.
    assert body["leverage"]["now_value"] is None  # naver_etf가 빈 리스트를 줘도 500이 아님.
    assert body["leverage"]["deviation_pct"] is None


async def test_pair_view_etf_nav_failure_does_not_break_quote_or_change_rate(monkeypatch):
    """naver_etf.fetch_etf_list()가 예외를 던져도(비공식 API) 이 엔드포인트는
    200을 유지하고, nav/now_value/deviation_pct만 None이어야 한다."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(market_closed=False))
    monkeypatch.setattr(markets, "_warm_stock_intraday", _fake_intraday({"000660": 205000}))
    monkeypatch.setattr(markets, "get_stock_series_from_db", _fake_prev_close({"000660": 200000.0}))

    fake_client_cls = _make_fake_kiwoom_client(
        quote_by_code={"000660": {"sel_fpr_bid": "141000", "sel_fpr_req": "1"}}
    )
    monkeypatch.setattr(markets, "KiwoomClient", fake_client_cls)

    def _raise_etf_list():
        raise RuntimeError("naver boom")

    monkeypatch.setattr(markets.naver_etf, "fetch_etf_list", _raise_etf_list)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning/pair-view", params={"set": "hynix"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["stock"]["change_rate"] == 2.5
    assert body["stock"]["quote"]["asks"][0]["price"] == 141000.0
    assert body["leverage"]["nav"] is None
    assert body["leverage"]["deviation_pct"] is None


async def test_pair_view_etf_nav_cache_shared_across_both_sets(monkeypatch):
    """`_warm_etf_nav`는 `set` 파라미터와 무관하게 4개 코드 전부를 한 번에
    캐시한다(PLAN.md §5.50-3 지시) — samsung/hynix를 연달아 조회해도
    naver_etf.fetch_etf_list()는 1번만 호출돼야 한다."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(market_closed=False))
    monkeypatch.setattr(
        markets,
        "_warm_stock_intraday",
        _fake_intraday({"005930": 71000, "000660": 205000}),
    )
    monkeypatch.setattr(
        markets,
        "get_stock_series_from_db",
        _fake_prev_close({"005930": 70000.0, "000660": 200000.0}),
    )
    monkeypatch.setattr(markets, "KiwoomClient", _make_fake_kiwoom_client())

    call_count = {"n": 0}

    def fake_fetch_etf_list():
        call_count["n"] += 1
        return [
            {"code": "0193W0", "name": "KODEX 삼성전자레버리지", "nav": 10900.0, "now_value": 10840.0},
            {"code": "0193L0", "name": "PLUS 삼성전자선물인버스2X", "nav": 12246.0, "now_value": 12255.0},
            {"code": "0193T0", "name": "KODEX SK하이닉스레버리지", "nav": 9616.0, "now_value": 9570.0},
            {"code": "0197X0", "name": "SOL SK하이닉스선물인버스2X", "nav": 9351.0, "now_value": 9305.0},
        ]

    monkeypatch.setattr(markets.naver_etf, "fetch_etf_list", fake_fetch_etf_list)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp1 = await client.get("/api/markets/positioning/pair-view", params={"set": "samsung"})
        resp2 = await client.get("/api/markets/positioning/pair-view", params={"set": "hynix"})

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert call_count["n"] == 1
    assert resp1.json()["leverage"]["nav"] == 10900.0
    assert resp2.json()["leverage"]["nav"] == 9616.0


# -- GET /api/markets/nasdaq-futures/live -------------------------------------------
#
# **2026-08-06부터(PLAN.md §5.56) 이 라우트는 절대 yfinance를 직접 호출하지
# 않는다** — 오직 07:50 KST 아침 크론(`_fetch_and_cache_nasdaq_futures_live`,
# `collectors/live_refresh.py::_run_nasdaq_futures_morning_job`)만 실제로
# 조회하고, 라우트 핸들러(`_warm_nasdaq_futures_live`)는 그 크론이 채워둔
# 모듈 전역 캐시(`markets._nasdaq_futures_cache`)를 읽기만 한다. 그래서 아래
# 라우트 테스트는 `us_indices.fetch_nasdaq_futures_intraday`를 몽키패치하는
# 대신 캐시를 직접 채우거나 비워 둔 채로 호출한다 — "요청이 오면 조회한다"가
# 아니라 "캐시에 있으면 그대로 주고, 없으면 빈 payload"라는 새 계약을
# 검증한다. 실제 yfinance 호출 로직 자체(정상/실패)는 아래
# `_fetch_and_cache_nasdaq_futures_live` 전용 테스트가 담당한다.


async def test_nasdaq_futures_live_returns_cached_payload_without_fetching(monkeypatch):
    def _raising_fetch(bars=50):  # pragma: no cover - 호출되면 안 됨
        raise AssertionError("온디맨드 경로가 yfinance를 직접 호출했다 — PLAN.md §5.56 위반")

    monkeypatch.setattr(us_indices, "fetch_nasdaq_futures_intraday", _raising_fetch)
    markets._nasdaq_futures_cache["data"] = {
        "symbol": "NQ=F",
        "bars": [{"time": "2026-08-06T07:50:00+09:00", "close": 20050.0}],
        "latest_change_pct": 0.25,
        "cached_at": "2026-08-06T07:50:00+00:00",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/nasdaq-futures/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_change_pct"] == 0.25
    assert len(body["bars"]) == 1


async def test_nasdaq_futures_live_empty_payload_when_cache_not_warmed_yet(monkeypatch):
    def _raising_fetch(bars=50):  # pragma: no cover - 호출되면 안 됨
        raise AssertionError("온디맨드 경로가 yfinance를 직접 호출했다 — PLAN.md §5.56 위반")

    monkeypatch.setattr(us_indices, "fetch_nasdaq_futures_intraday", _raising_fetch)
    assert markets._nasdaq_futures_cache["data"] is None  # autouse fixture가 매 테스트 시작 전 리셋

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/nasdaq-futures/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"symbol": "NQ=F", "bars": [], "latest_change_pct": None, "cached_at": None}


# -- _fetch_and_cache_nasdaq_futures_live (07:50 KST 아침 크론 전용, 실제 yfinance 호출) --


async def test_fetch_and_cache_nasdaq_futures_live_computes_latest_change_pct(monkeypatch):
    def fake_fetch_nasdaq_futures_intraday(bars=50):
        assert bars == 50
        return [
            {"time": "2026-08-03T09:00:00+09:00", "close": 20000.0},
            {"time": "2026-08-03T09:05:00+09:00", "close": 20100.0},
        ]

    monkeypatch.setattr(us_indices, "fetch_nasdaq_futures_intraday", fake_fetch_nasdaq_futures_intraday)

    payload = await markets._fetch_and_cache_nasdaq_futures_live()

    assert payload["symbol"] == "NQ=F"
    assert len(payload["bars"]) == 2
    assert payload["latest_change_pct"] == round((20100.0 - 20000.0) / 20000.0 * 100, 4)
    assert markets._nasdaq_futures_cache["data"] == payload  # 캐시에 실제로 반영됐는지


async def test_fetch_and_cache_nasdaq_futures_live_propagates_failure(monkeypatch):
    def _raise(bars=50):
        raise RuntimeError("yfinance boom")

    monkeypatch.setattr(us_indices, "fetch_nasdaq_futures_intraday", _raise)

    with pytest.raises(RuntimeError, match="yfinance boom"):
        await markets._fetch_and_cache_nasdaq_futures_live()

    assert markets._nasdaq_futures_cache["data"] is None  # 실패 시 캐시는 그대로(부분 갱신 없음)
