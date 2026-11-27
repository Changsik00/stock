import { useEffect } from 'react'
import { resetOnError, useAutoTradeState } from '../queries'
import { applyDevMock } from '../devMock'
import { arrowOf, dirClass, fmtPct, holdingOf } from '../tradeView'

// 대시보드 상단 한 줄 타일(Phase 5.121) — 보유 중일 때만 렌더하고 매매 탭으로 이동한다.
// 상태 조회만 하며(주문 없음), 조회 실패·대기 중이면 아무것도 그리지 않는다(죽은 요소 금지).
// 30초 폴링은 useAutoTradeState(react-query)가 맡는다. 갱신 실패 시 null → 타일 숨김(기존 동작).
// 직전 방문 때 보유 중이었는지를 기억해 둔다 — 조회가 끝나기 전에 같은 높이의 빈 자리를 잡아, 보유 중인 사용자가
// 새로고침할 때 타일이 뒤늦게 나타나며 아래 화면 전체가 밀리는 것(CLS)을 막는다. 보유 중이 아니면 자리를 잡지 않는다.
const LAST_KEY = 'stock.dashboard.holdingTile'
function readLast() {
  try {
    return JSON.parse(window.localStorage.getItem(LAST_KEY) || 'null')
  } catch {
    return null
  }
}
function writeLast(v) {
  try {
    if (v) window.localStorage.setItem(LAST_KEY, JSON.stringify(v))
    else window.localStorage.removeItem(LAST_KEY)
  } catch {
    /* 저장 불가 환경은 자리 잡기만 건너뛴다 */
  }
}

export default function HoldingTile({ onNavigate }) {
  const q = useAutoTradeState()
  const raw = resetOnError(q)
  const state = raw ? applyDevMock(raw) : null
  const holding = Boolean(onNavigate && holdingOf(state))
  const loading = q.isLoading
  useEffect(() => {
    if (!loading) writeLast(holding ? { name: state.target?.name || state.code } : null)
  }, [loading, holding, state?.target?.name, state?.code])
  if (loading) {
    const last = onNavigate ? readLast() : null
    return last ? (
      <div className="at-hold-tile" style={{ visibility: 'hidden' }} aria-hidden="true">
        <span className="at-k">보유 중</span>
        <span className="at-hold-name">{last.name}</span>
        <span className="at-hold-pnl">+0.00%</span>
        <span className="at-sub-inline">· 수동 진입</span>
        <span className="at-hold-go">매매 탭 &rsaquo;</span>
      </div>
    ) : null
  }
  if (!holding) return null
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
