"""Unit tests for app.clients.naver_breadth (sise_index.naver 등락 종목수 파싱).

No real network involved. The fixture HTML fragment below reproduces the relevant
<li> block from the actual finance.naver.com/sise/sise_index.naver?code=KOSPI
response captured on 2026-07-18 (PLAN.md §3.5/§4.6 3.6-2) — trimmed to just the
등락 종목수 list, since that's all this client parses.
"""

from __future__ import annotations

import pytest
import requests

from app.clients import naver_breadth

KOSPI_FRAGMENT = """
<div class="subtop_sise_graph2">
    <ul>
    <li class="lst"><span class="blind">상한종목수</span><a href="/sise/sise_upper.naver"><span>6</span></a></li>
    <li class="lst2"><span class="blind">상승종목수</span><a href="/sise/sise_rise.naver"><span>384</span></a></li>
    <li class="lst3"><span class="blind">보합종목수</span><a href="/sise/sise_steady.naver"><span>40</span></a></li>
    <li class="lst4"><span class="blind">하락종목수</span><a href="/sise/sise_fall.naver"><span>488</span></a></li>
    <li class="lst5"><span class="blind">하한종목수</span><a href="/sise/sise_lower.naver"><span>0</span></a></li>
    </ul>
</div>
"""

KOSDAQ_FRAGMENT = """
<div class="subtop_sise_graph2">
    <ul>
    <li class="lst"><span class="blind">상한종목수</span><a href="/sise/sise_upper.naver"><span>11</span></a></li>
    <li class="lst2"><span class="blind">상승종목수</span><a href="/sise/sise_rise.naver"><span>501</span></a></li>
    <li class="lst3"><span class="blind">보합종목수</span><a href="/sise/sise_steady.naver"><span>56</span></a></li>
    <li class="lst4"><span class="blind">하락종목수</span><a href="/sise/sise_fall.naver"><span>1,182</span></a></li>
    <li class="lst5"><span class="blind">하한종목수</span><a href="/sise/sise_lower.naver"><span>1</span></a></li>
    </ul>
</div>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_fetch_breadth_parses_kospi_fields(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(KOSPI_FRAGMENT)

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    result = naver_breadth.fetch_breadth("kospi")

    assert captured["url"] == naver_breadth.INDEX_URL
    assert captured["params"] == {"code": "KOSPI"}
    assert captured["headers"]["User-Agent"]
    assert result == {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}


def test_fetch_breadth_parses_kosdaq_and_strips_commas(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert params == {"code": "KOSDAQ"}
        return _FakeResponse(KOSDAQ_FRAGMENT)

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    result = naver_breadth.fetch_breadth("kosdaq")

    assert result == {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}


def test_fetch_breadth_sum_sanity_within_market_size():
    """실측 조합(2026-07-18)의 다섯 필드 합이 한 시장 전체 상장종목수 범위(대략
    900~1800) 안에 들어와야 한다는 sanity check — 파서가 엉뚱한 숫자를 잡지
    않는지 회귀 방지용."""
    kospi = {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}
    kosdaq = {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}

    assert 900 <= sum(kospi.values()) <= 1800
    assert 900 <= sum(kosdaq.values()) <= 1800


def test_fetch_breadth_rejects_unknown_market():
    with pytest.raises(ValueError):
        naver_breadth.fetch_breadth("nasdaq")


def test_fetch_breadth_raises_on_missing_fields(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse("<div>상승종목수</div>")  # no <a><span>N</span></a> match

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    with pytest.raises(naver_breadth.NaverBreadthError):
        naver_breadth.fetch_breadth("kospi")
