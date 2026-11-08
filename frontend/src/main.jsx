import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Pretendard(OFL) — 가변 폰트 dynamic-subset: unicode-range로 쓰는 글리프 조각(woff2)만 내려받고 font-display: swap.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
