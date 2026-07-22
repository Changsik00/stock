"""Unit tests for app.collectors.intraday_snapshot — the in-memory "오늘 장중
누적" buffer (PLAN.md §5.4-2/3, §5.10) that collectors/live_refresh.py's 60초/7분
잡이 feeds with the already-fetched return values of routers.markets._warm_flow_live
/ _warm_futures_flow_live (no new HTTP/kiwoom/naver calls, tested elsewhere).

house convention for faking "now" (see test_market_hours.py): that module tests
market_hours.is_market_closed by constructing explicit datetimes. This module
computes "today"/"HH:MM" via two tiny private helpers (_today_kst/_now_hhmm_kst)
specifically so tests can monkeypatch just those instead of freezing global
time — module-level state (_buffers/_buffer_date) is reset directly between
tests via an autouse fixture rather than relying on real day rollovers.

**PLAN.md §5.10 (2026-07-22)**: `_buffers` is no longer a flat dict of 3
investor series — kospi/kosdaq now each keep their own 개인/외국인/기관계
series (`record_flow_snapshot` no longer sums the two markets). `get_flow_series`
reflects this split. `get_foreign_position_series` must still return the
kospi+kosdaq *sum* for its "spot" series (regression coverage for the "외인
양손" modal, which is out of scope for §5.10 and must keep working exactly as
before).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import intraday_snapshot as snap


@pytest.fixture(autouse=True)
def _reset_buffers():
    """Every test starts from a clean slate — module-level dicts/lists are
    process-global, so a previous test's points must not leak into the next."""

    def _clear_all():
        for value in snap._buffers.values():
            if isinstance(value, list):
                value.clear()
            else:
                for series in value.values():
                    series.clear()

    _clear_all()
    snap._buffer_date = None
    yield
    _clear_all()
    snap._buffer_date = None


def _flow_payload(kospi_gaein, kosdaq_gaein, market_closed=False):
    return {
        "kospi": {
            "date": "2026-07-21",
            "investors": {
                "개인": {"net_value": kospi_gaein, "net_volume": None},
                "외국인": {"net_value": 100, "net_volume": None},
                "기관계": {"net_value": -50, "net_volume": None},
            },
            "provisional": True,
            "source": "kiwoom_live",
        },
        "kosdaq": {
            "date": "2026-07-21",
            "investors": {
                "개인": {"net_value": kosdaq_gaein, "net_volume": None},
                "외국인": {"net_value": 10, "net_volume": None},
                "기관계": {"net_value": -5, "net_volume": None},
            },
            "provisional": True,
            "source": "kiwoom_live",
        },
        "market_closed": market_closed,
        "cached_at": "2026-07-21T03:00:00+00:00",
    }


def _futures_payload(net_value, market_closed=False):
    return {
        "date": "2026-07-21",
        "investors": {"외국인": {"net_value": net_value, "net_volume": None}},
        "market_closed": market_closed,
        "cached_at": "2026-07-21T03:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# record_flow_snapshot
# ---------------------------------------------------------------------------


def test_record_flow_snapshot_keeps_kospi_and_kosdaq_separate(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")

    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    assert snap._buffers["kospi"]["개인"] == [{"time": "10:00", "value": 100}]
    assert snap._buffers["kospi"]["외국인"] == [{"time": "10:00", "value": 100}]
    assert snap._buffers["kospi"]["기관계"] == [{"time": "10:00", "value": -50}]
    assert snap._buffers["kosdaq"]["개인"] == [{"time": "10:00", "value": 20}]
    assert snap._buffers["kosdaq"]["외국인"] == [{"time": "10:00", "value": 10}]
    assert snap._buffers["kosdaq"]["기관계"] == [{"time": "10:00", "value": -5}]
    # futures 시리즈는 record_flow_snapshot과 무관 — 건드리지 않는다.
    assert snap._buffers["외인선물"] == []


def test_record_flow_snapshot_grows_series_across_multiple_calls(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))

    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:01")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=200, kosdaq_gaein=30))

    assert [p["time"] for p in snap._buffers["kospi"]["개인"]] == ["10:00", "10:01"]
    assert [p["value"] for p in snap._buffers["kospi"]["개인"]] == [100, 200]
    assert [p["value"] for p in snap._buffers["kosdaq"]["개인"]] == [20, 30]


def test_record_flow_snapshot_market_closed_appends_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")

    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20, market_closed=True))

    assert snap._buffers["kospi"]["개인"] == []
    assert snap._buffers["kosdaq"]["개인"] == []


