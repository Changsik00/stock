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
    MAX_ORDER_NOTIONAL_KRW,
    KiwoomAPIError,
    KiwoomAuthError,
    KiwoomClient,
    _parse_minute_price,
    parse_minute_chart_rows,
    parse_quote_levels,
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

    assert captured["body"] == {"qry_tp": "1"}
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
    # ka20005는 가격이 실제 지수값의 100배로 온다(2026-07-21 실측 확정,
    # parse_minute_chart_rows 모듈 docstring "ka20005 가격 필드 100배 스케일
    # 버그" 절 참고) — 651627 -> 6516.27로 보정돼 나와야 한다.
    data = {"inds_min_pole_qry": [{"cur_prc": "+651627", "trde_qty": "16249", "cntr_tm": "20260720153000"}]}
    bars = parse_minute_chart_rows(data, "ka20005")
    assert len(bars) == 1
    assert bars[0]["close"] == 6516.27


def test_parse_minute_chart_rows_stock_no_scale_correction():
    # ka10080(개별 종목)은 100배 스케일이 없다 — 원화 정수 그대로 나와야 한다.
    data = {"stk_min_pole_chart_qry": [{"cur_prc": "+255000", "trde_qty": "1000", "cntr_tm": "20260720153000"}]}
    bars = parse_minute_chart_rows(data, "ka10080")
    assert len(bars) == 1
    assert bars[0]["close"] == 255000


def test_parse_minute_chart_rows_handles_missing_array():
    assert parse_minute_chart_rows({"return_code": 0}, "ka10080") == []
    assert parse_minute_chart_rows({}, "ka20005") == []


