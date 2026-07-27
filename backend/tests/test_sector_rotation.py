"""Unit tests for app.quant.sector_rotation (PLAN.md §5.33-2, 업종 로테이션
관찰 지표).

tests/test_concentration_backtest.py와 동일한 하우스 패턴(실 dev Postgres,
app.db.async_session_factory)을 쓰지만, 격리 기준은 "먼 미래 날짜"가 아니라
**가짜 market 문자열**이다 — `compute_sector_rotation`은 (concentration_backtest의
`_rolling_baseline`과 달리) 순수 함수가 아니라 그 자체가 "해당 market의 DB에 있는
모든 날짜"를 조회해 최신 날짜를 target으로, 그 이전 전부를 베이스라인 후보로 쓴다.
`collectors/market_flow.py`가 실제로 매일 kospi/kosdaq에 대해 sector_flow를 쌓고
있어(§5.33-1) 먼 미래 날짜를 써도 **같은 (market, sector_code)의 실제 과거 데이터가
함께 조회돼 베이스라인 윈도우에 섞여 들어간다**(실측으로 확인한 버그 — 예:
market="kospi", sector_code="009_AL"로 2099년 날짜만 심어도 all_dates에 2023~2026년
실데이터가 그대로 함께 잡혀 "최근 20거래일"이 실데이터로 채워짐). 그래서 이 파일은
실제 수집기가 절대 쓰지 않는 가짜 market 이름(`zzz_test_*`)을 써서 쿼리 자체가
테스트 행만 보게 만든다 — 날짜는 격리에 기여하지 않으므로 아무 값이나 써도 되지만
관례상 먼 미래로 유지한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import SectorFlow
from app.quant import sector_rotation

MARKET = "zzz_test_kospi"

# 6거래일: index 0~4(5일)는 베이스라인 구간, index 5는 오늘(target_date).
DATES = [dt.date(2099, 7, 1) + dt.timedelta(days=i) for i in range(6)]

# "전기/전자"(013_AL) — 평소 -100(외국인+기관계 signed) 꾸준한 순매도 업종인데,
# 오늘 -500으로 평소보다 훨씬 크게 순매도(유출 후보).
ELEC_BASELINE_SIGNED = -100
ELEC_TODAY_SIGNED = -500

# "음식료"(005_AL) — 평소 +20 꾸준한 순매수 업종인데, 오늘 +100으로 평소보다
# 훨씬 크게 순매수(유입 후보).
FOOD_BASELINE_SIGNED = 20
FOOD_TODAY_SIGNED = 100

# "대형주"(002_AL) — NON_INDUSTRY_SECTOR_CODES에 포함된 시가총액 규모별 분류.
# 값 자체는 극단적으로 크게 심어서, 필터링이 안 되면 랭킹 1위로 잡혀 테스트가
# 바로 실패하게 만든다.
SIZE_TIER_BASELINE_SIGNED = 1
SIZE_TIER_TODAY_SIGNED = 999_999

# "제약"(009_AL) — baseline_activity가 0(전부 0)이라 0-나눗셈 가드에 걸려
# 결과에서 제외돼야 하는 업종.
ZERO_BASELINE_SIGNED = 0
ZERO_TODAY_SIGNED = 50


def _split(value: int) -> tuple[int, int]:
    """signed 값을 외국인/기관계 두 정수로 나눈다(둘 다 부호를 절반씩 나눠
    가지면 합이 원래 값과 정확히 같다)."""
    half = value // 2
    return half, value - half


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            SectorFlow.__table__.delete().where(
                SectorFlow.market == MARKET, SectorFlow.date.in_(DATES)
            )
        )
        await session.commit()


async def _seed() -> None:
    async with async_session_factory() as session:
        for i, d in enumerate(DATES):
            is_today = i == 5
            elec_signed = ELEC_TODAY_SIGNED if is_today else ELEC_BASELINE_SIGNED
            food_signed = FOOD_TODAY_SIGNED if is_today else FOOD_BASELINE_SIGNED
            size_signed = SIZE_TIER_TODAY_SIGNED if is_today else SIZE_TIER_BASELINE_SIGNED
            zero_signed = ZERO_TODAY_SIGNED if is_today else ZERO_BASELINE_SIGNED

            for code, name, signed in (
                ("013_AL", "전기/전자", elec_signed),
                ("005_AL", "음식료", food_signed),
                ("002_AL", "대형주", size_signed),
                ("009_AL", "제약", zero_signed),
            ):
                frgnr, orgn = _split(signed)
                session.add(
                    SectorFlow(
                        market=MARKET,
                        sector_code=code,
                        date=d,
                        sector_name=name,
                        frgnr_net_value=frgnr,
                        orgn_net_value=orgn,
                        ind_net_value=0,
                        source="test",
                    )
                )
        await session.commit()


@pytest.fixture(autouse=True)
async def _fixture_data():
    await _clear_test_rows()
    await _seed()
    yield
    await _clear_test_rows()
    await engine.dispose()


async def _compute():
    async with async_session_factory() as session:
        return await sector_rotation.compute_sector_rotation(session, MARKET)


async def test_result_shape_and_target_date():
    result = await _compute()
    assert result["market"] == MARKET
    assert result["date"] == DATES[5].isoformat()
    assert "reason" not in result


async def test_non_industry_sector_code_excluded():
    """대형주(002_AL)는 NON_INDUSTRY_SECTOR_CODES라 값이 아무리 커도 랭킹에
    나타나면 안 된다."""
    result = await _compute()
    all_codes = {r["sector_code"] for r in result["gainers"] + result["losers"]}
    assert "002_AL" not in all_codes


async def test_zero_baseline_activity_excluded():
    """베이스라인이 전부 0인 업종(009_AL)은 0-나눗셈 가드에 걸려 결과에서
    빠진다(모듈 docstring "배율 정의" 참고 — baseline_activity<=0이면 계산
    자체를 포기)."""
    result = await _compute()
    all_codes = {r["sector_code"] for r in result["gainers"] + result["losers"]}
    assert "009_AL" not in all_codes


async def test_food_sector_is_top_gainer_with_correct_multiple():
    result = await _compute()
    assert len(result["gainers"]) >= 1
    top = result["gainers"][0]
    assert top["sector_code"] == "005_AL"
    assert top["sector_name"] == "음식료"
    assert top["today_net_value"] == FOOD_TODAY_SIGNED
    assert top["baseline_activity"] == pytest.approx(abs(FOOD_BASELINE_SIGNED))
    assert top["baseline_signed_avg"] == pytest.approx(FOOD_BASELINE_SIGNED)
    # multiple = 오늘_signed / 평소_activity = 100 / 20 = 5.0
    assert top["multiple"] == pytest.approx(5.0)
    # delta = 오늘_signed - 평소_signed_avg = 100 - 20 = 80
    assert top["delta"] == pytest.approx(80.0)


async def test_electronics_sector_is_top_loser_with_correct_multiple():
    result = await _compute()
    assert len(result["losers"]) >= 1
    top = result["losers"][0]
    assert top["sector_code"] == "013_AL"
    assert top["sector_name"] == "전기/전자"
    assert top["today_net_value"] == ELEC_TODAY_SIGNED
    assert top["baseline_activity"] == pytest.approx(abs(ELEC_BASELINE_SIGNED))
    # multiple = -500 / 100 = -5.0
    assert top["multiple"] == pytest.approx(-5.0)
    # delta = -500 - (-100) = -400
    assert top["delta"] == pytest.approx(-400.0)


async def test_aggregate_gaining_and_losing_sums():
    result = await _compute()
    agg = result["aggregate"]
    # gaining_sum은 오늘 signed > 0인 업종들의 합 = 음식료(+100)뿐(대형주/제약은
    # NON_INDUSTRY/0-베이스라인으로 이미 결과 집합에서 빠짐).
    assert agg["gaining_sum"] == FOOD_TODAY_SIGNED
    # losing_sum은 오늘 signed < 0인 업종들의 합 = 전기/전자(-500)뿐.
    assert agg["losing_sum"] == ELEC_TODAY_SIGNED
    assert agg["today_net_value"] == FOOD_TODAY_SIGNED + ELEC_TODAY_SIGNED


async def test_baseline_days_used_reflects_actual_available_history():
    """기본 baseline_days=20을 요청해도 실제로는 5일치 이력만 있으므로
    baseline_days_used=5로 정직하게 보고한다(모듈 docstring "표본 부족 시
    정직한 실패" 참고 — 침묵하지 않는다)."""
    result = await _compute()
    assert result["baseline_days_requested"] == sector_rotation.BASELINE_WINDOW_DAYS
    assert result["baseline_days_used"] == 5


async def test_insufficient_overall_history_returns_reason():
    """전체 거래일 수가 MIN_BASELINE_DAYS+1보다 적으면 계산을 포기하고 reason을
    남긴다 — 다른 market 문자열+ 별도 먼 미래 날짜로 완전히 격리한 시나리오."""
    other_market = "zzz_test_kosdaq"
    short_dates = [dt.date(2099, 8, 1) + dt.timedelta(days=i) for i in range(3)]  # 3일뿐
    async with async_session_factory() as session:
        await session.execute(
            SectorFlow.__table__.delete().where(
                SectorFlow.market == other_market, SectorFlow.date.in_(short_dates)
            )
        )
        for d in short_dates:
            session.add(
                SectorFlow(
                    market=other_market,
                    sector_code="013_AL",
                    date=d,
                    sector_name="전기/전자",
                    frgnr_net_value=10,
                    orgn_net_value=10,
                    ind_net_value=0,
                    source="test",
                )
            )
        await session.commit()

    try:
        async with async_session_factory() as session:
            result = await sector_rotation.compute_sector_rotation(session, other_market)
        assert result["date"] is None
        assert result["gainers"] == []
        assert result["losers"] == []
        assert result["aggregate"] is None
        assert "부족" in result["reason"]
    finally:
        async with async_session_factory() as session:
            await session.execute(
                SectorFlow.__table__.delete().where(
                    SectorFlow.market == other_market, SectorFlow.date.in_(short_dates)
                )
            )
            await session.commit()
