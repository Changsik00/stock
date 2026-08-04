import { useState } from 'react'
import { STATIC_DATA } from './api'
import AutoTradePage from './pages/AutoTradePage'
import DashboardPage from './pages/DashboardPage'
import MarketPage from './pages/MarketPage'
import PlaceholderPage from './pages/PlaceholderPage'

// 대시보드가 기본 탭이다 (PLAN.md §6 3.7-1) — 핵심 숫자 요약 화면. 기존 '시장' 탭은
// 그대로 두어 상세 뷰(투자자/기간/시장 토글이 있는 전체 표·차트) 역할을 계속한다.
// '매크로' 탭은 제거했다 — 환율·유가 2~3개 차트만으로 탭 하나는 과했다는 판단에 따라
// 대시보드 "시황·자금" 줄의 타일(환율/WTI) + 모달(MacroModal, DashboardPage.jsx)로
// 편입했다. 차트 렌더 로직은 components/MacroChart.jsx로 뽑아 모달에서 재사용한다.
//
// '자동매매' 탭(PLAN.md §5.54, 2026-08-04 추가) — 완전자동매매 실행 엔진 전용 탭.
// 사용자 지적("완전자동매매인데 히스토리 관리해야, 탭 하나 따로 만들어야, 대시보드에서
// 관리하면 안 된다")에 따라 대시보드와 분리했다. **STATIC_DATA(GitHub Pages 정적
// 배포) 빌드에서는 이 탭 자체를 노출하지 않는다** — 실제 증권 계좌에 연결된 기능을
// 공개 데모에 노출할 이유가 없다(다른 탭들의 "로컬 전용 기능 숨김" 관례보다 한 단계
// 더 엄격하게, 탭 진입점 자체를 제거).
const PAGES = [
  { key: 'dashboard', label: '대시보드', component: DashboardPage },
  { key: 'market', label: '시장', component: MarketPage },
  { key: 'stock', label: '종목', component: null },
  { key: 'etf', label: 'ETF', component: null },
  ...(STATIC_DATA ? [] : [{ key: 'auto-trade', label: '자동매매', component: AutoTradePage }]),
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
