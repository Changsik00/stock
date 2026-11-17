import { useState } from 'react'
import useValueRankAuto from '../../useValueRankAuto'
import ValueRankTable from '../ValueRankTable'
import { VALUE_RANK_MARKET_OPTIONS } from './shared'

export default function ValueRankFullModal({ onRowClick }) {
  const [market, setMarket] = useState('all')
  const { date, rows, live, cachedAt, loading, error } = useValueRankAuto(market)

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
      <ValueRankTable rows={rows} loading={loading} error={error} date={date} live={live} cachedAt={cachedAt} onRowClick={onRowClick} />
    </div>
  )
}
