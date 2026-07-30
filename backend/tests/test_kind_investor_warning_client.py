"""Unit tests for app.clients.kind_investor_warning (KRX 공시채널 KIND, PLAN.md
§5.39). No real network involved — fixture HTML fragments are trimmed to the
actual shape captured via real curl calls (2026-07-30) — see the module
docstring for the full investigation writeup (키움 TR 부재, data.krx.co.kr
로그인 장벽, KIND 무인증 콜드 POST 발견, tier별 스키마 차이).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.clients import kind_investor_warning as k


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        pass


# 실측(2026-07-30, menuIndex=2 투자경고) 응답 조각 — 해제된 것(가온전선)과
# 현재도 지정 중인 것(비비안, 해제일 "-") 둘 다 포함.
_WARNING_PAGE = """
<section class="scrarea type-00">
<table><tbody>
<tr>
<td class="first txc" scope="row">395</td>
<td title="가온전선"><img src='/images/common/icn_t_yu.gif' class='vmiddle legend' alt='유가증권'> <a id="companysum" href="#companysum" onclick="companysummary_open('00050'); return false;" title='가온전선'> 가온전선</a> </td>
<td class="txc">2026-04-27</td>
<td class="txc">2026-04-28</td>
<td class="txc">2026-05-11</td>
</tr>
<tr>
<td class="first txc" scope="row">399</td>
<td title="비비안"><img src='/images/common/icn_t_yu.gif' class='vmiddle legend' alt='유가증권'> <a id="companysum" href="#companysum" onclick="companysummary_open('00207'); return false;" title='비비안'> 비비안</a> </td>
<td class="txc">2026-07-16</td>
<td class="txc">2026-07-20</td>
<td class="txc">-</td>
</tr>
</tbody></table>
</section>
전체 <em>2</em>건 : <strong>1</strong>/1
"""

# 실측(2026-07-30, menuIndex=1 투자주의) 응답 조각 — 해제일 컬럼이 없고 "유형"
# 컬럼이 있다(스키마가 다름). "형지I&C"는 KIND가 HTML 엔티티로 이스케이프해서
# 준다(실측 버그 재현 케이스 — html.unescape 없으면 stocks.name 매칭 실패).
_CAUTION_PAGE = """
<section class="scrarea type-00">
<table><tbody>
<tr>
<td class="first txc" scope="row">672</td>
<td title="CJ씨푸드1우"><img src='/images/common/icn_t_yu.gif' class='vmiddle legend' alt='유가증권'> <a id="companysum" href="#companysum" onclick="companysummary_open('01115'); return false;" title='CJ씨푸드1우'> CJ씨푸드1우</a> <img src='/images/common/icn_t_kwan.gif' class='vmiddle legend' alt='관리종목'/> </td>
<td>종가급변</td>
<td class="txc">2026-07-29</td>
<td class="txc">2026-07-30</td>
</tr>
<tr>
<td class="first txc" scope="row">670</td>
<td title="형지I&amp;C"><img src='/images/common/icn_t_ko.gif' class='vmiddle legend' alt='코스닥'> <a id="companysum" href="#companysum" onclick="companysummary_open('01234'); return false;" title='형지I&amp;C'> 형지I&amp;C</a> </td>
<td>소수지점/계좌</td>
<td class="txc">2026-07-29</td>
<td class="txc">2026-07-30</td>
</tr>
</tbody></table>
</section>
전체 <em>2</em>건 : <strong>1</strong>/1
"""

_EMPTY_PAGE = """
<section class="scrarea type-00"><table><tbody></tbody></table></section>
전체 <em>0</em>건 : <strong>1</strong>/1
"""


def test_fetch_designations_rejects_unknown_tier():
    with pytest.raises(ValueError):
        k.fetch_designations("overheat", dt.date(2026, 1, 1), dt.date(2026, 7, 30))


def test_fetch_designations_warning_parses_released_and_active_rows(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return _FakeResponse(_WARNING_PAGE)

    monkeypatch.setattr(k.requests, "post", fake_post)

    rows = k.fetch_designations(k.TIER_WARNING, dt.date(2026, 1, 1), dt.date(2026, 7, 30))

    assert captured["url"] == k.ENDPOINT
    assert captured["data"]["forward"] == "invstwarnisu_sub"
    assert captured["data"]["menuIndex"] == "2"
    assert captured["data"]["searchCorpName"] == ""  # 모듈 docstring "회사명 검색 안 씀" 절
    assert captured["headers"]["Referer"] == k.REFERER

    assert len(rows) == 2
    released, active = rows

    assert released["raw_name"] == "가온전선"
    assert released["market"] == "KOSPI"
    assert released["notice_date"] == dt.date(2026, 4, 27)
    assert released["designated_date"] == dt.date(2026, 4, 28)
    assert released["released_date"] == dt.date(2026, 5, 11)
    assert released["warning_type"] is None

    # 해제일 "-" -> None(=아직 해제 안 됨=현재 지정 중).
    assert active["raw_name"] == "비비안"
    assert active["designated_date"] == dt.date(2026, 7, 20)
    assert active["released_date"] is None


def test_fetch_designations_caution_parses_type_and_has_no_released_date(monkeypatch):
    monkeypatch.setattr(k.requests, "post", lambda *a, **kw: _FakeResponse(_CAUTION_PAGE))

    rows = k.fetch_designations(k.TIER_CAUTION, dt.date(2026, 7, 20), dt.date(2026, 7, 30))

    assert len(rows) == 2
    first, second = rows

    assert first["raw_name"] == "CJ씨푸드1우"
    assert first["warning_type"] == "종가급변"
    assert first["released_date"] is None  # caution은 개념 자체가 없음(모듈 docstring)
    assert first["designated_date"] == dt.date(2026, 7, 30)

    # HTML 엔티티(&amp;) unescape 확인 — stocks.name과의 매칭 근거.
    assert second["raw_name"] == "형지I&C"
    assert second["market"] == "KOSDAQ"
    assert second["warning_type"] == "소수지점/계좌"


def test_fetch_designations_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(k.requests, "post", lambda *a, **kw: _FakeResponse(_EMPTY_PAGE))

    rows = k.fetch_designations(k.TIER_RISK, dt.date(2026, 1, 1), dt.date(2026, 7, 30))
    assert rows == []


def test_fetch_designations_paginates_until_total_pages_reached(monkeypatch):
    page1 = _WARNING_PAGE.replace("전체 <em>2</em>건 : <strong>1</strong>/1", "전체 <em>4</em>건 : <strong>1</strong>/2")
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(data["pageIndex"])
        if data["pageIndex"] == "1":
            return _FakeResponse(page1)
        return _FakeResponse(_WARNING_PAGE)  # page 2, total/1 -> loop stops here

    monkeypatch.setattr(k.requests, "post", fake_post)

    rows = k.fetch_designations(k.TIER_WARNING, dt.date(2026, 1, 1), dt.date(2026, 7, 30))

    assert calls == ["1", "2"]
    assert len(rows) == 4  # 2 rows/page * 2 pages


def test_fetch_designations_raises_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(k.requests, "post", lambda *a, **kw: _FakeResponse("<html>garbage</html>"))

    with pytest.raises(k.KindInvestorWarningError):
        k.fetch_designations(k.TIER_WARNING, dt.date(2026, 1, 1), dt.date(2026, 7, 30))
