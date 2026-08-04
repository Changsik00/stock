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


class SectorFlow(Base):
    """업종별 투자자 순매수 (PLAN.md §5.33-1) — `collectors/market_flow.py`가
    이미 매일 호출하는 ka10051(업종별투자자순매수) 응답의 `inds_netprps` 배열 중
    "종합"(시장 전체) 행 1개만 쓰고 버려지던 개별 업종 행들을 새 외부 호출 없이
    추가로 적재한다(자세한 배경은 `collectors/market_flow.py` 모듈 docstring
    "업종별 행 추가 적재" 절 참고).

    PK는 `(market, sector_code, date)` — 같은 업종 코드라도 코스피/코스닥은
    서로 다른 코드 체계를 쓰므로(예: 코스피 "화학"=008_AL, 코스닥 "화학"=119_AL)
    market이 없으면 PK 충돌 위험이 있어 반드시 포함한다. `sector_code`는 ka10051의
    `inds_cd`(예: "013_AL")를 그대로 쓴다 — `group_snapshot.py`(네이버 소스,
    업종 79개/테마 266개, GICS 계열 세분류)와는 **완전히 다른 코드 체계·분류
    granularity**라 1:1로 매핑하지 않는다(실측 근거: 코스피 "전기/전자"
    013_AL 하나에 네이버의 "반도체와반도체장비"/"전자장비와기기"/"전자제품"/
    "디스플레이패널" 등 여러 세부 업종이 뭉쳐 있음 — 2026-07-28 실측, PLAN.md
    §5.33-1). 이 테이블은 group_snapshot의 업종·테마 트리맵과 독립적인, 키움 자체
    분류 체계로 취급한다.

    투자자 분류는 13종 전부가 아니라 로테이션 분석(quant/sector_rotation.py)에
    필요한 최소 3종(외국인/기관계/개인)만 저장한다 — 나머지 10종(금융투자/보험/
    투신/은행/연기금 등)은 이번 범위에서 쓰임새가 없어 스키마를 불필요하게
    부풀리지 않는다(필요해지면 나중에 컬럼을 추가하면 된다, 이미 저장된 행은
    그대로 두고 마이그레이션으로 컬럼만 늘리면 되므로 되돌릴 수 없는 결정이
    아니다)."""

    __tablename__ = "sector_flow"

    market: Mapped[str] = mapped_column(String(20), primary_key=True)
    sector_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    sector_name: Mapped[str | None] = mapped_column(String(50))
    frgnr_net_value: Mapped[int | None] = mapped_column(BigInteger)
    orgn_net_value: Mapped[int | None] = mapped_column(BigInteger)
    ind_net_value: Mapped[int | None] = mapped_column(BigInteger)
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
    market_value_million(시가총액/AUM, 백만 원, PLAN.md §5.38-1)은 소스
    (clients/naver_value_rank.py의 marketValueRaw)가 turnover 계산용으로 이미
    주던 값을 그대로 저장한 것 — collectors/value_rank.py가 지금까지 이 값을
    받아서 turnover만 계산하고 버리고 있었다(§5.38 설계 절 "이미 받아오고
    있었는데 저장 안 했다" 패턴). quant/flow_percentile.py가 이 컬럼을 시장별
    시총 tier 분류에 쓴다.
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
    market_value_million: Mapped[int | None] = mapped_column(BigInteger)


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


class ScalpPick(Base):
    """스켈핑 후보 추적 기록 — 관찰 로그, 매매 신호 아님 (PLAN.md §5.7).

    그날 스켈핑 후보 스코어(quant/screener.py::compute_scalp_scores) 상위
    N위(collectors/scalp_tracker.py TOP_N, 현재 10)에 "처음" 등장한 종목만
    1일 1종목 1회 기록한다(PK: date+code — 같은 날 재등장은 자기상관을
    피하려고 중복 기록하지 않는다). 이후 진입 시각 기준 고정 호라이즌
    (5/15/30/60분·당일 마감) 시점의 change_rate를 다시 조회해 채워 넣는다.
    score가 실제로 유효한지(추이와 상관관계가 있는지) 사후 검증할 근거
    자료를 쌓는 목적이며, 실제 매매를 시뮬레이션하지 않는다 — 진입/청산가,
    매매 수수료·슬리피지 등은 반영하지 않는다(§5.7 원칙).

    change_rate_5m/15m/30m/60m/eod는 NULL이면 "아직 그 호라이즌 시각이 안
    됐거나 아직 못 채움", NULL이 아니면 "채워짐"이라는 뜻으로 쓴다(값 자체가
    이론상 0%일 수 있어도 문제없음 — 그래서 별도의 "sampled_at" 컬럼을 두지
    않는다, collectors/scalp_tracker.py 참고).
    """

    __tablename__ = "scalp_pick"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str | None] = mapped_column(String(10))
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_rank: Mapped[int | None] = mapped_column(SmallInteger)
    entry_score: Mapped[float | None] = mapped_column(Numeric(8, 4))
    entry_change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    entry_turnover: Mapped[float | None] = mapped_column(Numeric(8, 4))
    in_attention_top_at_entry: Mapped[bool] = mapped_column(nullable=False, default=False)
    change_rate_5m: Mapped[float | None] = mapped_column(Numeric(8, 4))
    change_rate_15m: Mapped[float | None] = mapped_column(Numeric(8, 4))
    change_rate_30m: Mapped[float | None] = mapped_column(Numeric(8, 4))
    change_rate_60m: Mapped[float | None] = mapped_column(Numeric(8, 4))
    change_rate_eod: Mapped[float | None] = mapped_column(Numeric(8, 4))


