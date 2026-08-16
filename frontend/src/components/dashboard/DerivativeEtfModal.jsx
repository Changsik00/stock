import { useEffect, useState } from 'react'
import { fetchDerivativeFlow } from '../../api'
import EtfDirectionCard from '../EtfDirectionCard'

// 개인 파생ETF 방향성 게이지 상세 — EtfDirectionCard(순수 프레젠테이션, PLAN.md §4.5-1)에
// derivative-flow 데이터를 붙여주는 자기완결 래퍼. 컴팩트 타일에서 클릭했을 때만
// 마운트되므로(Modal.jsx 주석 참고) 열 때마다 최신 데이터를 새로 받는다.
export default function DerivativeEtfModal() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchDerivativeFlow(90)
      .then((body) => {
        if (!cancelled) setData(body)
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
    <EtfDirectionCard
      loading={loading}
      error={error}
      universe={data?.universe}
      latest={data?.latest}
      series={data?.series ?? []}
    />
  )
}
