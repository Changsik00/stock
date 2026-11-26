import { useEffect, useState } from 'react'
import { fetchAutoTradeState } from '../api'
import { applyDevMock } from '../devMock'
import { arrowOf, dirClass, fmtPct, holdingOf } from '../tradeView'

// 대시보드 상단 한 줄 타일(Phase 5.121) — 보유 중일 때만 렌더하고 매매 탭으로 이동한다.
// 상태 조회만 하며(주문 없음), 조회 실패·대기 중이면 아무것도 그리지 않는다(죽은 요소 금지).
export default function HoldingTile({ onNavigate }) {
  const [state, setState] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () =>
      fetchAutoTradeState()
        .then((s) => alive && setState(applyDevMock(s)))
        .catch(() => alive && setState(null))
    load()
    const t = setInterval(load, 30000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])
  if (!onNavigate || !holdingOf(state)) return null
  const pct = state.unrealized_pnl_pct
  const source = state.entry_source === 'manual' ? '수동 진입' : '자동 진입'
  return (
    <button type="button" className="at-hold-tile" onClick={() => onNavigate('auto-trade')} aria-label="매매 탭으로 이동">
      <span className="at-k">보유 중</span>
      <span className="at-hold-name">{state.target?.name || state.code}</span>
      <span className={`at-hold-pnl ${dirClass(pct)}`}>
        {pct == null ? '-' : `${arrowOf(pct) ? `${arrowOf(pct)} ` : ''}${fmtPct(pct)}`}
      </span>
      <span className="at-sub-inline">· {source}</span>
      <span className="at-hold-go" aria-hidden="true">매매 탭 &rsaquo;</span>
    </button>
  )
}