class ShortSellingMarket(Base):
    """시장 전체(코스피/코스닥) 공매도 거래 현황 (PLAN.md §5.32) — KRX 정보데이터
    시스템 공매도 통계 포털(``clients/krx_short_selling.py`` 모듈 docstring
    "시장 전체" 절 참고, MDCSTAT30201_OUT). 대차잔고(macro_series.lending_balance,
    KOFIA, 시장 전체 합계 단일 시계열)와는 별개 지표다 — 대차잔고는 공매도의
    전조 지표(빌린 주식 잔고)일 뿐 실제 공매도 거래량/거래대금과는 다르다.

    short_volume/short_value = 공매도 거래량(주)/거래대금(백만원),
    total_volume/total_value = 시장 전체 거래량(주)/거래대금(백만원, 공매도가
    아닌 전체), volume_ratio/value_ratio = 공매도 비중(%, 소스가 직접 제공하는
    값을 그대로 저장 — 재계산하지 않음)."""

    __tablename__ = "short_selling_market"

    market: Mapped[str] = mapped_column(String(10), primary_key=True)  # kospi/kosdaq
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    short_volume: Mapped[int | None] = mapped_column(BigInteger)
    short_value: Mapped[int | None] = mapped_column(BigInteger)
    total_volume: Mapped[int | None] = mapped_column(BigInteger)
    total_value: Mapped[int | None] = mapped_column(BigInteger)
    volume_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    value_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))


