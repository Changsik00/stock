"""ORM models for the 10 tables in PLAN.md §5.2.

Conventions (per PLAN.md §5.2):
- money/value columns are BIGINT (단위: 백만 원, unless the column is a raw
  market rate like macro_series.value which needs decimal precision)
- dates are DATE
- time-series tables use composite primary keys so they can later become
  TimescaleDB hypertables (`create_hypertable(table, 'date')`) without a
  schema change
"""

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Stock(Base):
    """종목 마스터."""

    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(10), nullable=False)  # KOSPI/KOSDAQ
    is_etf: Mapped[bool] = mapped_column(nullable=False, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IndexOhlcv(Base):
    """지수 일봉 (market: kospi/kosdaq/k200_futures/kospi200 — kospi200은 KOSPI200
    현물지수, PLAN.md §4.5-3 베이시스 계산용)."""

    __tablename__ = "index_ohlcv"

    market: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    # 네이버 fchart(siseJson) raw 정수값을 변환 없이 저장(collectors/ohlcv.py 참고).
    # yfinance 폴백 값도 정상 구간에서는 같은 스케일임을 실측 확인했다 — 소스가
    # 섞여도 단위가 어긋나지 않는다. (2026-07-17: 코스닥 volume이 yfinance
    # 1차였을 때 대부분 기간 800~1,300 수준의 잘못된 스케일로 적재돼 있었던 문제를
    # 네이버 1차 전환 + 전체 재백필로 해결.)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    value: Mapped[int | None] = mapped_column(BigInteger)


class StockOhlcv(Base):
    """종목 일봉."""

    __tablename__ = "stock_ohlcv"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[int | None] = mapped_column(BigInteger)
    high: Mapped[int | None] = mapped_column(BigInteger)
    low: Mapped[int | None] = mapped_column(BigInteger)
    close: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    value: Mapped[int | None] = mapped_column(BigInteger)


class MarketFlow(Base):
    """시장별 투자자 순매수 (KIS 3분류 + pykrx/kiwoom 13분류 겸용, source 컬럼으로 구분)."""

    __tablename__ = "market_flow"

    market: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    investor: Mapped[str] = mapped_column(String(30), primary_key=True)
    net_value: Mapped[int | None] = mapped_column(BigInteger)
    net_volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str | None] = mapped_column(String(20))


class StockFlow(Base):
    """종목별 투자자 수급 (키움 ka10059)."""

    __tablename__ = "stock_flow"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    investor: Mapped[str] = mapped_column(String(30), primary_key=True)
    net_value: Mapped[int | None] = mapped_column(BigInteger)
    net_volume: Mapped[int | None] = mapped_column(BigInteger)


class ProgramTrade(Base):
    """프로그램 매매 (키움 ka90013)."""

    __tablename__ = "program_trade"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    arb_net: Mapped[int | None] = mapped_column(BigInteger)
    non_arb_net: Mapped[int | None] = mapped_column(BigInteger)
    total_net: Mapped[int | None] = mapped_column(BigInteger)


class MacroSeries(Base):
    """환율/유가 등 매크로 시계열 (series: usdkrw/wti/brent, source: ecos/yfinance/fred)."""

    __tablename__ = "macro_series"

    series: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    source: Mapped[str | None] = mapped_column(String(20))


class WhaleScore(Base):
    """주포 매집/분산 스코어 (재계산 가능한 캐시)."""

    __tablename__ = "whale_score"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    score: Mapped[int | None] = mapped_column(SmallInteger)
    signals: Mapped[dict | None] = mapped_column(JSONB)


class Watchlist(Base):
    """일별 수집 대상 종목."""

    __tablename__ = "watchlist"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EtfHolding(Base):
    """ETF 구성종목 일별 스냅샷 (PLAN.md §4.5).

    stock_code/etf_code는 stocks 마스터가 아직 완전하지 않아(Phase 2-2 전)
    FK를 걸지 않는다. weight는 % (예: 8.1234 = 8.12%).
    """

    __tablename__ = "etf_holdings"

    etf_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    weight: Mapped[float | None] = mapped_column(Numeric(8, 4))
    shares: Mapped[int | None] = mapped_column(BigInteger)


class EtfStat(Base):
    """ETF 일별 통계 — NAV/AUM/순유입 (PLAN.md §4.5). 금액 단위 백만 원."""

    __tablename__ = "etf_stats"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    nav: Mapped[float | None] = mapped_column(Numeric(18, 4))
    aum: Mapped[int | None] = mapped_column(BigInteger)
    net_inflow: Mapped[int | None] = mapped_column(BigInteger)


