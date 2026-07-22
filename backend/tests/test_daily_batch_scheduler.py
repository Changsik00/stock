"""회귀 테스트: 평일 18:00 일별 배치의 misfire_grace_time (PLAN.md, 2026-07-22).

사용자가 "ETF 파생 방향성 차트가 이틀치뿐이라 이상하다"고 지적해 collect_log를
전수 조사한 결과, ``add_job``에 ``misfire_grace_time``이 없어(APScheduler
기본값이 사실상 초 단위) 이벤트 루프가 몇 분만 늦어도 그날의 18:00 배치
전체가 예외/로그 없이 조용히 스킵되고 있었다 — 실측: worker 로그에
"missed by 0:03:56" 경고만 남고 ``_run_all_jobs`` 자체가 호출된 흔적이
전혀 없었음(job이 실행됐다면 첫 줄에 남는 "scheduled batch starting..." INFO도
없었다). 지난 일주일 중 정규 스케줄로 실제 완주한 날이 사실상 0일이었다.

이 테스트는 스케줄러를 실제로 기동하지 않고(백그라운드 트리거 대기는
테스트에 부적합) ``add_job`` 호출 자체를 스파이해서 ``misfire_grace_time``
인자가 충분히 크게(최소 30분) 전달되는지만 검증한다 — 실제 misfire 재현은
비현실적이라, "설정이 있는지"만 고정해 다시 빠뜨리는 회귀를 잡는다.
"""

from __future__ import annotations

from app.collectors import scheduler as scheduler_module


def test_daily_batch_job_has_generous_misfire_grace_time(monkeypatch):
    captured = {}

    class _FakeScheduler:
        def __init__(self, *args, **kwargs):
            pass

        def add_job(self, *args, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeScheduler)

    scheduler_module.start_scheduler()
    monkeypatch.setattr(scheduler_module, "_scheduler", None)  # 다른 테스트에 영향 없게 정리

    assert "misfire_grace_time" in captured
    # 최소 30분 — 기본값(사실상 초 단위)으로 되돌아가는 회귀를 잡는 게 목적이라
    # 정확한 값(3600)에 묶어두지 않고 "충분히 크다"만 확인한다.
    assert captured["misfire_grace_time"] >= 1800
