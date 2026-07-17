import { useEffect, useState } from 'react'
import { fetchMacroSeries, fetchMarketSeries } from '../api'
import CandleChart from '../components/CandleChart'
import FlowChart from '../components/FlowChart'
import MarketFundChart from '../components/MarketFundChart'
import PeriodPicker from '../components/PeriodPicker'
import { MARKET_FUND_IDS, MARKETS } from '../constants'

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

      <div className="section-title">시장 자금·대차</div>
      {fundLoading && <div className="state">불러오는 중…</div>}
      {fundError && <div className="state error">{fundError}</div>}
      {!fundLoading && !fundError && <MarketFundChart seriesMap={fundSeries} />}
    </div>
  )
}