class ShortSellingStock(Base):
    """종목별 공매도 거래·순보유잔고 현황 (PLAN.md §5.32) — KRX 정보데이터시스템
    공매도 통계 포털(``clients/krx_short_selling.py`` 모듈 docstring "종목별" 절
    참고, MDCSTAT30001_OUT). ``ProgramTrade``와 동일한 (code, date) PK 모양.

    volume/value = 공매도 거래량(주)/거래대금(백만원), balance_qty/balance_value =
    순보유잔고수량(주)/금액(백만원) — 순보유잔고는 보고의무 발생 종목만 T+2
    지연으로 채워지고, 그 외엔 NULL(억지로 채우지 않음, §5 "정직한 표시" 원칙)."""

    __tablename__ = "short_selling_stock"

    code: Mapped[str] = mapped_column(
        String(20), ForeignKey("stocks.code"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    value: Mapped[int | None] = mapped_column(BigInteger)
    balance_qty: Mapped[int | None] = mapped_column(BigInteger)
    balance_value: Mapped[int | None] = mapped_column(BigInteger)


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


class IntradaySample(Base):
    """장중 누적 스냅샷 영속화 (PLAN.md §5.14) — collectors/intraday_snapshot.py의
    옛 순수 메모리 버퍼(재배포마다 소실, §5.6 사고 원인)를 대체한다.

    ``series_key``는 8종 고정 문자열: 투자자별 수급 6개(``flow_kospi_개인``/
    ``flow_kospi_외국인``/``flow_kospi_기관계``/``flow_kosdaq_개인``/
    ``flow_kosdaq_외국인``/``flow_kosdaq_기관계``) + 외인선물 1개
    (``futures_외국인``) + 등락비율 1개(``breadth_ratio``). "외인 양손"의 현물
    시리즈는 별도 저장하지 않는다 — 조회 시 flow_kospi_외국인+flow_kosdaq_외국인을
    시간 매칭으로 합산한다(collectors/intraday_snapshot.get_foreign_position_series
    참고).

    PK는 ``(series_key, time)`` — 같은 series_key+time에 원본(resolution_seconds=0)과
    압축본(900)이 동시에 존재할 일은 없다는 전제(다운샘플링 배치가 원본을 압축한
    뒤 즉시 삭제하므로, collectors/intraday_compaction.py 참고). ``resolution_seconds``는
    원본 0, 15분 압축 후 900."""

    __tablename__ = "intraday_sample"

    series_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    resolution_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class VolumeProfileDaily(Base):
    """거래량 프로파일 일별 스냅샷 (PLAN.md §5.35) — §5.34
    (``quant/volume_profile.py``)의 온디맨드 계산 결과를 매일 배치로 적재해
    "POC가 어느 방향으로 이동 중인지" 같은 추이를 볼 수 있게 누적한다(§5.34는
    요청마다 다시 계산할 뿐 아무것도 남기지 않았다 — 사용자 지적: "단발성으로
    확인하지 말고 누적 정보로 추이 분석을 해야 하는데").

    ``entity_type``은 ``"index"``(지수: 코스피/코스닥/선물) 또는
    ``"stock"``(개별 종목, watchlist 대상). ``entity_code``는 지수면
    ``routers/markets.py``의 ``MARKETS``와 동일한 표기(``kospi``/``kosdaq``/
    ``futures`` — ``collectors/ohlcv.py`` 내부의 ``k200_futures``/``kospi200``
    표기가 아니다. 이 테이블은 ``services.py::get_market_series_from_db``를
    거쳐 이미 변환된 값을 그대로 쓴다), 종목이면 ``stocks.code``와 같은 값이다.

    **entity_code에 FK를 걸지 않는다** — 지수 식별자(kospi/kosdaq/futures)는
    ``stocks`` 테이블의 행이 아니기 때문(``MarketFlow.market``/
    ``SectorFlow.sector_code``가 이미 쓰는 것과 동일한, 문자열 비FK 선례).

    ``levels``는 ``quant/volume_profile.py::detect_levels()``의 반환값을
    가공 없이 그대로 JSONB로 저장한다. 종목이 watchlist에 막 추가돼 아직
    캔들이 적거나, 지수라도 초기 적재 구간이 짧으면 ``poc_price=None``/
    ``levels=[]``일 수 있다 — 이는 오류가 아니라 "아직 표본이 부족하다"는
    정직한 관찰이므로(``detect_levels``의 ``MIN_BARS_FOR_LEVELS`` 미달) 그
    상태 그대로 행을 적재한다(행 자체를 건너뛰면 나중에 추이를 조회할 때
    "그 날짜엔 계산을 안 한 것"과 "계산했더니 표본 부족이었던 것"을 구분할
    수 없다). ``bar_count``가 그 판단 근거를 남긴다.

    ``lookback_days``는 그 스냅샷 계산에 실제로 쓰인 조회 창(예: 180)을
    기록한다 — 나중에 스냅샷 간 조회 창이 달라졌을 수 있다는 맥락을 추이
    해석 시 함께 볼 수 있게 하기 위함.
    """

    __tablename__ = "volume_profile_daily"

    entity_type: Mapped[str] = mapped_column(String(10), primary_key=True)  # index | stock
    entity_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    poc_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    levels: Mapped[list | None] = mapped_column(JSONB)
    bar_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    total_volume: Mapped[float | None] = mapped_column(Numeric(24, 4))
    lookback_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class InvestorWarningEvent(Base):
    """투자주의/투자경고/투자위험종목 지정 이력 (PLAN.md §5.39) — KRX가 운영하는
    3단계 종목 단위 공식 경보 제도(과열/불공정거래 방지 목적). §5.36의
    서킷브레이커/사이드카가 "지수" 단위인 것과 달리 이건 "종목" 단위 지정이다.
    소스: KRX 공시채널 KIND(kind.krx.co.kr) — ``clients/kind_investor_warning.py``
    모듈 docstring "실측 경과" 절 참고.

    ``tier``: "caution"(투자주의)/"warning"(투자경고)/"risk"(투자위험) — 세
    tier가 서로 다른 실제 제도라 응답 스키마도 다르다. ``released_date``는
    tier="warning"/"risk"에서만 의미 있다(그 두 tier는 "지정 -> 유지 -> 해제"의
    기간을 갖는 상태, NULL이면 아직 해제 안 됨=현재 지정 중). tier="caution"은
    "그날 하루" 통보 개념이라 해제라는 개념 자체가 없어 ``released_date``가
    항상 NULL이다 — 대신 ``warning_type``(예: "종가급변")이 채워진다. 개념이
    없는 필드를 억지로 채우지 않는다(§5 "정직한 표시" 원칙, 클라이언트 모듈
    docstring과 동일).

    PK는 (tier, raw_name, designated_date) — 같은 종목이 같은 tier로 여러 번
    지정될 수 있어(지정 -> 해제 -> 재지정) code만으로는 유일하지 않고, 이
    프로젝트의 기존 관례(파일 상단 "시계열 테이블은 복합 PK" 원칙)를 그대로
    따른다. ``code``는 KIND가 주는 회사명 문자열(``raw_name``)을
    ``stocks.name``과 정확 일치시켜 수집 시점에 채운다(우선주도 이름 자체가
    구분되므로— "SK증권" vs "SK증권우" — 별도 접미사 처리 불필요,
    ``collectors/investor_warning.py`` 참고). 일치하는 종목을 못 찾으면
    NULL로 남기고 ``raw_name``은 항상 보존한다(매칭 실패를 조용히 버리지
    않음)."""

    __tablename__ = "investor_warning_event"

    tier: Mapped[str] = mapped_column(String(10), primary_key=True)  # caution/warning/risk
    raw_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    designated_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(20), ForeignKey("stocks.code"))
    market: Mapped[str | None] = mapped_column(String(10))  # KOSPI/KOSDAQ/KONEX
    warning_type: Mapped[str | None] = mapped_column(String(50))  # tier="caution" 전용
    notice_date: Mapped[dt.date | None] = mapped_column(Date)
    released_date: Mapped[dt.date | None] = mapped_column(Date)  # tier="warning"/"risk" 전용
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaperTrade(Base):
    """가상 매매 기록 — 실제 주문 없이 사용자가 직접 판단한 진입/청산을 기록해
    손익을 추적하는 순수 로컬 장부(PLAN.md §5.51).

    **이 테이블/라우터는 매매 실행이 아니다** — `KiwoomClient.place_buy_order`/
    `place_sell_order`(§5.48, kt10000/kt10001)를 어떤 코드 경로에서도 호출하지
    않는다. 사용자가 §5.50 통합 뷰(호가·ETF 괴리율·나스닥선물 등)를 보고 이미
    직접 내린 진입/청산 판단을 진입가·수량·청산가로 "기록"만 하고 손익을
    계산해 줄 뿐이다(house rule §5 — 판단/추천 문구 생성 금지, 관찰 서술만).

    대상 종목은 §5.50 `PAIR_SETS`의 6개 코드(005930/000660/0193W0/0193T0/
    0193L0/0197X0)로 한정한다 — `routers/paper_trades.py`가 요청 시점에
    검증한다(이 테이블 자체는 다른 종목코드를 막지 않지만, 라우터가 그 6개
    바깥의 code로는 insert하지 않는다).

    ``side``: "buy"(롱, 상승에 베팅) | "sell"(숏, 하락에 베팅) — 실제 주문
    방향이 아니라 사용자가 기록한 포지션 방향일 뿐이다. ``status``: "open"
    (진입만 기록됨) | "closed"(청산가까지 기록됨). id는 사람이 손으로 입력하는
    장부라 오타 정정(삭제) 대상을 가리키는 단순 식별자로만 쓰는 autoincrement
    정수 PK다 — 이 프로젝트의 다른 테이블들과 달리 시계열 복합 PK가 아니다
    (파일 상단 docstring 참고 — 이 테이블은 TimescaleDB 하이퍼테이블 대상이
    아니라 예외를 둔다)."""

    __tablename__ = "paper_trade"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # "buy"(롱) | "sell"(숏)
    entry_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    entry_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    entry_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    exit_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # "open"|"closed"
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PositioningSnapshot(Base):
    """§5.50 포지셔닝 프레임(하이닉스 중심 top-down 브리핑)의 하루 1회 스냅샷 +
    사후 결과 기록 — 사후 검증(PLAN.md §5.52) 전용 관찰 로그다.

    **배경**: "이 프레임이 실제로 유용한지 어떻게 증명하냐"는 질문에 대해, 이
    프로젝트는 AI가 하루치 데이터를 보고 매매를 임의로 "감지"해 판단하지
    않는다(house rule §5 — 판단은 사용자 몫)는 원칙과, 표본 1개로는 애초에
    아무것도 증명되지 않는다는 원칙(§5.15/§5.19/§5.34)을 그대로 지킨다. 대신
    `collectors/scalp_tracker.py`(§5.7)·`quant/regime_backtest.py`(§5.15)가 이미
    적용한 것과 동일한 사후 검증 패턴 — 매일 스냅샷을 DB에 쌓고, 나중에 실제
    결과와 짝지어 표본이 쌓이면 통계로 확인한다 — 를 §5.50 포지셔닝 프레임에도
    적용한다.

    ``date``가 자연 키인 진짜 시계열(하루 1행)이라, 파일 상단 docstring의 §5.2
    복합 PK 관례를 예외 없이 그대로 따른다 — 단일 컬럼(``date``)이 이미
    복합키의 역할을 하므로 별도로 두 컬럼을 묶을 필요가 없을 뿐이다
    (``PaperTrade``의 autoincrement 정수 PK 예외와는 다른 케이스 — 그 모델은
    "사람이 손으로 입력하는 장부"라 자연 키가 없어 예외를 둔 것이고, 이 모델은
    자연 키(date)가 있어 예외가 아니다).

    모든 값 컬럼이 nullable인 이유: 개별 소스(regime, flow-concentration, 외인
    현물/선물, SOX, 나스닥선물, 하이닉스 상대강도, 리스크 경보) 중 하나가
    실패해도 나머지는 기록되게 하기 위해서다 — `collectors/scalp_tracker.py`가
    호라이즌 컬럼을 다루는 것과 동일한 관대함(부분 실패 허용).

    ``same_day_remaining_change_rate``/``next_day_change_rate``는 스냅샷 시점엔
    당연히 알 수 없어 처음엔 NULL로 남고, `collectors/positioning_snapshot.py`가
    이후 폴링에서 2단계로 채운다(모듈 docstring 참고) — NULL은 "아직 안 채워짐"
    이지 "0%"가 아니다(`ScalpPick`의 호라이즌 컬럼과 동일한 NULL 의미 관례)."""

    __tablename__ = "positioning_snapshot"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    snapshot_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    regime: Mapped[str | None] = mapped_column(String(20))
    concentration_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    foreign_spot_cum: Mapped[float | None] = mapped_column(Numeric(14, 4))
    foreign_futures_cum: Mapped[float | None] = mapped_column(Numeric(14, 4))
    sox_change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    nasdaq_futures_change_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    hynix_price_at_snapshot: Mapped[float | None] = mapped_column(Numeric(12, 2))
    hynix_change_rate_at_snapshot: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kospi_change_rate_at_snapshot: Mapped[float | None] = mapped_column(Numeric(8, 4))
    relative_strength_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    risk_alert_count: Mapped[int | None] = mapped_column(SmallInteger)
    same_day_remaining_change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    next_day_change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))


