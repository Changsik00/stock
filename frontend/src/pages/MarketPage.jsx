import { useEffect, useState } from 'react'
import { fetchFlowPath, fetchFlowRank, fetchMacroSeries, fetchMarketSeries } from '../api'
import CandleChart from '../components/CandleChart'
import FlowChart from '../components/FlowChart'
import FlowPathTable from '../components/FlowPathTable'
import FlowRankTable from '../components/FlowRankTable'
import MarketFundChart from '../components/MarketFundChart'
import PeriodPicker from '../components/PeriodPicker'
import { MARKET_FUND_IDS, MARKETS } from '../constants'

// 수급 상위 테이블 조회 기간(일) — flow_rank는 배치를 반복 실행한 날짜만 누적되고
// 소스 자체도 주말/공휴일 지연이 있어(PLAN.md §4.5, backend clients/naver_rank.py
// docstring) 1일 창으로는 "최근 거래일"을 놓칠 수 있다. 넉넉히 잡고 화면에는 항상
// 반환된 것 중 가장 최근 날짜 하나만 보여준다(FlowRankTable 참고).
const FLOW_RANK_LOOKBACK_DAYS = 7

const numFmt = new Intl.NumberFormat('ko-KR')

// 코스피/코스닥/선물: 지수 캔들+거래량(CandleChart, lightweight-charts) 아래에 투자자별
// 수급(FlowChart)을 이어 붙인다 (PLAN.md §5.1/§6 1-5). market_flow가 비어 있으면
// (KRX 로그인 미설정) 수급 영역은 안내 배너로 대체하고, 화면은 시세만으로도 성립한다.
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

  // ETF 경유 수급 상위(flow_path)도 시장 탭과 무관하게 페이지 마운트 시 한 번만
  // 불러온다 — 백엔드가 이미 최신 날짜 하나만 골라 상위 목록을 반환한다 (PLAN.md §4.5).
  useEffect(() => {
    let cancelled = false
    setFlowPathLoading(true)
    setFlowPathError(null)
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 30)
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
  }, [])

  const latest = prices?.length ? prices[prices.length - 1] : null
  const hasFlows = Object.keys(flows || {}).length > 0
  const flowCapableMarket = market !== 'futures'

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
            <span className="stat-value">{latest.date}</span>
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
              수급 데이터 수집 대기 (KRX 로그인 설정 필요) — data.krx.co.kr 무료 회원가입 후
              .env의 KRX_ID/KRX_PW를 설정하면 코스피·코스닥 투자자별 순매수가 이 영역에
              표시됩니다.
            </div>
          )}
        </>
      )}

      <div className="section-title">수급 상위</div>
      <FlowRankTable
        investor={flowRankInvestor}
        onInvestorChange={setFlowRankInvestor}
        side={flowRankSide}
        onSideChange={setFlowRankSide}
        loading={flowRankLoading}
        error={flowRankError}
        dates={flowRankDates}
      />

      <div className="section-title">ETF 경유 수급 상위</div>
      <FlowPathTable
        loading={flowPathLoading}
        error={flowPathError}
        date={flowPathDate}
        rows={flowPathRows}
      />

      <div className="section-title">시장 자금·대차</div>
      {fundLoading && <div className="state">불러오는 중…</div>}
      {fundError && <div className="state error">{fundError}</div>}
      {!fundLoading && !fundError && <MarketFundChart seriesMap={fundSeries} />}
    </div>
  )
}
