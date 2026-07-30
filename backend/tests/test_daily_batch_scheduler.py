"""회귀 테스트: 평일 18:00 일별 배치 + 평일 07:30 macro 조기 배치의
misfire_grace_time / 등록 여부 (PLAN.md §5.11의 2026-07-22 사고, §5.22).

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

**2026-07-24 추가(PLAN.md §5.22)**: 07:30 macro 전용 조기 배치(미국장이 한국시간
새벽에 이미 마감해도 기존 18:00 배치까지 기다려야 했던 문제의 해결책)가 같은
실수(그레이스 타임 누락)를 처음부터 반복하지 않도록, 그리고 REGISTRY 전체가
아니라 "macro" 잡 하나만 도는지 별도로 검증한다.

**2026-07-31 추가(PLAN.md §5.46)**: 19:30 flow_rank 전용 저녁 재수집 배치(네이버
소스가 18:00 정기 배치 시점엔 그날 최종 랭킹을 아직 발행하지 않았을 수 있다는
문제의 해결책 — 실측: 07-30 18:03 실행분 collect_log.message가 "실제 적재된
날짜: 2026-07-28, 2026-07-29"로 하루 이상 뒤처진 재적재를 남김)도 같은 패턴으로
검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import scheduler as scheduler_module


class _FakeScheduler:
    def __init__(self, *args, **kwargs):
        self.calls: list[tuple[tuple, dict]] = []

    def add_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def start(self):
        pass

    def _call_for_id(self, job_id: str) -> dict:
        for args, kwargs in self.calls:
            if kwargs.get("id") == job_id:
                return kwargs
        raise AssertionError(f"no add_job call found for id={job_id!r}")


@pytest.fixture
def fake_scheduler(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeScheduler)
    scheduler = scheduler_module.start_scheduler()
    yield scheduler
    monkeypatch.setattr(scheduler_module, "_scheduler", None)  # 다른 테스트에 영향 없게 정리


def test_daily_batch_job_has_generous_misfire_grace_time(fake_scheduler):
    kwargs = fake_scheduler._call_for_id("daily_batch")
    assert "misfire_grace_time" in kwargs
    # 최소 30분 — 기본값(사실상 초 단위)으로 되돌아가는 회귀를 잡는 게 목적이라
    # 정확한 값(3600)에 묶어두지 않고 "충분히 크다"만 확인한다.
    assert kwargs["misfire_grace_time"] >= 1800


def test_macro_morning_job_registered_with_generous_misfire_grace_time(fake_scheduler):
    kwargs = fake_scheduler._call_for_id("macro_morning")
    assert kwargs["replace_existing"] is True
    assert kwargs["misfire_grace_time"] >= 1800

    # add_job(func, trigger, ...) — trigger는 두 번째 위치 인자로 전달됨
    args = [a for a in fake_scheduler.calls if a[1].get("id") == "macro_morning"][0][0]
    trigger = args[1]
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "7"
    assert fields["minute"] == "30"
    assert fields["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "Asia/Seoul"


async def test_run_macro_job_runs_only_macro(monkeypatch):
    calls = []

    async def fake_run_job(job_name, target_date, collect_fn):
        calls.append((job_name, target_date, collect_fn))
        return {"job": job_name, "status": "ok", "rows": 0}

    sentinel_fn = object()
    monkeypatch.setattr(scheduler_module, "REGISTRY", {"macro": sentinel_fn, "other": object()})
    monkeypatch.setattr(scheduler_module, "run_job", fake_run_job)

    await scheduler_module._run_macro_job()

    assert len(calls) == 1
    job_name, target_date, collect_fn = calls[0]
    assert job_name == "macro"
    assert collect_fn is sentinel_fn
    assert target_date == dt.date.today()


async def test_run_macro_job_warns_and_skips_when_macro_missing(monkeypatch, caplog):
    calls = []

    async def fake_run_job(job_name, target_date, collect_fn):
        calls.append(job_name)
        return {}

    monkeypatch.setattr(scheduler_module, "REGISTRY", {"other": object()})
    monkeypatch.setattr(scheduler_module, "run_job", fake_run_job)

    with caplog.at_level("WARNING"):
        await scheduler_module._run_macro_job()

    assert calls == []
    assert any("macro" in rec.message for rec in caplog.records)


def test_flow_rank_catchup_job_registered_with_generous_misfire_grace_time(fake_scheduler):
    kwargs = fake_scheduler._call_for_id("flow_rank_catchup")
    assert kwargs["replace_existing"] is True
    assert kwargs["misfire_grace_time"] >= 1800

    # add_job(func, trigger, ...) — trigger는 두 번째 위치 인자로 전달됨
    args = [a for a in fake_scheduler.calls if a[1].get("id") == "flow_rank_catchup"][0][0]
    trigger = args[1]
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "19"
    assert fields["minute"] == "30"
    assert fields["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "Asia/Seoul"


async def test_run_flow_rank_catchup_job_runs_only_flow_rank(monkeypatch):
    calls = []

    async def fake_run_job(job_name, target_date, collect_fn):
        calls.append((job_name, target_date, collect_fn))
        return {"job": job_name, "status": "ok", "rows": 0}

    sentinel_fn = object()
    monkeypatch.setattr(
        scheduler_module, "REGISTRY", {"flow_rank": sentinel_fn, "other": object()}
    )
    monkeypatch.setattr(scheduler_module, "run_job", fake_run_job)

    await scheduler_module._run_flow_rank_catchup_job()

    assert len(calls) == 1
    job_name, target_date, collect_fn = calls[0]
    assert job_name == "flow_rank"
    assert collect_fn is sentinel_fn
    assert target_date == dt.date.today()


async def test_run_flow_rank_catchup_job_warns_and_skips_when_flow_rank_missing(
    monkeypatch, caplog
):
    calls = []

    async def fake_run_job(job_name, target_date, collect_fn):
        calls.append(job_name)
        return {}

    monkeypatch.setattr(scheduler_module, "REGISTRY", {"other": object()})
    monkeypatch.setattr(scheduler_module, "run_job", fake_run_job)

    with caplog.at_level("WARNING"):
        await scheduler_module._run_flow_rank_catchup_job()

    assert calls == []
    assert any("flow_rank" in rec.message for rec in caplog.records)