async def test_return_code_error_raises_kiwoom_api_error(make_client):
    """return_code==3이지만 8005/인증 실패가 아닌 무관한 오류(존재하지 않는
    종목코드)는 토큰 재발급을 시도하지 않고 바로 예외를 던져야 한다 —
    PLAN.md §5.46 "버그 1"에서 확인된 return_code==3의 범용성 근거."""
    calls = {"token": 0, "stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return _token_response(request)
        calls["stkinfo"] += 1
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
    # 인증과 무관한 return_code==3이므로 재시도/재발급 없이 1콜만 나가야 한다.
    assert calls["stkinfo"] == 1
    assert calls["token"] == 1


async def test_token_invalid_8005_forces_refresh_then_succeeds(make_client, monkeypatch):
    """PLAN.md §5.46 "버그 1" 재현: 실측 에러 텍스트
    `[3] 인증에 실패했습니다[8005:Token이 유효하지 않습니다]` 그대로 첫 시도에서
    받으면, 로컬 캐시를 무시하고 강제 재발급한 뒤 같은 요청을 재시도해서
    성공해야 한다."""
    calls = {"token": 0, "stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return _token_response(request)
        calls["stkinfo"] += 1
        if calls["stkinfo"] == 1:
            return httpx.Response(
                200,
                json={
                    "return_code": 3,
                    "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]",
                },
            )
        return _stkinfo_response(request)

    client = make_client(handler)
    # 토큰 재발급 경로에는 백오프가 없어야 하지만(즉시 재시도), 혹시라도 다른
    # 경로에서 sleep이 걸리는 회귀가 생기면 테스트가 느려지지 않도록 방어적으로 몽키패치.
    monkeypatch.setattr(client, "_backoff", lambda attempt: _noop())

    try:
        data = await client.stock_info("005930")
    finally:
        await client.aclose()

    assert data["stk_nm"] == "삼성전자"
    assert calls["stkinfo"] == 2
    # 최초 발급 1회 + 강제 재발급 1회 = 토큰 엔드포인트 2콜.
    assert calls["token"] == 2


async def test_token_invalid_8005_exhausts_retries_and_raises(make_client, monkeypatch):
    """재발급해도 계속 8005가 나는 경우(진짜 앱키/시크릿 문제 등) — 무한루프
    없이 기존 max_retries 상한에서 정확히 멈추고 KiwoomAPIError를 던져야 한다."""
    calls = {"token": 0, "stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return _token_response(request)
        calls["stkinfo"] += 1
        return httpx.Response(
            200,
            json={
                "return_code": 3,
                "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]",
            },
        )

    client = make_client(handler)
    monkeypatch.setattr(client, "_backoff", lambda attempt: _noop())

    try:
        with pytest.raises(KiwoomAPIError) as exc_info:
            await client.stock_info("005930")
    finally:
        await client.aclose()

    assert exc_info.value.code == 3
    # 무한루프가 아니라 max_retries+1번(최초 시도 + 재시도들)에서 정확히 멈춤.
    assert calls["stkinfo"] == client.max_retries + 1
    # 토큰 엔드포인트도 무한 재발급이 아니라 최초 발급 1회 + 마지막 시도를
    # 제외한 재시도 횟수(max_retries)만큼만 강제 재발급됨.
    assert calls["token"] == client.max_retries + 1


async def test_rate_limit_retry_does_not_force_token_refresh(make_client, monkeypatch):
    """회귀 방지: return_code==5(rate limit) 경로는 이번 변경과 무관하게 그대로
    "같은 토큰으로 백오프 후 재시도"여야 한다 — 8005 경로처럼 토큰을 강제
    재발급하면 안 된다."""
    calls = {"token": 0, "stkinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return _token_response(request)
        calls["stkinfo"] += 1
        if calls["stkinfo"] == 1:
            return httpx.Response(
                429,
                json={"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"},
            )
        return _stkinfo_response(request)

    client = make_client(handler)
    monkeypatch.setattr(client, "_backoff", lambda attempt: _noop())

    try:
        data = await client.stock_info("005930")
    finally:
        await client.aclose()

    assert data["stk_nm"] == "삼성전자"
    assert calls["stkinfo"] == 2
    # rate limit 재시도는 토큰을 재사용해야 하므로 발급은 최초 1회뿐이어야 한다.
    assert calls["token"] == 1


# -- 주문 (PLAN.md §5.48, 실호출 미확정 — 전부 mock, 실제 HTTP 호출 절대 없음) ------


def _order_response(request: httpx.Request, api_id: str) -> httpx.Response:
    assert request.headers["api-id"] == api_id
    return httpx.Response(
        200,
        json={"return_code": 0, "return_msg": "", "ord_no": "0000001"},
        headers={"cont-yn": "N", "next-key": "", "api-id": api_id},
    )


async def test_place_buy_order_sends_correct_body(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/ordr"
        captured["body"] = json.loads(request.content)
        return _order_response(request, "kt10000")

    client = make_client(handler)
    try:
        data = await client.place_buy_order("007980", 1, 1928)
    finally:
        await client.aclose()

    assert captured["body"] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "007980",
        "ord_qty": 1,
        "ord_uv": 1928,
        "trde_tp": "0",
    }
    assert data["ord_no"] == "0000001"


async def test_place_buy_order_over_cap_raises_without_any_http_call(make_client):
    """이 테스트가 이 작업 전체의 핵심 안전장치다: notional이 캡을 넘으면
    ValueError만 던지는 게 아니라, 실제로 HTTP 계층에 단 1건도 요청이 나가지
    않아야 한다(토큰 발급조차 포함) — 그래야 "버그로 인한 의도치 않은 실주문"을
    막는다는 목적을 충족한다."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise AssertionError("notional 캡 초과 시에는 어떤 HTTP 요청도 나가면 안 된다")

    client = make_client(handler)
    try:
        with pytest.raises(ValueError):
            await client.place_buy_order("007980", 10, 6000)  # 60,000 > 50,000
    finally:
        await client.aclose()

    assert calls["count"] == 0


async def test_place_sell_order_over_cap_raises_without_any_http_call(make_client):
    """매도도 동일한 캡을 적용하며, 마찬가지로 HTTP 호출이 전혀 없어야 한다."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise AssertionError("notional 캡 초과 시에는 어떤 HTTP 요청도 나가면 안 된다")

    client = make_client(handler)
    try:
        with pytest.raises(ValueError):
            await client.place_sell_order("007980", 100, 1000)  # 100,000 > 50,000
    finally:
        await client.aclose()

    assert calls["count"] == 0


async def test_place_buy_order_at_exact_cap_boundary_is_allowed(make_client):
    """정확히 5만원(경계값)은 거부되면 안 된다 — 캡이 `>`이지 `>=`가 아님을 확인."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        captured["body"] = json.loads(request.content)
        return _order_response(request, "kt10000")

    client = make_client(handler)
    try:
        quantity, price = 10, 5000
        assert quantity * price == MAX_ORDER_NOTIONAL_KRW
        data = await client.place_buy_order("007980", quantity, price)
    finally:
        await client.aclose()

    assert captured["body"]["ord_qty"] == 10
    assert data["ord_no"] == "0000001"


async def test_place_sell_order_sends_correct_body(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/ordr"
        captured["body"] = json.loads(request.content)
        return _order_response(request, "kt10001")

    client = make_client(handler)
    try:
        data = await client.place_sell_order("007980", 1, 1928)
    finally:
        await client.aclose()

    assert captured["body"] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "007980",
        "ord_qty": 1,
        "ord_uv": 1928,
        "trde_tp": "0",
    }
    assert data["ord_no"] == "0000001"


async def test_cancel_order_sends_correct_body(make_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/ordr"
        captured["body"] = json.loads(request.content)
        return _order_response(request, "kt10003")

    client = make_client(handler)
    try:
        data = await client.cancel_order("007980", "0000001", 1)
    finally:
        await client.aclose()

    assert captured["body"] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "007980",
        "orig_ord_no": "0000001",
        "ord_qty": 1,
    }
    assert data["ord_no"] == "0000001"


async def test_get_deposit_detail_request_shape(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/acnt"
        assert request.headers["api-id"] == "kt00001"
        assert json.loads(request.content) == {}
        return httpx.Response(
            200,
            json={"return_code": 0, "return_msg": "", "entr": "100000"},
            headers={"cont-yn": "N", "next-key": "", "api-id": "kt00001"},
        )

    client = make_client(handler)
    try:
        data = await client.get_deposit_detail()
    finally:
        await client.aclose()

    assert data["entr"] == "100000"


async def test_stock_quote_request_shape(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/mrkcond"
        assert request.headers["api-id"] == "ka10004"
        assert json.loads(request.content) == {"stk_cd": "005930"}
        return httpx.Response(
            200,
            json={
                "return_code": 0,
                "return_msg": "",
                "sel_fpr_bid": "+71100",
                "buy_fpr_bid": "+71000",
                "tot_sel_req": "1000",
                "tot_buy_req": "2000",
            },
            headers={"cont-yn": "N", "next-key": "", "api-id": "ka10004"},
        )

    client = make_client(handler)
    try:
        data = await client.stock_quote("005930")
    finally:
        await client.aclose()

    assert data["sel_fpr_bid"] == "+71100"
    assert data["buy_fpr_bid"] == "+71000"
    assert data["tot_sel_req"] == "1000"
    assert data["tot_buy_req"] == "2000"


async def test_get_unfilled_orders_request_shape(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        assert request.url.path == "/api/dostk/acnt"
        assert request.headers["api-id"] == "ka10075"
        assert json.loads(request.content) == {}
        return httpx.Response(
            200,
            json={"return_code": 0, "return_msg": "", "oso": []},
            headers={"cont-yn": "N", "next-key": "", "api-id": "ka10075"},
        )

    client = make_client(handler)
    try:
        data = await client.get_unfilled_orders()
    finally:
        await client.aclose()

    assert data["oso"] == []


# -- parse_quote_levels (ka10004 응답 파싱, PLAN.md §5.50-2) -------------------------


def _full_quote_response() -> dict:
    """매도/매수 각 10단계가 모두 채워진 가짜 ka10004 응답 — 부호 접두 없음."""
    data = {
        "return_code": 0,
        "return_msg": "",
        "sel_fpr_bid": "71100",
        "sel_fpr_req": "100",
        "buy_fpr_bid": "71000",
        "buy_fpr_req": "200",
        "tot_sel_req": "5000",
        "tot_buy_req": "6000",
        "bid_req_base_tm": "153000",
    }
    for level in range(2, 11):
        data[f"sel_{level}th_pre_bid"] = str(71100 + (level - 1) * 100)
        data[f"sel_{level}th_pre_req"] = str(100 * level)
        data[f"buy_{level}th_pre_bid"] = str(71000 - (level - 1) * 100)
        data[f"buy_{level}th_pre_req"] = str(200 * level)
    return data


def test_parse_quote_levels_normal_case_fills_all_ten_levels():
    result = parse_quote_levels(_full_quote_response())

    assert len(result["asks"]) == 10
    assert len(result["bids"]) == 10
    assert [row["level"] for row in result["asks"]] == list(range(1, 11))
    assert [row["level"] for row in result["bids"]] == list(range(1, 11))

    assert result["asks"][0] == {"level": 1, "price": 71100.0, "qty": 100.0}
    assert result["bids"][0] == {"level": 1, "price": 71000.0, "qty": 200.0}
    assert result["asks"][9] == {"level": 10, "price": 71100.0 + 9 * 100, "qty": 100.0 * 10}
    assert result["bids"][9] == {"level": 10, "price": 71000.0 - 9 * 100, "qty": 200.0 * 10}

    assert result["total_ask_qty"] == 5000.0
    assert result["total_bid_qty"] == 6000.0
    assert result["base_time"] == "153000"


def test_parse_quote_levels_sign_prefix_converted_to_absolute_value():
    """실호출 미검증 TR이라 다른 키움 TR들처럼 부호(+/-) 접두가 붙을 가능성을
    대비한다(PLAN.md §5.50-2 지시) — 붙어 있으면 방향 표시를 무시하고 절대값만
    남긴다."""
    data = {
        "sel_fpr_bid": "+71100",
        "sel_fpr_req": "-100",  # 잔량에 부호가 섞여 와도 절대값으로 처리.
        "buy_fpr_bid": "-71000",
        "buy_fpr_req": "+200",
        "tot_sel_req": "+5000",
        "tot_buy_req": "-6000",
    }
    result = parse_quote_levels(data)

    assert result["asks"][0] == {"level": 1, "price": 71100.0, "qty": 100.0}
    assert result["bids"][0] == {"level": 1, "price": 71000.0, "qty": 200.0}
    assert result["total_ask_qty"] == 5000.0
    assert result["total_bid_qty"] == 6000.0


def test_parse_quote_levels_missing_fields_default_to_zero_without_crashing():
    """필드 누락/빈 문자열/dict 자체가 텅 비어 있어도 크래시하지 않고 0으로
    채운다(§5 방어적 처리 관례)."""
    result = parse_quote_levels({})

    assert len(result["asks"]) == 10
    assert len(result["bids"]) == 10
    assert all(row["price"] == 0.0 and row["qty"] == 0.0 for row in result["asks"])
    assert all(row["price"] == 0.0 and row["qty"] == 0.0 for row in result["bids"])
    assert result["total_ask_qty"] == 0.0
    assert result["total_bid_qty"] == 0.0
    assert result["base_time"] is None

    # 빈 문자열도 0으로 처리 (일부 필드만 빈 문자열인 부분 누락 케이스).
    partial = {"sel_fpr_bid": "", "sel_fpr_req": "   ", "tot_sel_req": None}
    partial_result = parse_quote_levels(partial)
    assert partial_result["asks"][0] == {"level": 1, "price": 0.0, "qty": 0.0}
    assert partial_result["total_ask_qty"] == 0.0
