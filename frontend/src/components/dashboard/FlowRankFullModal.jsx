import { useEffect, useState } from 'react'
import { fetchFlowRank } from '../../api'
import FlowRankTable from '../FlowRankTable'
import { FLOW_RANK_LOOKBACK_DAYS } from './shared'

export default function FlowRankFullModal({ onRowClick }) {
  const [investor, setInvestor] = useState('foreign')
  const [side, setSide] = useState('buy')
  const [dates, setDates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowRank(investor, side, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setDates(body.dates || [])
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
  }, [investor, side])

  return (
    <FlowRankTable
      investor={investor}
      onInvestorChange={setInvestor}
      side={side}
      onSideChange={setSide}
      loading={loading}
      error={error}
      dates={dates}
      onRowClick={onRowClick}
    />
  )
}