def test_record_flow_snapshot_one_market_missing_only_appends_the_other(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")

    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kosdaq"] = None

    snap.record_flow_snapshot(payload)

    assert snap._buffers["kospi"]["개인"] == [{"time": "10:00", "value": 100}]
    assert snap._buffers["kosdaq"]["개인"] == []


def test_record_flow_snapshot_both_markets_missing_skips_everything(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")

    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kospi"] = None
    payload["kosdaq"] = None

    snap.record_flow_snapshot(payload)

    assert snap._buffers["kospi"]["개인"] == []
    assert snap._buffers["kosdaq"]["개인"] == []


# ---------------------------------------------------------------------------
# record_futures_flow_snapshot
# ---------------------------------------------------------------------------


def test_record_futures_flow_snapshot_appends_foreign_net_value(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:07")

    snap.record_futures_flow_snapshot(_futures_payload(456))

    assert snap._buffers["외인선물"] == [{"time": "10:07", "value": 456}]
    # flow 시리즈는 건드리지 않는다.
    assert snap._buffers["kospi"]["개인"] == []
    assert snap._buffers["kosdaq"]["개인"] == []


def test_record_futures_flow_snapshot_market_closed_appends_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:07")

    snap.record_futures_flow_snapshot(_futures_payload(456, market_closed=True))

    assert snap._buffers["외인선물"] == []


def test_record_futures_flow_snapshot_missing_investor_appends_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:07")

    snap.record_futures_flow_snapshot({"date": "2026-07-21", "investors": {}, "market_closed": False})

    assert snap._buffers["외인선물"] == []


# ---------------------------------------------------------------------------
# 자정 리셋 (다음 append 때 지연 감지)
# ---------------------------------------------------------------------------


def test_date_rollover_clears_all_buffers_on_next_append(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "15:29")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))
    snap.record_futures_flow_snapshot(_futures_payload(456))
    assert snap._buffers["kospi"]["개인"] != []
    assert snap._buffers["kosdaq"]["개인"] != []
    assert snap._buffers["외인선물"] != []

    # 다음 거래일 첫 워밍 — 날짜가 바뀌었으니 append 전에 전부 비워져야 한다.
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 22))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "09:00")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=5, kosdaq_gaein=1))

    assert snap._buffers["kospi"]["개인"] == [{"time": "09:00", "value": 5}]
    assert snap._buffers["kosdaq"]["개인"] == [{"time": "09:00", "value": 1}]
    # 어제 쌓인 외인선물 포인트도 함께 비워졌다 — 아직 오늘 futures 워밍(7분 잡)은
    # 안 돌았으니 빈 채로.
    assert snap._buffers["외인선물"] == []
    assert snap._buffer_date == dt.date(2026, 7, 22)


# ---------------------------------------------------------------------------
# 500포인트 캡
# ---------------------------------------------------------------------------


def test_max_points_per_series_drops_oldest(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))

    for i in range(snap.MAX_POINTS_PER_SERIES + 10):
        monkeypatch.setattr(snap, "_now_hhmm_kst", lambda i=i: f"{i:04d}")
        snap.record_futures_flow_snapshot(_futures_payload(i))

    series = snap._buffers["외인선물"]
    assert len(series) == snap.MAX_POINTS_PER_SERIES
    # 가장 오래된 10개(값 0..9)가 버려지고, 마지막 점은 최신 값이어야 한다.
    assert series[0]["value"] == 10
    assert series[-1]["value"] == snap.MAX_POINTS_PER_SERIES + 9


# ---------------------------------------------------------------------------
# get_flow_series / get_foreign_position_series
# ---------------------------------------------------------------------------


def test_get_flow_series_shape_and_date_when_empty(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)

    result = snap.get_flow_series()

    assert result == {
        "date": "2026-07-21",
        "series": {
            "kospi": {"개인": [], "외국인": [], "기관계": []},
            "kosdaq": {"개인": [], "외국인": [], "기관계": []},
        },
        "market_closed": False,
    }


def test_get_flow_series_reflects_appended_points_split_by_market(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)

    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))
    result = snap.get_flow_series()

    assert result["series"]["kospi"]["개인"] == [{"time": "10:00", "value": 100}]
    assert result["series"]["kosdaq"]["개인"] == [{"time": "10:00", "value": 20}]
    assert result["market_closed"] is False


def test_get_flow_series_market_closed_reflects_current_clock_not_stored_value(monkeypatch):
    # market_closed는 저장된 값이 아니라 호출 시점에 새로 계산한다(모듈 docstring).
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: True)
    result = snap.get_flow_series()

    assert result["market_closed"] is True
    # 이미 적립된 점은 그대로 남아 있다 — market_closed 재계산이 버퍼를 지우지 않는다.
    assert result["series"]["kospi"]["개인"] == [{"time": "10:00", "value": 100}]


def test_get_foreign_position_series_sums_kospi_and_kosdaq_by_time(monkeypatch):
    # 회귀 테스트(PLAN.md §5.10 필수 요구): §5.10로 kospi/kosdaq 버퍼가 분리된
    # 뒤에도 "외인 양손" 모달의 현물 시리즈는 여전히 두 시장 합산값이어야 한다.
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)

    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))
    snap.record_futures_flow_snapshot(_futures_payload(456))

    result = snap.get_foreign_position_series()

    assert result["date"] == "2026-07-21"
    # kospi 외국인(100) + kosdaq 외국인(10) = 110 (기존 합산 동작과 동일).
    assert result["spot"] == [{"time": "10:00", "value": 110}]
    assert result["futures"] == [{"time": "10:00", "value": 456}]
    assert result["market_closed"] is False


def test_get_foreign_position_series_sums_across_multiple_ticks(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)

    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=100, kosdaq_gaein=20))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:01")
    snap.record_flow_snapshot(_flow_payload(kospi_gaein=200, kosdaq_gaein=30))

    result = snap.get_foreign_position_series()

    # 두 틱 모두 kospi 외국인(100)+kosdaq 외국인(10) = 110씩(payload가 외국인
    # net_value를 고정값으로 쓰므로 두 틱 다 동일).
    assert result["spot"] == [
        {"time": "10:00", "value": 110},
        {"time": "10:01", "value": 110},
    ]


def test_get_foreign_position_series_one_market_missing_uses_available_side(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "_now_hhmm_kst", lambda: "10:00")
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)

    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kosdaq"] = None
    snap.record_flow_snapshot(payload)

    result = snap.get_foreign_position_series()

    # kosdaq이 통째로 빠졌으니 kospi 외국인(100)만 반영된다.
    assert result["spot"] == [{"time": "10:00", "value": 100}]


def test_get_series_reports_today_even_with_empty_buffer(monkeypatch):
    # _buffer_date가 아직 None이어도(한 번도 append 안 됨) date는 오늘을 보고한다.
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    assert snap._buffer_date is None

    result = snap.get_foreign_position_series()

    assert result["date"] == "2026-07-21"
    assert result["spot"] == []
    assert result["futures"] == []
