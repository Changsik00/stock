import { useEffect, useState } from 'react'
import { STATIC_DATA, fetchFlowAnomaly, fetchPremarketOutlook, fetchRegime, fetchVolRegime } from '../api'
import PremarketOutlookCard from './dashboard/PremarketOutlookCard'
import VolRegimeCard from './dashboard/VolRegimeCard'
import RegimeCard from './dashboard/RegimeCard'
import FlowAnomalyCard from './dashboard/FlowAnomalyCard'

// 시장 탭 상단 — 지금 유입 우세(§5.15) + 프리마켓 예상 시작점(PLAN.md §5.107) + 오늘 변동성 레짐(§5.95).
// Phase 5.128: 대시보드에서 옮겨 왔다(대시보드엔 핵심만 두기로 한 사용자 결정). 코스피·코스닥을 함께 보는
// 카드라 시장(코스피/코스닥/선물) 탭 선택과 무관하게 탭 위에 둔다. 데이터 수명은 이 컴포넌트가 직접 관리하고
// 대시보드와 같은 60초 주기로 갱신한다. 표시 전용 — 정적 배포에서는 숨김.
// Phase 5.129: 이상 거래 카드(§5.117)도 대시보드에서 옮겨 와 맨 아래에 둔다(종목 클릭 → onStockClick).
const POLL_MS = 60000

export default function MarketOverviewCards({ onKosdaq, onStockClick }) {
  // null=로딩, {error:true}=첫 로드 실패(프리마켓), 이후 실패는 마지막 값 유지 — 대시보드 시절 관례 그대로.
  const [premarket, setPremarket] = useState(null)
  const [volRegime, setVolRegime] = useState(null)
  const [regime, setRegime] = useState(null)
  const [flowAnomaly, setFlowAnomaly] = useState(null)

  useEffect(() => {
    if (STATIC_DATA) return undefined
    let cancelled = false
    const load = () => {
      fetchPremarketOutlook()
        .then((body) => !cancelled && setPremarket(body))
        .catch(() => !cancelled && setPremarket((prev) => prev ?? { error: true }))
      fetchVolRegime()
        .then((body) => !cancelled && setVolRegime(body))
        .catch(() => {}) // 실패 시 마지막 값 유지
      fetchRegime()
        .then((body) => !cancelled && setRegime(body))
        .catch(() => {}) // 실패 시 마지막 값 유지
      fetchFlowAnomaly()
        .then((body) => !cancelled && setFlowAnomaly(body))
        .catch(() => {}) // 실패 시 마지막 값 유지
    }
    load()
    const t = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  if (STATIC_DATA) return null
  return (
    <div className="m-font market-overview">
      <RegimeCard regime={regime} onKosdaq={onKosdaq} />
      <PremarketOutlookCard data={premarket} />
      <VolRegimeCard data={volRegime} />
      <FlowAnomalyCard
        data={flowAnomaly}
        loading={flowAnomaly === null}
        onRowClick={onStockClick ? (row) => onStockClick(row.code, row.name, row.market) : undefined}
      />
    </div>
  )
}
