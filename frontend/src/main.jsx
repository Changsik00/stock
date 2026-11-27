import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
// Pretendard(OFL) — 가변 폰트 dynamic-subset: unicode-range로 쓰는 글리프 조각(woff2)만 내려받고 font-display: swap.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './index.css'
import App from './App.jsx'

// 데이터 캐시(Phase 5.132) — 페이지 탭을 오가도 60초 안엔 재조회하지 않고, 지나면 캐시를 먼저 보여준 뒤 뒤에서 갱신한다.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 30 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
