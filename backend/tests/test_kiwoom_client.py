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

from app.clients.kiwoom import (
    KiwoomAPIError,
    KiwoomAuthError,
    KiwoomClient,
    _parse_minute_price,
    parse_minute_chart_rows,
)

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


def _minute_chart_response(api_id: str, rows_key: str, request: httpx.Request) -> httpx.Response:
    assert request.headers["api-id"] == api_id
    return httpx.Response(
        200,
        json={"return_code": 0, "return_msg": "", rows_key: []},
        headers={"cont-yn": "Y", "next-key": "dummy", "api-id": api_id},
    )


async def test_stock_minute_chart_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/chart"
        captured["body"] = json.loads(request.content)
        return _minute_chart_response("ka10080", "stk_min_pole_chart_qry", request)

    client = make_client(handler)
    try:
        data, headers = await client.stock_minute_chart("005930", "5")
    finally:
        await client.aclose()

    assert captured["body"] == {"stk_cd": "005930", "tic_scope": "5", "upd_stkpc_tp": "1"}
    assert headers["api-id"] == "ka10080"
    assert data["stk_min_pole_chart_qry"] == []


async def test_sector_minute_chart_request_shape(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/chart"
        captured["body"] = json.loads(request.content)
        return _minute_chart_response("ka20005", "inds_min_pole_qry", request)

    client = make_client(handler)
    try:
        data, headers = await client.sector_minute_chart("001", "1")
    finally:
        await client.aclose()

    assert captured["body"] == {"inds_cd": "001", "tic_scope": "1"}
    assert headers["api-id"] == "ka20005"
    assert data["inds_min_pole_qry"] == []


# -- parse_minute_chart_rows / _parse_minute_price (순수 함수, 2026-07-21 실측 대조) ---


def test_parse_minute_price_strips_sign_prefixes_and_takes_abs():
    assert _parse_minute_price("-244000") == 244000
    assert _parse_minute_price("+654294") == 654294
    # 2026-07-21 실측에서 관측된 이중 부호(원인 미상 포매팅 특이사항)도 방어적으로 처리.
    assert _parse_minute_price("--30433") == 30433
    assert _parse_minute_price("0") == 0
    assert _parse_minute_price(None) is None
    assert _parse_minute_price("") is None
    assert _parse_minute_price("abc") is None


def test_parse_minute_chart_rows_keeps_only_latest_date_ascending():
    """실측(2026-07-21)처럼 한 응답에 여러 거래일이 섞여 있어도 최신 날짜만 남기고
    오름차순(과거->최신)으로 뒤집는다."""
    data = {
        "stk_min_pole_chart_qry": [
            # 최신이 먼저(내림차순) — 실제 API 순서 그대로.
            {
                "cur_prc": "-244000",
                "trde_qty": "10857",
                "cntr_tm": "20260720153500",
                "open_pric": "-244000",
                "high_pric": "-244000",
                "low_pric": "-244000",
            },
            {
                "cur_prc": "-245500",
                "trde_qty": "388453",
                "cntr_tm": "20260720151500",
                "open_pric": "-243000",
                "high_pric": "-246000",
                "low_pric": "-243000",
            },
            # 이전 거래일 — 결과에서 제외돼야 한다.
            {
                "cur_prc": "-300500",
                "trde_qty": "262598",
                "cntr_tm": "20260716111500",
                "open_pric": "-303250",
                "high_pric": "-304000",
                "low_pric": "-300000",
            },
        ]
    }

    bars = parse_minute_chart_rows(data, "ka10080")

    assert len(bars) == 2
    assert [b["date"] for b in bars] == ["20260720", "20260720"]
    # 오름차순으로 뒤집힘: 15:15 봉이 먼저, 15:35 봉이 나중.
    assert bars[0]["time"] == "1515"
    assert bars[1]["time"] == "1535"
    assert bars[0]["timestamp"] == "2026-07-20T15:15:00+09:00"
    # 부호 접두 파싱: 절대값.
    assert bars[0]["open"] == 243000
    assert bars[0]["high"] == 246000
    assert bars[0]["low"] == 243000
    assert bars[0]["close"] == 245500
    assert bars[0]["volume"] == 388453


def test_parse_minute_chart_rows_sector_uses_inds_key():
    data = {"inds_min_pole_qry": [{"cur_prc": "+651627", "trde_qty": "16249", "cntr_tm": "20260720153000"}]}
    bars = parse_minute_chart_rows(data, "ka20005")
    assert len(bars) == 1
    assert bars[0]["close"] == 651627


def test_parse_minute_chart_rows_handles_missing_array():
    assert parse_minute_chart_rows({"return_code": 0}, "ka10080") == []
    assert parse_minute_chart_rows({}, "ka20005") == []


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
