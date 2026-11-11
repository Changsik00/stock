import { useState } from 'react'
import NarrativeSection from '../components/dashboard/NarrativeSection'
import NewsIssues from '../components/dashboard/NewsIssues'
import RecentNews from '../components/dashboard/RecentNews'

// 시장 내러티브 탭 (PLAN.md §5.90, 5.106) — 위: 오늘의 이슈(뉴스를 사건 단위로 묶고 실제 수급과 대조),
// 중간: 추적 중인 내러티브, 아래: 전체 수집 뉴스(접이식).
// 로컬 DB라 정적 배포에서는 App.jsx에서 탭 자체를 노출하지 않는다.
export default function NarrativePage() {
  const [reloadKey, setReloadKey] = useState(0)
  return (
    <div className="narr-page m-font">
      <NewsIssues onTracked={() => setReloadKey((k) => k + 1)} />
      <NarrativeSection reloadKey={reloadKey} />
      <RecentNews />
    </div>
  )
}
