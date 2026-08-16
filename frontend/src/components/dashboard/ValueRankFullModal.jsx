import { useEffect, useState } from 'react'
import { fetchValueRank } from '../../api'
import ValueRankTable from '../ValueRankTable'
import { FLOW_RANK_LOOKBACK_DAYS, VALUE_RANK_MARKET_OPTIONS } from './shared'

export default function ValueRankFullModal({ onRowClick }) {
  const [market, setMarket] = useState('all')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchValueRank(market, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) {
          setDate(body.date)
          setRows(body.rows || [])
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
  }, [market])

  return (
    <div>
      <div className="toggle-row">
        {VALUE_RANK_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${market === opt.key ? 'active' : ''}`}
            onClick={() => setMarket(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <ValueRankTable rows={rows} loading={loading} error={error} date={date} onRowClick={onRowClick} />
    </div>
  )
}
