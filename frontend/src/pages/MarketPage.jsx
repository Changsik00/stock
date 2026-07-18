import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchBreadth,
  fetchBreadthLive,
  fetchFlowPath,
  fetchFlowRank,
  fetchGroups,
  fetchMacroSeries,
  fetchMarketSeries,
  fetchSentiment,
  fetchValueRank,
} from '../api'
import BreadthBadge from '../components/BreadthBadge'
import CandleChart from '../components/CandleChart'
import FlowChart from '../components/FlowChart'
import FlowPathTable from '../components/FlowPathTable'
import FlowRankTable from '../components/FlowRankTable'
import GroupTreemap from '../components/GroupTreemap'
import MarketFundChart from '../components/MarketFundChart'
import Modal from '../components/Modal'
import PeriodPicker from '../components/PeriodPicker'
import SentimentGauge from '../components/SentimentGauge'
import StockDetailModal from '../components/StockDetailModal'
import ValueRankTable from '../components/ValueRankTable'
import { MARKET_FUND_IDS, MARKETS } from '../constants'
import { formatDate } from '../format'

// 수급 상위 테이블 조회 기간(일) — flow_rank는 배치를 반복 실행한 날짜만 누적되고
// 소스 자체도 주말/공휴일 지연이 있어(PLAN.md §4.5, backend clients/naver_rank.py
// docstring) 1일 창으로는 "최근 거래일"을 놓칠 수 있다. 넉넉히 잡고 화면에는 항상
// 반환된 것 중 가장 최근 날짜 하나만 보여준다(FlowRankTable 참고).
const FLOW_RANK_LOOKBACK_DAYS = 7

const numFmt = new Intl.NumberFormat('ko-KR')

const GROUP_TYPE_OPTIONS = [
  { key: 'upjong', label: '업종' },
  { key: 'theme', label: '테마' },
]

