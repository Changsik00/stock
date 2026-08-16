import { useEffect, useState } from 'react'
import { fetchMacroSeries } from '../../api'
import MarketFundChart from '../MarketFundChart'
import PeriodPicker from '../PeriodPicker'
import { MARKET_FUND_IDS } from '../../constants'

// 자금(예탁금/대차잔고/신용융자) 모달 차트 기본 기간 — 추세를 보려면 90일보다 넉넉해야
// 자연스럽다.
const DEFAULT_FUND_DAYS = 180

export default function FundModal() {
  const [days, setDays] = useState(DEFAULT_FUND_DAYS)
  const [seriesMap, setSeriesMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(MARKET_FUND_IDS, days)
      .then((body) => {
        if (!cancelled) setSeriesMap(body.series || {})
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && <MarketFundChart seriesMap={seriesMap} />}
    </div>
  )
}
