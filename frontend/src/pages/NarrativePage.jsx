import { useState } from 'react'
import NarrativeProposals from '../components/dashboard/NarrativeProposals'
import NarrativeSection from '../components/dashboard/NarrativeSection'

// 시장 내러티브 탭 (PLAN.md §5.90) — 위: 뉴스 기반 새 제안 피드(승인 대기), 아래: 활성 내러티브.
// 로컬 DB라 정적 배포에서는 App.jsx에서 탭 자체를 노출하지 않는다.
export default function NarrativePage() {
  const [reloadKey, setReloadKey] = useState(0)
  return (
    <div className="narr-page m-font">
      <NarrativeProposals onChanged={() => setReloadKey((k) => k + 1)} />
      <NarrativeSection reloadKey={reloadKey} />
    </div>
  )
}
