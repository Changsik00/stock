"""Unit tests for GET /api/markets/basis/live (app.routers.basis, PLAN.md §4.7
3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app (basis.router is
already wired into main.py). No real network for the price fetch (the blocking
naver_index call is monkeypatched via basis._fetch_index_series_blocking) — same
no-network philosophy as test_markets_breadth_router.py.

**2026-08-13 추가**: `_warm_basis_live`가 이제 optional `session`을 받아
`expiry.history`(과거 만기 사이클 D-day별 베이시스 대비 오늘 실측치의 편차,
`quant/expiry_pattern.compute_expiry_pattern` 기반)를 계산한다. 라우트
핸들러는 `Depends(get_session)`으로 실 DB 세션을 넘기므로 이 파일의 HTTP 테스트는
실제 dev Postgres에 연결된다 — 다만 `compute_expiry_pattern` 자체(무거운
`index_ohlcv` 전체 스캔, market 문자열로 격리 불가능 — 모듈 docstring 참고)는
history 값을 결정론적으로 검증해야 하는 테스트에서 `basis.compute_expiry_pattern`을
직접 monkeypatch해 synthetic 결과로 대체한다(`_fake_pattern` 헬퍼 참고) — 기존
happy-path 테스트들은 그대로 실 DB를 타되 history 값 자체를 assert하지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.main import app
from app.routers import basis

FUT_ROWS = [
    {"date": dt.date(2026, 7, 20), "open": 1040.0, "high": 1051.0, "low": 1033.0, "close": 1049.85, "volume": 9866}
]
SPOT_ROWS = [
    {"date": dt.date(2026, 7, 20), "open": 1051.0, "high": 1083.0, "low": 1027.0, "close": 1075.96, "volume": 21579}
]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """2026-08-13 추가 — `basis_live()`가 이제 `Depends(get_session)`로 실
    DB 세션을 연다(expiry.history 계산용). pytest-asyncio가 테스트 함수마다
    새 이벤트 루프를 쓰는데, 커넥션 풀이 이전 루프에 바인딩된 채로 남으면
    다음 테스트에서 "Task attached to a different loop" RuntimeError가 난다
    — tests/test_basis_router.py가 이미 쓰는 것과 동일한 fixture를 그대로
    가져온다.

    **setup에서도 dispose하는 이유(teardown만으로는 부족함을 실측으로 확인)**:
    전체 스위트를 알파벳 순으로 돌리면 이 파일 바로 앞(test_auto_trader_collector.py)이
    실거래 안전장치(킬스위치 enabled=True 감지 시 `pytest.fail`, auto_trader
    관련 파일이라 이 작업 범위에서 절대 건드리지 않는다)로 전체 실패하면서
    같은 프로세스 전역의 `app.db.engine` 풀에 이전 이벤트 루프에 바인딩된
    커넥션을 남긴다 — 이 파일의 teardown dispose만으로는 그 파일이 이미
    만들어 둔 dirty 상태를 못 걸러내(이 파일의 "첫" 테스트가 그 dirty 커넥션을
    먼저 집어든다), 이 파일의 **모든** 테스트가 시작 전에도 스스로
    dispose해 어느 순서로 실행되든 격리되게 한다."""
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_cache():
    basis._basis_live_cache["data"] = None
    basis._basis_live_cache["ts"] = 0.0
    yield
    basis._basis_live_cache["data"] = None
    basis._basis_live_cache["ts"] = 0.0


# 이 파일의 happy-path 테스트는 실제 wall-clock과 무관하게 "장중"을 가정한다 —
# 장 마감 케이스는 아래 별도 절이 다룬다(2026-07-20 신규 5~10분 티어 게이트 원칙).
@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: False)


async def test_basis_live_computes_from_today_bar(monkeypatch):
    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["futures_close"] == 1049.85
    assert body["kospi200_close"] == 1075.96
    assert body["basis"] == round(1049.85 - 1075.96, 2)
    assert body["backwardation"] is True
    assert body["market_closed"] is False
    assert "expiry" in body
    assert "cached_at" in body


async def test_basis_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market, start, end):
        calls.append(market)
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/basis/live")
        r2 = await client.get("/api/markets/basis/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == [basis.FUTURES_MARKET, basis.SPOT_MARKET]


async def test_basis_live_502_when_both_fail(monkeypatch):
    def fake_fetch(market, start, end):
        raise RuntimeError("boom")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 502


async def test_basis_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["basis"] is None


async def test_basis_live_includes_futures_today_ohlcv(monkeypatch):
    """PLAN.md §5.21-1 — futures_row 전체(open/high/low/close/volume)가
    futures_today로 노출되어야 한다(기존엔 close만 basis 계산에 쓰고 나머지는
    버렸다)."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    body = resp.json()
    assert body["futures_today"] == {
        "date": "2026-07-20",
        "open": 1040.0,
        "high": 1051.0,
        "low": 1033.0,
        "close": 1049.85,
        "volume": 9866,
    }


async def test_basis_live_market_closed_no_cache_sets_futures_today_none(monkeypatch):
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    body = resp.json()
    assert body["futures_today"] is None