const VALUE_RANK_MARKET_OPTIONS = [
  { key: 'all', label: '전체' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]

// 코스피/코스닥/선물: 지수 캔들+거래량(CandleChart, lightweight-charts) 아래에 투자자별
// 수급(FlowChart)을 이어 붙인다 (PLAN.md §5.1/§6 1-5). market_flow가 비어 있으면
// (키움 ka10051 수집 미실행/인증 불가) 수급 영역은 안내 배너로 대체하고, 화면은
// 시세만으로도 성립한다.
export default function MarketPage() {
  const [market, setMarket] = useState('kospi')
  const [days, setDays] = useState(90)
  const [prices, setPrices] = useState(null)
  const [flows, setFlows] = useState({})
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [fundSeries, setFundSeries] = useState({})
  const [fundError, setFundError] = useState(null)
  const [fundLoading, setFundLoading] = useState(false)
  const [flowRankInvestor, setFlowRankInvestor] = useState('foreign')
  const [flowRankSide, setFlowRankSide] = useState('buy')
  const [flowRankDates, setFlowRankDates] = useState([])
  const [flowRankError, setFlowRankError] = useState(null)
  const [flowRankLoading, setFlowRankLoading] = useState(false)
  const [flowPathDate, setFlowPathDate] = useState(null)
  const [flowPathRows, setFlowPathRows] = useState([])
  const [flowPathError, setFlowPathError] = useState(null)
  const [flowPathLoading, setFlowPathLoading] = useState(false)
  const [flowPathDirection, setFlowPathDirection] = useState('in')
  // 시장 종합 매수세/매도세 게이지(PLAN.md §4.6 3.6-4) — GET /api/markets/sentiment
  // 응답을 그대로 담는다({ score, approx, components }).
  const [sentiment, setSentiment] = useState(null)
  const [sentimentError, setSentimentError] = useState(null)
  const [sentimentLoading, setSentimentLoading] = useState(false)
  // 등락 종목수(breadth) 배지 — 시장 탭과 무관하게 코스피/코스닥을 함께 보여준다.
  // breadth = { kospi, kosdaq, live: bool, date: string|null } (camelCase 변환 완료 상태)
  const [breadth, setBreadth] = useState(null)
  const [breadthError, setBreadthError] = useState(null)
  const [groupType, setGroupType] = useState('upjong')
  const [groupItems, setGroupItems] = useState([])
  const [groupError, setGroupError] = useState(null)
  const [groupLoading, setGroupLoading] = useState(false)
  const [valueRankMarket, setValueRankMarket] = useState('all')
  const [valueRankDate, setValueRankDate] = useState(null)
  const [valueRankRows, setValueRankRows] = useState([])
  const [valueRankError, setValueRankError] = useState(null)
  const [valueRankLoading, setValueRankLoading] = useState(false)
  // 종목 상세 모달(PLAN.md §6 3.7-2) — DashboardPage와 달리 MarketPage는 지금까지
  // 종목 상세 모달이 없었다(수급/거래대금/ETF경유 표는 순수 데이터 표시였다). 모든
  // 랭킹 행 클릭 → 종목 상세 모달 통일(사용자 요구) 작업으로 최소 상태만 이식한다 —
  // DashboardPage.jsx의 모달은 타입이 여러 개(candle/sentiment/…)라 { type, ... } 객체를
  // 쓰지만, 여기서는 종목 상세 하나뿐이라 { code, name, market, is_etf } | null로 충분하다.
  const [stockModal, setStockModal] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMarketSeries(market, days)
      .then((body) => {
        if (!cancelled) {
          setPrices(body.prices)
          setFlows(body.flows || {})
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [market, days])

  // 투자자예탁금·신용융자·대차잔고는 시장 전체 지표라 코스피/코스닥/선물 탭과 무관하게
  // 기간(days)에만 연동한다 (PLAN.md §3.5).
  useEffect(() => {
    let cancelled = false
    setFundLoading(true)
    setFundError(null)
    fetchMacroSeries(MARKET_FUND_IDS, days)
      .then((body) => {
        if (!cancelled) setFundSeries(body.series || {})
      })
      .catch((e) => {
        if (!cancelled) setFundError(e.message)
      })
      .finally(() => {
        if (!cancelled) setFundLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days])

  // 수급 상위(flow_rank)는 시장 탭(코스피/코스닥/선물)과 무관하게 외인/기관 토글,
  // 순매수/순매도 토글에만 반응한다 — 백엔드가 이미 코스피+코스닥을 통합한 랭킹을
  // 주기 때문 (PLAN.md §4.5/§6 3.5-2b).
  useEffect(() => {
    let cancelled = false
    setFlowRankLoading(true)
    setFlowRankError(null)
    fetchFlowRank(flowRankInvestor, flowRankSide, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setFlowRankDates(body.dates || [])
      })
      .catch((e) => {
        if (!cancelled) setFlowRankError(e.message)
      })
      .finally(() => {
        if (!cancelled) setFlowRankLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [flowRankInvestor, flowRankSide])

  // ETF 경유 수급 상위(flow_path)는 시장 탭과 무관하게 유입/유출 토글에만 반응한다 —
  // 백엔드가 이미 최신 날짜 하나만 골라 상위 목록을 반환한다 (PLAN.md §4.5, 유출
  // 토글은 §4.6 3.6-4).
  useEffect(() => {
    let cancelled = false
    setFlowPathLoading(true)
    setFlowPathError(null)
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 30, flowPathDirection)
      .then((body) => {
        if (!cancelled) {
          setFlowPathDate(body.date)
          setFlowPathRows(body.rows || [])
        }
      })
      .catch((e) => {
        if (!cancelled) setFlowPathError(e.message)
      })
      .finally(() => {
        if (!cancelled) setFlowPathLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [flowPathDirection])

  // 시장 종합 매수세/매도세 게이지 — 페이지 마운트 시 한 번만 불러온다(시장 탭과
  // 무관, flow-path와 동일 패턴) (PLAN.md §4.6 3.6-4).
  useEffect(() => {
    let cancelled = false
    setSentimentLoading(true)
    setSentimentError(null)
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
      })
      .catch((e) => {
        if (!cancelled) setSentimentError(e.message)
      })
      .finally(() => {
        if (!cancelled) setSentimentLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 등락 종목수 배지 — 장중엔 live(60초 서버 캐시) 우선, 실패 시 일별 최신 확정치로
  // 폴백한다 (PLAN.md §3.5/§4.6 3.6-2). 백엔드 응답은 snake_case(limit_up/limit_down)
  // 이므로 BreadthBadge가 기대하는 camelCase로 여기서 변환한다.
  useEffect(() => {
    let cancelled = false
    const toCamel = (row) =>
      row
        ? {
            adv: row.adv,
            dec: row.dec,
            flat: row.flat,
            limitUp: row.limit_up ?? row.limitUp,
            limitDown: row.limit_down ?? row.limitDown,
          }
        : null

    async function load() {
      try {
        const body = await fetchBreadthLive()
        if (cancelled) return
        setBreadth({
          kospi: toCamel(body.kospi),
          kosdaq: toCamel(body.kosdaq),
          live: body.live !== false,
          // 정적 모드 폴백 행에는 date가 있다(일별 스냅샷) — live 응답에는 없음.
          date: body.kospi?.date || body.kosdaq?.date || null,
        })
        return
      } catch {
        // live 실패(장 마감 후 소스 장애 등) — 일별 최신 확정치로 폴백.
      }
      try {
        const [kospiBody, kosdaqBody] = await Promise.all([
          fetchBreadth('kospi', 30).catch(() => null),
          fetchBreadth('kosdaq', 30).catch(() => null),
        ])
        if (cancelled) return
        const last = (body) => {
          const series = body?.series
          return series && series.length > 0 ? series[series.length - 1] : null
        }
        const kospiRow = last(kospiBody)
        const kosdaqRow = last(kosdaqBody)
        if (!kospiRow && !kosdaqRow) {
          setBreadthError('등락 종목수 데이터를 불러오지 못했습니다.')
          return
        }
        setBreadth({
          kospi: toCamel(kospiRow),
          kosdaq: toCamel(kosdaqRow),
          live: false,
          date: kospiRow?.date || kosdaqRow?.date || null,
        })
      } catch (e) {
        if (!cancelled) setBreadthError(e.message)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  // 업종/테마 트리맵 — 토글(groupType)에만 반응한다 (PLAN.md §4.6 3.6-3).
  useEffect(() => {
    let cancelled = false
    setGroupLoading(true)
    setGroupError(null)
    fetchGroups(groupType)
      .then((items) => {
        if (!cancelled) setGroupItems(items || [])
      })
      .catch((e) => {
        if (!cancelled) setGroupError(e.message)
      })
      .finally(() => {
        if (!cancelled) setGroupLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [groupType])

  // 거래대금 상위 — 시장 필터(all/kospi/kosdaq)에만 반응한다 (PLAN.md §4.6 3.6-1).
  useEffect(() => {
    let cancelled = false
    setValueRankLoading(true)
    setValueRankError(null)
    fetchValueRank(valueRankMarket, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) {
          setValueRankDate(body.date)
          setValueRankRows(body.rows || [])
        }
      })
      .catch((e) => {
        if (!cancelled) setValueRankError(e.message)
      })
      .finally(() => {
        if (!cancelled) setValueRankLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [valueRankMarket])

  const latest = prices?.length ? prices[prices.length - 1] : null
  const hasFlows = Object.keys(flows || {}).length > 0
  const flowCapableMarket = market !== 'futures'

  // 랭킹 표(FlowRankTable/ValueRankTable/FlowPathTable)의 onRowClick(code, name) 콜백 —
  // 정적 배포(STATIC_DATA)에서는 fetchStockSeries가 스냅샷을 지원하지 않아 클릭해도
  // 항상 에러만 뜨므로(DashboardPage.jsx의 동일 판단 참고) undefined로 넘겨 행 클릭
  // 자체를 비활성화한다.
  const handleRowClick = STATIC_DATA ? undefined : (code, name) => setStockModal({ code, name })

  return (
    <div>
      <div className="tabs">
        {MARKETS.map((m) => (
          <button
            key={m.key}
            type="button"
            className={`tab ${market === m.key ? 'active' : ''}`}
            onClick={() => setMarket(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <PeriodPicker value={days} onChange={setDays} />

      {latest && (
        <div className="stat-row">
          <div className="stat">
            <span className="stat-label">기준일</span>
            <span className="stat-value">{formatDate(latest.date)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">시가</span>
            <span className="stat-value">{latest.open != null ? numFmt.format(latest.open) : '–'}</span>
          </div>
          <div className="stat">
            <span className="stat-label">고가</span>
            <span className="stat-value">{latest.high != null ? numFmt.format(latest.high) : '–'}</span>
          </div>
          <div className="stat">
            <span className="stat-label">저가</span>
            <span className="stat-value">{latest.low != null ? numFmt.format(latest.low) : '–'}</span>
          </div>
          <div className="stat">
            <span className="stat-label">종가</span>
            <span className="stat-value">{numFmt.format(latest.close)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">등락률</span>
            <span className={`stat-value ${latest.changeRate >= 0 ? 'up' : 'down'}`}>
              {latest.changeRate >= 0 ? '+' : ''}
              {latest.changeRate.toFixed(2)}%
            </span>
          </div>
        </div>
      )}

      {/* 등락 종목수 — 탭에 반응한다: 코스피/코스닥 탭이면 해당 시장 한 줄만, 선물 탭이면
          섹션 자체를 숨긴다(선물엔 등락 종목수 개념이 없음). BreadthBadge의 market prop이
          그 필터링을 맡는다 — 대시보드 모달처럼 market을 안 넘기면 기존과 동일하게
          코스피+코스닥 두 줄이 모두 나온다. */}
      {flowCapableMarket && (breadth || breadthError) && (
        <div className="breadth-panel">
          {breadth && (
            <>
              <div className="toggle-hint breadth-panel-hint">
                등락 종목수 — {breadth.live ? '장중 잠정치 (60초 캐시)' : '일별 확정치'}
              </div>
              <BreadthBadge
                kospi={breadth.kospi}
                kosdaq={breadth.kosdaq}
                date={breadth.live ? null : breadth.date}
                market={market}
              />
            </>
          )}
          {breadthError && <div className="toggle-hint">{breadthError}</div>}
        </div>
      )}

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && prices && prices.length === 0 && (
        <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
      )}
      {!loading && !error && prices && prices.length > 0 && <CandleChart data={prices} />}

      {!loading && flowCapableMarket && (
        <>
          <div className="section-title">투자자별 수급</div>
          {hasFlows ? (
            <FlowChart flows={flows} />
          ) : (
            <div className="banner">
              수급 데이터 수집 대기 — 키움 ka10051 수집(market_flow)이 아직 실행되지 않았거나
              이 배포 환경에서 키움 인증이 불가합니다(IP 등록제). 백엔드에서
              scripts.backfill_market_flow를 실행하면 코스피·코스닥 투자자별 순매수가 이
              영역에 표시됩니다.
            </div>
          )}
        </>
      )}

      {/* B. 시장 전체 구역 — 코스피/코스닥/선물 탭 선택과 무관하게 항상 코스피+코스닥
          통합치를 보여준다(위 A 구역과 달리 market state에 연동하지 않음). 사용자 피드백:
          "등락 종목수·매수세/매도세 게이지가 탭을 눌러도 안 바뀐다. 탭에 반응하는 것끼리,
          아닌 것끼리 따로 둬야 한다" — 그래서 탭 무관 지표는 이 구분 헤더 아래로 모은다. */}
      <div className="section-title">
        시장 전체 지표{' '}
        <span className="toggle-hint">(코스피+코스닥 통합 — 위 시장 탭 선택과 무관)</span>
      </div>

      <div className="breadth-panel">
        <SentimentGauge
          loading={sentimentLoading}
          error={sentimentError}
          score={sentiment?.score ?? null}
          approx={sentiment?.approx ?? true}
          components={sentiment?.components ?? null}
        />
      </div>

      <div className="section-title">업종·테마 강약</div>
      <div className="toggle-row">
        {GROUP_TYPE_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${groupType === opt.key ? 'active' : ''}`}
            onClick={() => setGroupType(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        <span className="toggle-hint">일별 스냅샷 · 색 = 등락률</span>
      </div>
      {groupLoading && <div className="state">불러오는 중…</div>}
      {groupError && <div className="state error">{groupError}</div>}
      {!groupLoading && !groupError && <GroupTreemap items={groupItems} sizeBy="value" />}

      <div className="section-title">거래대금 상위</div>
      <div className="toggle-row">
        {VALUE_RANK_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${valueRankMarket === opt.key ? 'active' : ''}`}
            onClick={() => setValueRankMarket(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <ValueRankTable
        rows={valueRankRows}
        loading={valueRankLoading}
        error={valueRankError}
        date={valueRankDate}
        onRowClick={handleRowClick}
      />

      <div className="section-title">수급 상위</div>
      <FlowRankTable
        investor={flowRankInvestor}
        onInvestorChange={setFlowRankInvestor}
        side={flowRankSide}
        onSideChange={setFlowRankSide}
        loading={flowRankLoading}
        error={flowRankError}
        dates={flowRankDates}
        onRowClick={handleRowClick}
      />

      <div className="section-title">ETF 경유 수급 상위</div>
      <FlowPathTable
        loading={flowPathLoading}
        error={flowPathError}
        date={flowPathDate}
        rows={flowPathRows}
        direction={flowPathDirection}
        onDirectionChange={setFlowPathDirection}
        onRowClick={handleRowClick}
      />

      <div className="section-title">시장 자금·대차</div>
      {fundLoading && <div className="state">불러오는 중…</div>}
      {fundError && <div className="state error">{fundError}</div>}
      {!fundLoading && !fundError && <MarketFundChart seriesMap={fundSeries} />}

      <Modal
        open={Boolean(stockModal)}
        onClose={() => setStockModal(null)}
        title={stockModal ? `${stockModal.name || stockModal.code} · 종목 상세` : undefined}
      >
        {stockModal && <StockDetailModal code={stockModal.code} initial={stockModal} />}
      </Modal>
    </div>
  )
}
