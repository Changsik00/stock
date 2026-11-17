import { useEffect, useState } from 'react'
import { fetchValueRankAuto } from './api'
import { FLOW_RANK_LOOKBACK_DAYS } from './components/dashboard/shared'

// 라이브 엔드포인트의 서버 캐시 주기(7분)와 맞춘다(DashboardPage EXTRA_LIVE_POLL_MS와 동일).
const POLL_MS = 420_000

// 거래대금 상위 표 데이터 훅 (PLAN.md Phase 5.110) — 장중엔 라이브, 그 외엔 일별
// 스냅샷. 화면이 열려 있는 동안 7분마다 다시 불러와 장중 값이 갱신되고, 장이 열리고
// 닫히는 전환도 따라간다. 로딩 표시는 첫 조회·시장 필터 변경 때만 하고, 주기 갱신은
// 표를 깜빡이지 않게 조용히 교체한다(갱신 실패 시 직전 값을 유지).
export default function useValueRankAuto(market) {
  const [state, setState] = useState({ date: null, rows: [], live: false, cachedAt: null })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    function load(initial) {
      if (initial) {
        setLoading(true)
        setError(null)
      }
      return fetchValueRankAuto(market, FLOW_RANK_LOOKBACK_DAYS)
        .then((body) => {
          if (cancelled) return
          setState({
            date: body.date,
            rows: body.rows || [],
            live: Boolean(body.live),
            cachedAt: body.cached_at || null,
          })
          setError(null)
        })
        .catch((e) => {
          if (!cancelled && initial) setError(e.message)
        })
        .finally(() => {
          if (!cancelled && initial) setLoading(false)
        })
    }

    load(true)
    const intervalId = setInterval(() => load(false), POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [market])

  return { ...state, loading, error }
}