class FlowRank(Base):
    """투자자별 순매수/순매도 상위 종목 일별 스냅샷 (PLAN.md §4.5/§6 3.5-2b).

    net_value 단위 백만 원, quantity 단위 천주 — **둘 다 항상 양수(크기)로
    저장**한다. 매도 방향은 음수 부호가 아니라 ``side='sell'``로 표현한다(부호와
    방향 컬럼을 동시에 쓰면 "어느 쪽이 진실인지" 헷갈리는 걸 막기 위한 설계
    결정 — collectors/flow_rank.py에서 소스가 sell에 대해 반환하는 음수 값을
    abs()로 정규화해서 넣는다).

    turnover(회전율, %) = 그 종목의 당일 거래대금 ÷ 시가총액 × 100 — 랭킹에 오른
    투자자의 순매수/순매도 크기가 아니라 **종목 전체의 손바뀜 정도**를 나타내는
    부가 지표다(정렬/판단 기준은 여전히 net_value). 수집 시점에 계산해 저장한다
    (과거 시점 재현 가능 — API 조회 시점 재계산은 과거 스냅샷을 못 만든다).
    """

    __tablename__ = "flow_rank"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    investor: Mapped[str] = mapped_column(String(30), primary_key=True)
    side: Mapped[str] = mapped_column(String(4), primary_key=True, default="buy")
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    net_value: Mapped[int | None] = mapped_column(BigInteger)
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    turnover: Mapped[float | None] = mapped_column(Numeric(8, 4))
    is_etf: Mapped[bool] = mapped_column(nullable=False, default=False)
    # 어느 시장 랭킹 페이지에서 왔는지 (kospi/kosdaq). 2026-07-18 이전 적재분은 수집
    # 시 시장 구분을 버리고 병합했어서 NULL (§4.6 3.6-1).
    market: Mapped[str | None] = mapped_column(String(10))


class ValueRank(Base):
    """거래대금 상위 종목 일별 스냅샷 (PLAN.md §4.6 3.6-1) — 돈이 모이는 곳.

    value(거래대금)는 백만 원, change_rate는 % (등락률 — 돈이 몰린 종목이
    올랐는지/내렸는지), turnover는 회전율 %(flow_rank와 동일 정의, 선택 적재).
    """

    __tablename__ = "value_rank"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    market: Mapped[str] = mapped_column(String(10), primary_key=True)  # kospi/kosdaq
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[int | None] = mapped_column(BigInteger)
    change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    is_etf: Mapped[bool] = mapped_column(nullable=False, default=False)
    turnover: Mapped[float | None] = mapped_column(Numeric(8, 4))


class GroupSnapshot(Base):
    """업종/테마별 일별 스냅샷 (PLAN.md §4.6 3.6-3 트리맵).

    group_type: 'upjong'(업종) / 'theme'(테마). change_rate %, value(거래대금)·
    market_sum(시가총액)은 백만 원 (소스가 안 주는 값은 NULL).
    """

    __tablename__ = "group_snapshot"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    group_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    value: Mapped[int | None] = mapped_column(BigInteger)
    market_sum: Mapped[int | None] = mapped_column(BigInteger)


class FlowPath(Base):
    """종목별 수급 경로 분해 캐시 — 직접 vs ETF 경유 (PLAN.md §4.5). 단위 백만 원."""

    __tablename__ = "flow_path"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    direct_net: Mapped[int | None] = mapped_column(BigInteger)
    via_etf_net: Mapped[int | None] = mapped_column(BigInteger)
    top_etfs: Mapped[dict | None] = mapped_column(JSONB)


class MarketBreadth(Base):
    """시장별 등락 종목수 일별 확정치 (PLAN.md §3.5/§4.6 3.6-2).

    adv(상승)/dec(하락)/flat(보합)/limit_up(상한)/limit_down(하한) — 전부 종목수(개).
    1차 소스는 네이버 시장 요약(finance.naver.com/sise/sise_index.naver, 실호출
    확정 2026-07-18 — clients/naver_breadth.py 참고). 키움 ka20001 앱키 확보 후
    정밀 소스로 교체 예정(1.5-3). 장중 값은 이 테이블에 쌓지 않는다 — 장마감 후
    확정치만 upsert하고, 장중 조회는 /api/markets/breadth/live의 온디맨드 캐시로
    처리한다(§3.5 원칙).
    """

    __tablename__ = "market_breadth"

    market: Mapped[str] = mapped_column(String(10), primary_key=True)  # kospi/kosdaq
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    adv: Mapped[int | None] = mapped_column(SmallInteger)
    dec: Mapped[int | None] = mapped_column(SmallInteger)
    flat: Mapped[int | None] = mapped_column(SmallInteger)
    limit_up: Mapped[int | None] = mapped_column(SmallInteger)
    limit_down: Mapped[int | None] = mapped_column(SmallInteger)


class CollectLog(Base):
    """배치 수집 로그 (모니터링·중복 방지)."""

    __tablename__ = "collect_log"

    job: Mapped[str] = mapped_column(String(50), primary_key=True)
    target_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok/fail
    rows: Mapped[int | None] = mapped_column(BigInteger)
    message: Mapped[str | None] = mapped_column(String(500))
    ran_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