async def test_basis_live_market_closed_reuse_carries_over_futures_today(monkeypatch):
    """장 마감 + 캐시 재사용 분기(`{**cached, "market_closed": True}`)가
    futures_today를 그대로 이어받는지 확인 — PLAN.md §5.21-1 "carry over" 요구사항."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/basis/live")
    assert r1.json()["futures_today"] is not None

    basis._basis_live_cache["ts"] = 0.0  # TTL 만료 시뮬레이션
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/markets/basis/live")

    body = r2.json()
    assert body["market_closed"] is True
    assert body["futures_today"] == r1.json()["futures_today"]


def _fake_pattern(points: list[dict], cycle_count: int = 20, reason: str | None = None):
    """``basis.compute_expiry_pattern``을 대체할 synthetic 결과 factory —
    2026-08-13 추가(expiry.history 테스트). 실제 dev Postgres의 index_ohlcv
    전체 히스토리를 읽는 무거운 쿼리(quant/expiry_pattern.py 모듈 docstring
    참고) 대신 결정론적인 값으로 deviation_pct 계산을 정확히 검증하기 위해
    라우트 모듈에 바인딩된 ``compute_expiry_pattern`` 자체를 monkeypatch한다
    (routers/basis.py가 ``from ..quant.expiry_pattern import
    compute_expiry_pattern``으로 임포트해 두어 ``basis.compute_expiry_pattern``
    이름으로 patch 가능 — quant/expiry_pattern.py 원본은 건드리지 않는다)."""

    async def fake(session):
        return {
            "cycle_count": cycle_count,
            "date_from": "2023-08-10" if points else None,
            "date_to": "2026-07-09" if points else None,
            "max_lookback_days": 14,
            "points": points,
            "reason": reason,
        }

    return fake


async def test_basis_live_history_present_with_matching_d_day_and_deviation(monkeypatch):
    """장중 라이브 + 과거 히스토리 표본 충분 → expiry.history에 mean/median/n이
    들어가고, deviation_pct가 today_basis_pct - mean_basis_pct와 정확히
    일치하는지 수치 검증."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    today = dt.date.today()
    d_day = basis.days_to_expiry(today)
    monkeypatch.setattr(
        basis,
        "compute_expiry_pattern",
        _fake_pattern(
            [
                {
                    "d_day": d_day,
                    "mean_basis": 1.0,
                    "median_basis": 0.9,
                    "mean_basis_pct": 0.05,
                    "median_basis_pct": 0.04,
                    "n": 18,
                }
            ]
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    body = resp.json()
    history = body["expiry"]["history"]
    assert history is not None
    assert history["cycle_count"] == 20
    assert history["mean_basis_pct"] == 0.05
    assert history["median_basis_pct"] == 0.04
    assert history["n"] == 18
    assert history["deviation_pct"] == round(body["basis_pct"] - 0.05, 4)


async def test_basis_live_history_none_when_cycles_insufficient(monkeypatch):
    """과거 사이클 표본 부족(compute_expiry_pattern의 points: []) → history: None."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)
    monkeypatch.setattr(
        basis,
        "compute_expiry_pattern",
        _fake_pattern([], cycle_count=2, reason="과거 만기 사이클 2회만 확보 — 최소 6회 필요"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.json()["expiry"]["history"] is None


async def test_basis_live_history_none_when_no_point_for_today_d_day(monkeypatch):
    """오늘 d_day에 해당하는 point가 없음(max_lookback_days보다 먼 D-day 등)
    → history: None."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    today = dt.date.today()
    d_day = basis.days_to_expiry(today)
    monkeypatch.setattr(
        basis,
        "compute_expiry_pattern",
        _fake_pattern(
            [
                {
                    "d_day": d_day - 1000,  # 오늘의 d_day와 절대 겹치지 않는 값
                    "mean_basis": 1.0,
                    "median_basis": 0.9,
                    "mean_basis_pct": 0.05,
                    "median_basis_pct": 0.04,
                    "n": 10,
                }
            ]
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.json()["expiry"]["history"] is None


async def test_warm_basis_live_session_none_history_none_no_crash(monkeypatch):
    """session=None으로 직접 호출(routers/markets.py::_append_futures_provisional_row
    스타일 호출) → history: None이지만 나머지 필드는 정상, 크래시 없음."""

    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    payload = await basis._warm_basis_live()

    assert payload["market_closed"] is False
    assert payload["basis"] == round(1049.85 - 1075.96, 2)
    assert payload["expiry"]["history"] is None


async def test_basis_live_market_closed_no_cache_history_field_present(monkeypatch):
    """장마감 + 캐시 없음(cold start) 분기에서도 session이 주어졌다면 history가
    (오늘 basis_pct가 없으므로 deviation_pct는 None인 채로) 계산되는지 확인."""
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)

    today = dt.date.today()
    d_day = basis.days_to_expiry(today)
    monkeypatch.setattr(
        basis,
        "compute_expiry_pattern",
        _fake_pattern(
            [
                {
                    "d_day": d_day,
                    "mean_basis": 1.0,
                    "median_basis": 0.9,
                    "mean_basis_pct": 0.05,
                    "median_basis_pct": 0.04,
                    "n": 12,
                }
            ],
            cycle_count=15,
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    history = body["expiry"]["history"]
    assert history is not None
    assert history["deviation_pct"] is None  # 오늘 basis_pct가 없으므로(장마감 캐시 없음)
    assert history["mean_basis_pct"] == 0.05
    assert history["n"] == 12


async def test_basis_live_market_closed_reuses_last_cache(monkeypatch):
    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/basis/live")
    assert r1.json()["market_closed"] is False

    basis._basis_live_cache["ts"] = 0.0  # TTL 만료 시뮬레이션

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/markets/basis/live")

    assert r2.status_code == 200
    body = r2.json()
    assert body["market_closed"] is True
    assert body["basis"] == r1.json()["basis"]
