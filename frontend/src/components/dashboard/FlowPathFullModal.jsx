import { useEffect, useState } from 'react'
import { fetchFlowPath } from '../../api'
import FlowPathTable from '../FlowPathTable'
import { FLOW_RANK_LOOKBACK_DAYS } from './shared'

export default function FlowPathFullModal({ onRowClick }) {
  const [direction, setDirection] = useState('in')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 30, direction)
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
  }, [direction])

  return (
    <FlowPathTable
      loading={loading}
      error={error}
      date={date}
      rows={rows}
      direction={direction}
      onDirectionChange={setDirection}
      onRowClick={onRowClick}
    />
  )
}
