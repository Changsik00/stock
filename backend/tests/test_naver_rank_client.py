"""Unit tests for app.clients.naver_rank (sise_deal_rank_iframe.naver parsing).

No real network involved. The fixture response text below is a trimmed sample of the
actual `finance.naver.com/sise/sise_deal_rank_iframe.naver` response, captured via
requests for sosok=01&investor_gubun=9000&type=buy on 2026-07-18 (PLAN.md §4.5/§7).
The real page always returns the two most recent trading days with no date parameter
support (see naver_rank.py docstring) — the fixture keeps that two-block shape with a
handful of rows per block.
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import naver_rank

BUY_BODY = """
<div style="text-align:left;">
	<h4 class="top_tlt"><em>외국인</em>순매수 (단위:천주, 백만원)</h4>
	<div class="box_type_ms" style=" margin-top:0">
		<div class="sise_guide_date">26.07.15</div>
		<table cellpadding="0" cellspacing="0" class="type_1">
			<tr>
				<td><p class="tit"><a href="/item/main.naver?code=000660" class="tltle" target="_top" title='SK하이닉스'>SK하이닉스</a></p></td>
				<td class="number">331</td>
				<td class="number">714,008</td>
				<td class="number">6,046,951</td>
			</tr>
			<tr>
				<td><p class="tit"><a href="/item/main.naver?code=0195S0" class="tltle" target="_top" title='TIGER SK하이닉스단일종목레버리지'>TIGER SK하이닉스단일종목레버리지</a></p></td>
				<td class="number">5,194</td>
				<td class="number">85,668</td>
				<td class="number">141,606,843</td>
			</tr>
		</table>
	</div>
	<div class="box_type_ms" style="margin-left:9px; margin-top:0">
		<div class="sise_guide_date">26.07.16</div>
		<table cellpadding="0" cellspacing="0" class="type_1">
			<tr>
				<td><p class="tit"><a href="/item/main.naver?code=252670" class="tltle" target="_top" title='KODEX 200선물인버스2X'>KODEX 200선물인버스2X</a></p></td>
				<td class="number">1,234</td>
				<td class="number">97,456</td>
				<td class="number">1,000,000</td>
			</tr>
		</table>
	</div>
</div>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_fetch_deal_rank_parses_blocks_date_ascending(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(BUY_BODY)

    monkeypatch.setattr(naver_rank.requests, "get", fake_get)

    blocks = naver_rank.fetch_deal_rank("kospi", "foreign")

    assert captured["url"] == naver_rank.IFRAME_URL
    assert captured["params"] == {"sosok": "01", "investor_gubun": "9000", "type": "buy"}
    assert captured["headers"]["User-Agent"]

    assert [b["date"] for b in blocks] == [dt.date(2026, 7, 15), dt.date(2026, 7, 16)]
    assert blocks[0]["rows"] == [
        {"code": "000660", "name": "SK하이닉스", "net_value": 714008},
        {"code": "0195S0", "name": "TIGER SK하이닉스단일종목레버리지", "net_value": 85668},
    ]
    assert blocks[1]["rows"] == [
        {"code": "252670", "name": "KODEX 200선물인버스2X", "net_value": 97456},
    ]


def test_fetch_deal_rank_maps_institution_and_kosdaq_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(BUY_BODY)

    monkeypatch.setattr(naver_rank.requests, "get", fake_get)

    naver_rank.fetch_deal_rank("kosdaq", "institution")

    assert captured["params"] == {"sosok": "02", "investor_gubun": "1000", "type": "buy"}


def test_fetch_deal_rank_rejects_unknown_market():
    with pytest.raises(ValueError):
        naver_rank.fetch_deal_rank("nasdaq", "foreign")


def test_fetch_deal_rank_rejects_unknown_investor():
    with pytest.raises(ValueError):
        naver_rank.fetch_deal_rank("kospi", "retail")


def test_fetch_deal_rank_raises_on_no_date_blocks(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse("<div>no data here</div>")

    monkeypatch.setattr(naver_rank.requests, "get", fake_get)

    with pytest.raises(naver_rank.NaverRankError):
        naver_rank.fetch_deal_rank("kospi", "foreign")


class _FakeJsonResponse(_FakeResponse):
    def __init__(self, payload):
        super().__init__(text="")
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_etf_codes_extracts_itemcodes(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeJsonResponse(
            {
                "resultCode": "success",
                "result": {
                    "etfItemList": [
                        {"itemcode": "069500", "itemname": "KODEX 200"},
                        {"itemcode": "360750", "itemname": "TIGER 미국S&P500"},
                    ]
                },
            }
        )

    monkeypatch.setattr(naver_rank.requests, "get", fake_get)

    codes = naver_rank.fetch_etf_codes()

    assert captured["url"] == naver_rank.ETF_LIST_URL
    assert captured["headers"]["User-Agent"]
    assert codes == {"069500", "360750"}
