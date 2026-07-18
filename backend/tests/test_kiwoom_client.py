"""Unit tests for app.clients.kiwoom.KiwoomClient using httpx.MockTransport.

No real network/keys involved — these only verify the client's own logic:
token issuance -> cache -> reuse, and 429/return_code=5 retry-then-succeed.
Real-server verification (once KIWOOM_APP_KEY/SECRET are set) lives in
scripts/kiwoom_probe.py, per PLAN.md §6 Phase 2-1.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from app.clients.kiwoom import KiwoomAPIError, KiwoomAuthError, KiwoomClient

FAKE_BASE_URL = "https://mockapi.kiwoom.com"


def _expires_dt_str(hours_from_now: int = 24) -> str:
    kst = dt.timezone(dt.timedelta(hours=9))
    return (dt.datetime.now(kst) + dt.timedelta(hours=hours_from_now)).strftime("%Y%m%d%H%M%S")


def _token_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "return_code": 0,
            "return_msg": "정상적으로 처리되었습니다",
            "token_type": "bearer",
            "token": "fake-access-token",
            "expires_dt": _expires_dt_str(),
        },
    )


def _stkinfo_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == "ka10001"
    assert request.headers["authorization"] == "Bearer fake-access-token"
    return httpx.Response(
        200,
        json={"return_code": 0, "return_msg": "", "stk_cd": "005930", "stk_nm": "삼성전자"},
        headers={"cont-yn": "N", "next-key": "", "api-id": "ka10001"},
    )


@pytest.fixture
def make_client(tmp_path):
    def _make(handler):
        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport, base_url=FAKE_BASE_URL)
        return KiwoomClient(
            app_key="test-key",
            app_secret="test-secret",
            mock=True,
            token_cache_path=tmp_path / ".kiwoom_token.json",
            http_client=http_client,
        )

    return _make


async def test_token_issued_once_and_reused(make_client):
    calls = {"token": 0, "stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return _token_response(request)
        assert request.url.path == "/api/dostk/stkinfo"
        calls["stkinfo"] += 1
        return _stkinfo_response(request)

    client = make_client(handler)
    try:
        data1 = await client.stock_info("005930")
        data2 = await client.stock_info("005930")
    finally:
        await client.aclose()

    assert data1["stk_nm"] == "삼성전자"
    assert data2["stk_nm"] == "삼성전자"
    assert calls["stkinfo"] == 2
    # The whole point of the token cache: two TR calls, only one token issuance.
    assert calls["token"] == 1


async def test_no_keys_raises_before_any_request(tmp_path):
    client = KiwoomClient(
        app_key=None,
        app_secret=None,
        mock=True,
        token_cache_path=tmp_path / ".kiwoom_token.json",
    )
    try:
        with pytest.raises(KiwoomAuthError):
            await client.call_tr("ka10001", {"stk_cd": "005930"})
    finally:
        await client.aclose()


async def test_rate_limit_429_then_success(make_client, monkeypatch):
    """Simulates Kiwoom's documented 429 + return_code=5 rate-limit response,
    then a normal 200 on retry. The client should back off and succeed."""
    attempts = {"stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        attempts["stkinfo"] += 1
        if attempts["stkinfo"] == 1:
            return httpx.Response(
                429,
                json={"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"},
            )
        return _stkinfo_response(request)

    client = make_client(handler)
    # Skip the real sleep — we only care about the retry-then-succeed behavior.
    monkeypatch.setattr(client, "_backoff", lambda attempt: _noop())

    try:
        data = await client.stock_info("005930")
    finally:
        await client.aclose()

    assert data["stk_nm"] == "삼성전자"
    assert attempts["stkinfo"] == 2


async def _noop() -> None:
    return None


def _sect_investor_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == "ka10051"
    assert request.headers["authorization"] == "Bearer fake-access-token"
    return httpx.Response(
        200,
        json={
            "return_code": 0,
            "return_msg": "",
            "inds_netprps": [
                {
                    "inds_cd": "001_AL",
                    "inds_nm": "종합(KOSPI)",
                    "ind_netprps": "12345",
                    "frgnr_netprps": "-6789",
                }
            ],
        },
        headers={"cont-yn": "N", "next-key": "", "api-id": "ka10051"},
    )


async def test_sector_investor_net_buy_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/sect"
        captured["body"] = json.loads(request.content)
        return _sect_investor_response(request)

    client = make_client(handler)
    try:
        data, headers = await client.sector_investor_net_buy(
            mrkt_tp="0", base_dt=dt.date(2026, 7, 2)
        )
    finally:
        await client.aclose()

    assert captured["body"] == {
        "mrkt_tp": "0",
        "amt_qty_tp": "0",
        "base_dt": "20260702",
        "stex_tp": "3",
    }
    assert headers["api-id"] == "ka10051"
    row = data["inds_netprps"][0]
    assert row["inds_cd"] == "001_AL"
    assert row["ind_netprps"] == "12345"


async def test_sector_investor_net_buy_accepts_preformatted_date_string(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        captured["body"] = json.loads(request.content)
        return _sect_investor_response(request)

    client = make_client(handler)
    try:
        await client.sector_investor_net_buy(mrkt_tp="1", base_dt="20260702")
    finally:
        await client.aclose()

    assert captured["body"]["base_dt"] == "20260702"
    assert captured["body"]["mrkt_tp"] == "1"


def _intraday_investor_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == "ka10063"
    return httpx.Response(
        200,
        json={
            "return_code": 0,
            "return_msg": "",
            "opmr_invsr_trde": [
                {"stk_cd": "005930_AL", "stk_nm": "삼성전자", "netprps_amt": "-1557"}
            ],
        },
        headers={"cont-yn": "N", "next-key": "", "api-id": "ka10063"},
    )


async def test_intraday_investor_trading_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/mrkcond"
        captured["body"] = json.loads(request.content)
        return _intraday_investor_response(request)

    client = make_client(handler)
    try:
        data, headers = await client.intraday_investor_trading(mrkt_tp="001", invsr="6")
    finally:
        await client.aclose()

    assert captured["body"] == {
        "mrkt_tp": "001",
        "amt_qty_tp": "1",
        "invsr": "6",
        "frgn_all": "1",
        "smtm_netprps_tp": "1",
        "stex_tp": "3",
    }
    assert headers["api-id"] == "ka10063"
    # 실호출 확인 사항(2026-07-18 probe, kiwoom.py 모듈 docstring 참고): 응답은
    # 시장 합계가 아니라 종목별 배열이다.
    row = data["opmr_invsr_trde"][0]
    assert row["stk_cd"] == "005930_AL"


def _after_hours_investor_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == "ka10066"
    return httpx.Response(
        200,
        json={
            "return_code": 0,
            "return_msg": "",
            "opaf_invsr_trde": [
                {
                    "stk_cd": "000020_AL",
                    "stk_nm": "동화약품",
                    "ind_invsr": "1123",
                    "frgnr_invsr": "-642",
                    "orgn": "97",
                }
            ],
        },
        headers={"cont-yn": "N", "next-key": "", "api-id": "ka10066"},
    )


async def test_after_hours_investor_trading_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/mrkcond"
        captured["body"] = json.loads(request.content)
        return _after_hours_investor_response(request)

    client = make_client(handler)
    try:
        data, headers = await client.after_hours_investor_trading(mrkt_tp="001")
    finally:
        await client.aclose()

    assert captured["body"] == {
        "mrkt_tp": "001",
        "amt_qty_tp": "1",
        "trde_tp": "0",
        "stex_tp": "3",
    }
    assert headers["api-id"] == "ka10066"
    row = data["opaf_invsr_trde"][0]
    assert row["ind_invsr"] == "1123"


def _realtime_inquiry_rank_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == "ka00198"
    return httpx.Response(
        200,
        json={
            "return_code": 0,
            "return_msg": "정상적으로 처리되었습니다",
            "item_inq_rank": [
                {"stk_nm": "SK하이닉스", "bigd_rank": "1", "stk_cd": "000660", "base_comp_chgr": "-12.10"},
                {"stk_nm": "삼성전자", "bigd_rank": "2", "stk_cd": "005930", "base_comp_chgr": "-9.30"},
                {"stk_nm": "기아", "bigd_rank": "3", "stk_cd": "000270", "base_comp_chgr": "+1.72"},
            ],
        },
        headers={"cont-yn": "N", "next-key": "", "api-id": "ka00198"},
    )


async def test_realtime_inquiry_rank_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/stkinfo"
        captured["body"] = json.loads(request.content)
        return _realtime_inquiry_rank_response(request)

    client = make_client(handler)
    try:
        data, headers = await client.realtime_inquiry_rank()
    finally:
        await client.aclose()

    assert captured["body"] == {"qry_tp": "4"}
    assert headers["api-id"] == "ka00198"
    rows = data["item_inq_rank"]
    assert len(rows) == 3
    assert rows[0] == {"stk_nm": "SK하이닉스", "bigd_rank": "1", "stk_cd": "000660", "base_comp_chgr": "-12.10"}
    assert rows[2]["stk_cd"] == "000270"


async def test_return_code_error_raises_kiwoom_api_error(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        return httpx.Response(
            200,
            json={"return_code": 3, "return_msg": "존재하지 않는 종목코드입니다"},
        )

    client = make_client(handler)
    try:
        with pytest.raises(KiwoomAPIError) as exc_info:
            await client.stock_info("000000")
    finally:
        await client.aclose()

    assert exc_info.value.code == 3