class AutoTradeState(Base):
    """자동매매 엔진의 현재 상태 — **단일 행(id=1)만 존재하는 싱글턴**(PLAN.md
    §5.54). 이 프로젝트 최초의 완전자동매매 실행 엔진(`collectors/auto_trader.py`)이
    참조/갱신하는 유일한 상태 저장소다.

    ``enabled``가 **킬스위치**다 — 기본값 `False`(꺼짐)이고, 사용자가 전용 탭
    (`AutoTradePage.jsx`)에서 명시적으로 켜기 전까지 `collectors/auto_trader.py`는
    이 값을 함수 맨 첫 줄에서 확인해 `False`면 신호 평가조차 하지 않고 즉시
    반환한다 — 이 원칙은 마이그레이션이 이 테이블에 `enabled=False`인 시드 행을
    직접 INSERT하는 것으로도 한 번 더 보장한다(배포 직후부터 안전한 기본
    상태여야 한다).

    ``status``는 이 전략의 상태 기계 3단계: "idle"(포지션 없음, 진입 조건
    감시 중) → "holding"(진입 완료, 손절만 감시) → "trailing"(진입가 대비
    +1% 도달, 신고가 갱신하며 추세반전+플로어 도달을 감시) → (손절 또는
    trailing 청산 시) 다시 "idle". 이 전략은 한 번에 포지션을 하나만
    허용한다(단일 종목 0167A0, 단일 수량 1주) — `code`/`entry_*`/`peak_price`는
    그 하나의 포지션에 대한 값이다.

    킬스위치를 껐다 켜도(`POST /api/auto-trade/toggle`) 이 상태(특히 status/
    entry_*)는 건드리지 않는다 — 사용자가 끄고 켜는 것만으로 실제 보유 포지션
    정보가 유실되면 안 된다(PLAN.md §5.54 안전 설계)."""

    __tablename__ = "auto_trade_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)  # 킬스위치, 기본 OFF
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="idle")  # idle|holding|trailing
    code: Mapped[str] = mapped_column(String(20), nullable=False, default="0167A0")
    entry_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    entry_qty: Mapped[int | None] = mapped_column(SmallInteger)
    entry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    entry_order_no: Mapped[str | None] = mapped_column(String(30))
    peak_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutoTradeLog(Base):
    """자동매매 감사 로그(매매일지) — **append-only**, 킬스위치가 켜진 상태에서
    엔진이 내린 모든 판단/실행을 기록한다(PLAN.md §5.54, 사용자가 명시적으로
    요청한 "매매일지, 판단 기준").

    단순히 "샀다/팔았다"가 아니라 **그 순간의 신호값**(ma_cross state,
    volume_spike 값, 가격, 진입가 대비 %, 어떤 조건이 충족/미충족이었는지)을
    사람이 읽을 수 있는 문장으로 ``reason``에 남긴다 — house rule(§5)에 따라
    "이 조합이 좋다/나쁘다" 같은 새로운 판단은 만들지 않고, 신호값과 실행
    결과만 정직하게 서술한다. ``signal_snapshot``/``order_response``는 원본
    JSON을 문자열로 그대로 보존해(사람이 읽는 reason과 별개로) 나중에 필요하면
    원시 데이터를 다시 볼 수 있게 한다.

    킬스위치가 꺼져 있는 동안(매 폴링마다 "disabled, skipped")이나 idle 상태에서
    진입 조건이 미충족인 동안은 이 테이블에 아무 것도 쌓이지 않는다 — 노이즈를
    피하기 위해 `collectors/auto_trader.py`가 의도적으로 로그를 남기지 않는
    경로다(모듈 docstring 참고). ``id``는 `PaperTrade`와 동일한 이유(자연 키
    없는 append-only 기록)로 autoincrement 정수 PK를 쓴다."""

    __tablename__ = "auto_trade_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # entry|trail_activate|exit_trail|exit_stop_loss|error 등
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)  # 사람이 읽는 판단 근거(신호값 포함)
    signal_snapshot: Mapped[str | None] = mapped_column(String(2000))  # ma_cross/volume_spike 등 원본 JSON 문자열
    order_response: Mapped[str | None] = mapped_column(String(2000))  # place_buy_order/place_sell_order 원본 응답 JSON 문자열(실행이 있었을 때만)
