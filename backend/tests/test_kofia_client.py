"""Unit tests for app.clients.kofia using httpx.MockTransport.

No real network involved. Fixture response bodies are trimmed samples of the
actual `/meta/getMetaDataList.do` responses captured with Playwright against
freesis.kofia.or.kr on 2026-07-17 (see PLAN.md Phase 1.5-2). These pin down the
request shape (dmSearch body: tmpV1/tmpV45/tmpV46/OBJ_NM, plus tmpV72 for the
대차거래추이 service) and the response parsing (column mapping + summary-row
filtering for lending_balance's "합계"/"평균" rows).
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from app.clients import kofia

INVESTOR_DEPOSIT_BODY = json.dumps(
    {
        "unit": "",
        "ds1": [
            {
                "TMPV1": "20260715",
                "TMPV2": 109866972,
                "TMPV3": 49577784,
                "TMPV4": 108701220,
                "TMPV5": 1396799,
                "TMPV6": 38467,
                "TMPV7": 3.8,
            },
            {
                "TMPV1": "20260714",
                "TMPV2": 111282480,
                "TMPV3": 47499250,
                "TMPV4": 108657584,
                "TMPV5": 1013673,
                "TMPV6": 21563,
                "TMPV7": 1.9,
            },
        ],
    }
)

CREDIT_LOAN_BODY = json.dumps(
    {
        "unit": "",
        "ds1": [
            {
                "TMPV1": "20260715",
                "TMPV2": 34370184,
                "TMPV3": 27135448,
                "TMPV4": 7234735,
                "TMPV5": 26953,
                "TMPV6": 23784,
                "TMPV7": 3169,
                "TMPV8": 0,
                "TMPV9": 25877910,
            },
            {
                "TMPV1": "20260714",
                "TMPV2": 34707755,
                "TMPV3": 27452720,
                "TMPV4": 7255034,
                "TMPV5": 28580,
                "TMPV6": 25297,
                "TMPV7": 3283,
                "TMPV8": 0,
                "TMPV9": 24804274,
            },
        ],
    }
)

LENDING_BALANCE_BODY = json.dumps(
    {
        "unit": "",
        "ds1": [
            {
                "TMPV1": "20260716",
                "TMPV2": "전체",
                "TMPV3": 44771014,
                "TMPV4": 57034003,
                "TMPV5": 2908117928,
                "TMPV6": 150472104,
            },
            {
                "TMPV1": "20260715",
                "TMPV2": "전체",
                "TMPV3": 43302051,
                "TMPV4": 48989008,
                "TMPV5": 2920380917,
                "TMPV6": 160748096,
            },
            # freesis appends non-date summary rows at the end of the range —
            # these must be filtered out, not parsed as a date.
            {
                "TMPV1": "합계",
                "TMPV2": "-",
                "TMPV3": 3779639385,
                "TMPV4": 4028478499,
                "TMPV5": 185922194976,
                "TMPV6": 10855607751,
            },
            {
                "TMPV1": "평균",
                "TMPV2": "-",
                "TMPV3": 60961926,
                "TMPV4": 64975460,
                "TMPV5": 2998745080,
                "TMPV6": 175090448,
            },
        ],
    }
)


@pytest.fixture
def make_client():
    def _make(handler):
        transport = httpx.MockTransport(handler)
        return httpx.Client(transport=transport)

    return _make


def _capture_request_handler(response_body: str, captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["json"] = json.loads(request.content)
        assert request.headers["content-type"] == "application/json;charset=UTF-8"
        return httpx.Response(200, text=response_body)

    return handler


def test_fetch_investor_deposit_request_shape_and_parsing(make_client):
    captured: dict = {}
    client = make_client(_capture_request_handler(INVESTOR_DEPOSIT_BODY, captured))

    start = dt.date(2026, 4, 15)
    end = dt.date(2026, 7, 15)
    rows = kofia.fetch_investor_deposit(client, start, end)

    assert captured["method"] == "POST"
    assert captured["url"] == kofia.DATA_ENDPOINT
    dm_search = captured["json"]["dmSearch"]
    assert dm_search["tmpV1"] == "D"
    assert dm_search["tmpV45"] == "20260415"
    assert dm_search["tmpV46"] == "20260715"
    assert dm_search["OBJ_NM"] == "STATSCU0100000060BO"

    # Rows come back sorted ascending by date; TMPV2 is 투자자예탁금(제외파생), 백만원.
    assert rows == [
        {"date": dt.date(2026, 7, 14), "value": 111282480.0},
        {"date": dt.date(2026, 7, 15), "value": 109866972.0},
    ]


def test_fetch_credit_loan_splits_kospi_kosdaq(make_client):
    captured: dict = {}
    client = make_client(_capture_request_handler(CREDIT_LOAN_BODY, captured))

    result = kofia.fetch_credit_loan(client, dt.date(2026, 4, 15), dt.date(2026, 7, 15))

    assert captured["json"]["dmSearch"]["OBJ_NM"] == "STATSCU0100000070BO"
    assert set(result.keys()) == {"credit_loan_kospi", "credit_loan_kosdaq"}
    # TMPV3 = 신용거래융자 유가증권(KOSPI), TMPV4 = 신용거래융자 코스닥(KOSDAQ).
    assert result["credit_loan_kospi"] == [
        {"date": dt.date(2026, 7, 14), "value": 27452720.0},
        {"date": dt.date(2026, 7, 15), "value": 27135448.0},
    ]
    assert result["credit_loan_kosdaq"] == [
        {"date": dt.date(2026, 7, 14), "value": 7255034.0},
        {"date": dt.date(2026, 7, 15), "value": 7234735.0},
    ]


def test_fetch_lending_balance_filters_summary_rows_and_sets_stock_filter(make_client):
    captured: dict = {}
    client = make_client(_capture_request_handler(LENDING_BALANCE_BODY, captured))

    rows = kofia.fetch_lending_balance(client, dt.date(2026, 4, 16), dt.date(2026, 7, 16))

    dm_search = captured["json"]["dmSearch"]
    assert dm_search["OBJ_NM"] == "STATSCU0100000140BO"
    # Empty tmpV72 = no specific stock selected -> aggregate "전체" market total.
    assert dm_search["tmpV72"] == ""

    # "합계"/"평균" summary rows must be dropped, only TMPV6(금액) for real dates kept.
    assert rows == [
        {"date": dt.date(2026, 7, 15), "value": 160748096.0},
        {"date": dt.date(2026, 7, 16), "value": 150472104.0},
    ]


def test_post_raises_kofia_error_on_unexpected_shape(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = make_client(handler)
    with pytest.raises(kofia.KofiaError):
        kofia.fetch_investor_deposit(client, dt.date(2026, 1, 1), dt.date(2026, 1, 31))
