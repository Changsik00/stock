"""Unit tests for GET /api/markets/nasdaq-futures/live (app.routers.markets) —
PLAN.md §5.50-1/5.50-5: 나스닥선물 인트라데이 참고 타일.

**2026-08-06부터(PLAN.md §5.56) 이 라우트는 절대 yfinance를 직접 호출하지
않는다** — 오직 07:50 KST 아침 크론(`_fetch_and_cache_nasdaq_futures_live`,
`collectors/live_refresh.py::_run_nasdaq_futures_morning_job`)만 실제로
조회하고, 라우트 핸들러(`_warm_nasdaq_futures_live`)는 그 크론이 채워둔
모듈 전역 캐시(`markets._nasdaq_futures_cache`)를 읽기만 한다. 그래서 아래
라우트 테스트는 `us_indices.fetch_nasdaq_futures_intraday`를 몽키패치하는
대신 캐시를 직접 채우거나 비워 둔 채로 호출한다 — "요청이 오면 조회한다"가
아니라 "캐시에 있으면 그대로 주고, 없으면 빈 payload"라는 새 계약을
검증한다. 실제 yfinance 호출 로직 자체(정상/실패)는 아래
`_fetch_and_cache_nasdaq_futures_live` 전용 테스트가 담당한다.

**2026-08-07(PLAN.md §5.58)**: `_fetch_and_cache_nasdaq_futures_live`가 이제
`us_indices`를 함수 안에서 지연 import한다(모듈 레벨 import가 앱 기동/`--reload`
때마다 yfinance를 실제로 로드해 좀비 서브프로세스를 남기는 문제를 고쳤다) —
그래서 `markets.us_indices`가 아니라 `app.clients.us_indices` 모듈 객체를 직접
patch한다(같은 싱글턴 모듈 객체라 지연 import든 상단 import든 patch 효과는
동일하다).

**2026-08-18**: 같은 파일에서 함께 다루던 GET /api/markets/positioning/pair-view
테스트는 그 엔드포인트 자체(§5.50-2/-3, 대시보드 PairViewModal)가 제거되면서
삭제했다(PLAN.md §5.69) — 이 파일은 이제 나스닥선물만 다룬다.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients import us_indices
from app.main import app
from app.routers import markets


@pytest.fixture(autouse=True)
def _reset_nasdaq_futures_cache():
    """_nasdaq_futures_cache는 모듈 전역 상태라 테스트 간에 공유되면 이전
    테스트의 캐시 히트로 monkeypatch한 fake가 호출되지 않을 수 있다 —
    매 테스트 전후로 초기화한다(test_markets_attention_router.py의
    _reset_attention_cache와 동일한 관례)."""
    markets._nasdaq_futures_cache["data"] = None
    markets._nasdaq_futures_cache["ts"] = 0.0
    yield
    markets._nasdaq_futures_cache["data"] = None
    markets._nasdaq_futures_cache["ts"] = 0.0


# -- GET /api/markets/nasdaq-futures/live -------------------------------------------


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
