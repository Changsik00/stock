import { useEffect, useState } from 'react'
import { fetchSentiment } from '../../api'
import SentimentGauge from '../SentimentGauge'

export default function SentimentModal() {
  const [sentiment, setSentiment] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
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
  }, [])

  return (
    <SentimentGauge
      loading={loading}
      error={error}
      score={sentiment?.score ?? null}
      approx={sentiment?.approx ?? true}
      components={sentiment?.components ?? null}
    />
  )
}
