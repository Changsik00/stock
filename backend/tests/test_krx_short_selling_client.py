"""Unit tests for app.clients.krx_short_selling (data.krx.co.kr 공매도 통계 포털).

No real network involved. Fixture payloads are trimmed to the actual shape
captured via real curl calls (2026-07-27, PLAN.md §5.32) — see the module
docstring for the full investigation writeup (why this endpoint needs no
login/OTP unlike the general KRX statistics portal, the T+2 순보유잔고 지연,
and the "코스피/코스닥 = 시장 자신을 가리키는 idxIndCd=001" reuse of the
per-industry screen for market-wide figures).
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import krx_short_selling as k


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def setup_function(_):
    # 종목코드->ISIN 캐시가 모듈 전역이라 테스트 간 오염을 막는다.
    k._ISIN_CACHE.clear()


def test_parse_int_handles_commas_and_dash():
    assert k._parse_int("793,057") == 793057
    assert k._parse_int("-") is None
    assert k._parse_int(None) is None
    assert k._parse_int("0") == 0
    assert k._parse_int("not-a-number") is None


def test_parse_won_to_million_converts_and_rounds():
    # 실측(005930, 2026-07-24): 203,400,170,750원 -> 203,400백만원(반올림).
    assert k._parse_won_to_million("203,400,170,750") == 203400
    assert k._parse_won_to_million("-") is None


def test_parse_float_handles_dash():
    assert k._parse_float("4.04") == 4.04
    assert k._parse_float("-") is None
    assert k._parse_float(None) is None


def test_parse_trd_dd():
    assert k._parse_trd_dd("2026/07/24") == dt.date(2026, 7, 24)
    assert k._parse_trd_dd("garbage") is None


def test_resolve_isin_parses_finder_response_and_caches(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return _FakeResponse(
            {"output": [{"tbox": "005930/삼성전자", "code": "KR7005930003", "codeNm": "삼성전자", "marketCode": "STK"}]}
        )

    monkeypatch.setattr(k.requests, "get", fake_get)

    isin = k.resolve_isin("005930")
    assert isin == "KR7005930003"
    assert calls[0][1] == {"bld": k._BLD_FINDER_ISIN, "isuCd": "005930", "locale": "ko_KR"}
    assert calls[0][2]["Referer"]

    # Second call must hit the cache, not requests.get again.
    k.resolve_isin("005930")
    assert len(calls) == 1


def test_resolve_isin_raises_when_not_found(monkeypatch):
    monkeypatch.setattr(k.requests, "get", lambda *a, **kw: _FakeResponse({"output": []}))

    with pytest.raises(k.KrxShortSellingError):
        k.resolve_isin("999999")


def test_fetch_stock_short_selling_parses_rows_and_converts_won_to_million(monkeypatch):
    monkeypatch.setattr(
        k.requests,
        "get",
        lambda *a, **kw: _FakeResponse({"output": [{"code": "KR7005930003"}]}),
    )

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        # 실측(005930, 2026-07-27 curl) 응답 부분집합 — 최근 2거래일은 T+2 지연으로
        # 순보유잔고가 "-", 그 이전은 채워짐.
        return _FakeResponse(
            {
                "OutBlock_1": [
                    {
                        "TRD_DD": "2026/07/24",
                        "CVSRTSELL_TRDVOL": "793,057",
                        "UPTICKRULE_APPL_TRDVOL": "355,289",
                        "UPTICKRULE_EXCPT_TRDVOL": "437,768",
                        "STR_CONST_VAL1": "-",
                        "CVSRTSELL_TRDVAL": "203,400,170,750",
                        "UPTICKRULE_APPL_TRDVAL": "90,925,403,750",
                        "UPTICKRULE_EXCPT_TRDVAL": "112,474,767,000",
                        "STR_CONST_VAL2": "-",
                    },
                    {
                        "TRD_DD": "2026/07/22",
                        "CVSRTSELL_TRDVOL": "726,133",
                        "UPTICKRULE_APPL_TRDVOL": "412,305",
                        "UPTICKRULE_EXCPT_TRDVOL": "313,828",
                        "STR_CONST_VAL1": "1,464,868",
                        "CVSRTSELL_TRDVAL": "196,540,713,250",
                        "UPTICKRULE_APPL_TRDVAL": "111,490,149,750",
                        "UPTICKRULE_EXCPT_TRDVAL": "85,050,563,500",
                        "STR_CONST_VAL2": "381,598,114,000",
                    },
                ]
            }
        )

    monkeypatch.setattr(k.requests, "post", fake_post)

    rows = k.fetch_stock_short_selling("005930", dt.date(2026, 7, 20), dt.date(2026, 7, 27))

    assert captured["data"]["isuCd"] == "KR7005930003"
    assert captured["data"]["bld"] == k._BLD_STOCK_COMPREHENSIVE
    assert captured["headers"]["Referer"] == k._REFERER_STOCK

    # 날짜 오름차순 정렬 확인.
    assert [r["date"] for r in rows] == [dt.date(2026, 7, 22), dt.date(2026, 7, 24)]

    recent = rows[1]
    assert recent["date"] == dt.date(2026, 7, 24)
    assert recent["volume"] == 793057
    assert recent["value"] == 203400  # 203,400,170,750원 -> 203,400백만원
    assert recent["balance_qty"] is None  # T+2 지연 "-"
    assert recent["balance_value"] is None

    older = rows[0]
    assert older["balance_qty"] == 1464868
    assert older["balance_value"] == 381598  # 381,598,114,000원 -> 381,598백만원


def test_fetch_market_short_selling_parses_ratio_fields(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["data"] = data
        captured["headers"] = headers
        # 실측(코스피, 2026-07-24 curl) 응답 부분집합.
        return _FakeResponse(
            {
                "OutBlock_1": [
                    {
                        "TRD_DD": "2026/07/24",
                        "CVSRTSELL_TRDVOL": "17,728,078",
                        "UPTICKRULE_APPL_TRDVOL": "15,932,052",
                        "UPTICKRULE_EXCPT_TRDVOL": "1,796,026",
                        "ACC_TRDVOL": "438,777,893",
                        "TRDVOL_WT": "4.04",
                        "CVSRTSELL_TRDVAL": "1,708,956,089,320",
                        "UPTICKRULE_APPL_TRDVAL": "1,327,596,889,778",
                        "UPTICKRULE_EXCPT_TRDVAL": "381,359,199,542",
                        "ACC_TRDVAL": "40,282,837,389,194",
                        "TRDVAL_WT": "4.24",
                    }
                ]
            }
        )

    monkeypatch.setattr(k.requests, "post", fake_post)

    rows = k.fetch_market_short_selling("kospi", dt.date(2026, 7, 20), dt.date(2026, 7, 27))

    assert captured["data"]["mktTpCd"] == "1"
    assert captured["data"]["idxIndCd"] == "001"
    assert captured["data"]["indAggClssCd"] == "001"
    assert captured["headers"]["Referer"] == k._REFERER_MARKET

    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == dt.date(2026, 7, 24)
    assert row["short_volume"] == 17728078
    assert row["short_value"] == 1708956  # 1,708,956,089,320원 -> 1,708,956백만원
    assert row["total_volume"] == 438777893
    assert row["total_value"] == 40282837
    assert row["volume_ratio"] == 4.04
    assert row["value_ratio"] == 4.24


def test_fetch_market_short_selling_rejects_unknown_market():
    with pytest.raises(ValueError):
        k.fetch_market_short_selling("futures", dt.date(2026, 7, 1), dt.date(2026, 7, 27))


def test_fetch_market_short_selling_raises_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(k.requests, "post", lambda *a, **kw: _FakeResponse({"unexpected": True}))

    with pytest.raises(k.KrxShortSellingError):
        k.fetch_market_short_selling("kospi", dt.date(2026, 7, 1), dt.date(2026, 7, 27))
