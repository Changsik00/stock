import { useState } from 'react'
import DashboardPage from './pages/DashboardPage'
import MacroPage from './pages/MacroPage'
import MarketPage from './pages/MarketPage'
import PlaceholderPage from './pages/PlaceholderPage'

// 대시보드가 기본 탭이다 (PLAN.md §6 3.7-1) — 핵심 숫자 요약 화면. 기존 '시장' 탭은
// 그대로 두어 상세 뷰(투자자/기간/시장 토글이 있는 전체 표·차트) 역할을 계속한다.
const PAGES = [
  { key: 'dashboard', label: '대시보드', component: DashboardPage },
  { key: 'market', label: '시장', component: MarketPage },
  { key: 'stock', label: '종목', component: null },
  { key: 'etf', label: 'ETF', component: null },
  { key: 'macro', label: '매크로', component: MacroPage },
]

export default function App() {
  const [page, setPage] = useState('dashboard')
  const current = PAGES.find((p) => p.key === page)
  const Page = current.component

  return (
    <>
      <header>
        <h1>수급 분석 대시보드</h1>
        <p className="subtitle">
          코스피 · 코스닥 · 선물 시세와 투자자별 수급, 환율 · 유가 매크로 지표를 한 화면에서
          확인합니다.
        </p>
      </header>

      <div className="page-tabs">
        {PAGES.map((p) => (
          <button
            key={p.key}
            type="button"
            className={`page-tab ${page === p.key ? 'active' : ''}`}
            onClick={() => setPage(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {Page ? <Page /> : <PlaceholderPage label={current.label} />}
    </>
  )
}
