"""app/quant/auto_trade_performance.py 순수 함수 단위테스트 (PLAN.md §5.76).

전부 DB/네트워크 무관 — 합성 로그로 진입 방식별(자동/수동) pairing·집계가
올바른지 확인한다. `test_known_bad_log_id_correction` 이 핵심 — id=1920
(§5.71 실사고, 미체결 EOD강제청산)을 제외해야 실제 경제적 결과가 복원됨을
직접 재현한다.
"""

from __future__ import annotations

from app.quant.auto_trade_performance import KNOWN_BAD_LOG_IDS, summarize_by_entry_source


def _row(id_, ts, event_type, price):
    return {"id": id_, "ts": ts, "event_type": event_type, "price": price}


def test_empty_log_returns_empty_groups():
    result = summarize_by_entry_source([])
    assert result["by_source"]["auto"]["n"] == 0
    assert result["by_source"]["manual"]["n"] == 0
    assert result["trades"] == []
    assert result["anomalies"] == []
    assert result["open_position"] is None


def test_single_auto_trade_win():
    rows = [
        _row(1, "2026-01-01T00:00:00", "entry", 100.0),
        _row(2, "2026-01-01T01:00:00", "exit_trail", 105.0),
    ]
    result = summarize_by_entry_source(rows)
    assert result["by_source"]["auto"] == {
        "n": 1,
        "win_rate_pct": 100.0,
        "avg_return_pct": 5.0,
        "cumulative_return_pct": 5.0,
    }
    assert result["by_source"]["manual"]["n"] == 0
    assert len(result["trades"]) == 1
    assert result["trades"][0]["entry_source"] == "auto"
    assert result["trades"][0]["pct_return"] == 5.0


def test_single_manual_trade_loss():
    rows = [
        _row(1, "2026-01-01T00:00:00", "manual_entry", 100.0),
        _row(2, "2026-01-01T01:00:00", "exit_stop_loss", 98.5),
    ]
    result = summarize_by_entry_source(rows)
    assert result["by_source"]["manual"] == {
        "n": 1,
        "win_rate_pct": 0.0,
        "avg_return_pct": -1.5,
        "cumulative_return_pct": -1.5,
    }
    assert result["by_source"]["auto"]["n"] == 0


def test_mixed_sources_aggregate_separately():
    rows = [
        _row(1, "2026-01-01T00:00:00", "entry", 100.0),
        _row(2, "2026-01-01T01:00:00", "exit_stop_loss", 99.0),  # auto, -1%
        _row(3, "2026-01-02T00:00:00", "manual_entry", 100.0),
        _row(4, "2026-01-02T01:00:00", "exit_manual", 102.0),  # manual, +2%
        _row(5, "2026-01-03T00:00:00", "entry", 100.0),
        _row(6, "2026-01-03T01:00:00", "exit_eod_forced", 100.5),  # auto, +0.5%
    ]
    result = summarize_by_entry_source(rows)
    assert result["by_source"]["auto"]["n"] == 2
    assert result["by_source"]["auto"]["win_rate_pct"] == 50.0
    assert result["by_source"]["manual"]["n"] == 1
    assert result["by_source"]["manual"]["win_rate_pct"] == 100.0


def test_orphan_exit_is_flagged_and_excluded_from_stats():
    """짝지을 진입이 없는 청산 — 조용히 무시하지 않고 anomalies에 남긴다."""
    rows = [
        _row(1, "2026-01-01T00:00:00", "exit_stop_loss", 100.0),
    ]
    result = summarize_by_entry_source(rows)
    assert result["trades"] == []
    assert result["by_source"]["auto"]["n"] == 0
    assert len(result["anomalies"]) == 1
    assert result["anomalies"][0]["type"] == "orphan_exit"
    assert result["anomalies"][0]["id"] == 1


def test_unexpected_second_entry_is_flagged():
    """열린 진입이 있는데 청산 없이 또 진입이 나오면(정상적으로는 안 나와야
    함) 이전 진입은 버려지고(anomalies 기록) 새 진입이 열린 포지션이 된다."""
    rows = [
        _row(1, "2026-01-01T00:00:00", "entry", 100.0),
        _row(2, "2026-01-02T00:00:00", "manual_entry", 110.0),
        _row(3, "2026-01-02T01:00:00", "exit_stop_loss", 108.0),
    ]
    result = summarize_by_entry_source(rows)
    assert len(result["anomalies"]) == 1
    assert result["anomalies"][0]["type"] == "unexpected_second_entry"
    # 두 번째(수동) 진입만 실제로 청산과 짝지어져 집계된다.
    assert len(result["trades"]) == 1
    assert result["trades"][0]["entry_source"] == "manual"
    assert result["trades"][0]["entry_price"] == 110.0


def test_trailing_open_position_excluded_from_completed_stats():
    rows = [
        _row(1, "2026-01-01T00:00:00", "entry", 100.0),
        _row(2, "2026-01-01T01:00:00", "exit_stop_loss", 99.0),
        _row(3, "2026-01-02T00:00:00", "manual_entry", 100.0),  # 아직 안 닫힘(현재 보유 중)
    ]
    result = summarize_by_entry_source(rows)
    assert result["by_source"]["auto"]["n"] == 1
    assert result["by_source"]["manual"]["n"] == 0  # 열린 포지션은 완결 통계에 안 들어감
    assert result["open_position"] == {
        "entry_id": 3,
        "entry_ts": "2026-01-02T00:00:00",
        "entry_price": 100.0,
        "entry_source": "manual",
    }


def test_known_bad_log_id_correction():
    """PLAN.md §5.71 실사고 재현 — id=1920(미체결 EOD강제청산)을 제외해야
    그 다음 진짜 청산이 원래 진입과 정확히 짝지어져 실제 경제적 결과
    (-5.52%)가 나온다. 제외 안 하면 가짜 승리(+0.19%)로 잘못 집계되고
    진짜 청산은 orphan이 된다 — 이 테스트는 그 대조까지 함께 확인한다."""
    assert 1920 in KNOWN_BAD_LOG_IDS

    rows = [
        _row(1919, "2026-08-14T04:58:10", "manual_entry", 18015.0),
        _row(1920, "2026-08-14T06:20:35", "exit_eod_forced", 18050.0),  # 미체결(제외 대상)
        _row(2000, "2026-08-19T01:07:44", "exit_stop_loss", 17020.0),  # 진짜 청산
        _row(2001, "2026-08-19T01:12:11", "manual_entry", 17020.0),
        _row(2002, "2026-08-19T01:55:01", "exit_stop_loss", 16870.0),
    ]

    result = summarize_by_entry_source(rows)

    # id=1920은 건너뛰어졌으므로 8/14 진입은 8/19 진짜 청산과 짝지어진다.
    assert len(result["trades"]) == 2
    first_trade = result["trades"][0]
    assert first_trade["entry_id"] == 1919
    assert first_trade["exit_id"] == 2000
    assert first_trade["pct_return"] == round((17020.0 - 18015.0) / 18015.0 * 100, 4)
    assert first_trade["pct_return"] < -5.0  # 실제 손실(-5.52%대) 복원 확인
    assert result["anomalies"] == []  # orphan/이상 없이 깔끔하게 짝지어짐
