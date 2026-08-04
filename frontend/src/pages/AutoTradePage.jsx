import { useEffect, useState } from 'react'
import { fetchAutoTradeLog, fetchAutoTradeState, toggleAutoTrade } from '../api'

// 대상 종목(PLAN.md §5.54, 사용자 확정 — quant/auto_trade_rules.py의
// TARGET_CODE와 동일한 값). 이 페이지의 "전략 규칙 요약" 절에서만 쓰는 표시용
// 상수라 앱 전체 공유 constants.js에는 넣지 않는다.
const TARGET_CODE = '0167A0'
const TARGET_NAME = 'SOL AI반도체TOP2플러스'

const numFmt = new Intl.NumberFormat('ko-KR')

function rateClass(rate) {
  if (rate === null || rate === undefined) return ''
  return rate > 0 ? 'up' : rate < 0 ? 'down' : ''
}

function rateLabel(rate) {
  if (rate === null || rate === undefined) return '-'
  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

function priceLabel(v) {
  return v === null || v === undefined ? '-' : `${numFmt.format(v)}원`
}

// 백엔드가 주는 entry_at/ts는 ISO8601(UTC) — 로컬 시각 "MM/DD HH:MM:SS"로 짧게 표기.
function formatDateTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

const STATUS_LABEL = { idle: '대기(idle)', holding: '보유중(holding)', trailing: '트레일링(trailing)' }

const EVENT_TYPE_LABEL = {
  entry: '진입',
  trail_activate: '트레일 전환',
  exit_trail: '트레일 청산',
  exit_stop_loss: '손절',
  error: '오류/차단',
}

// 이 페이지가 보여주는 값들은 전부 PLAN.md §5.54에서 사용자가 직접 확정한 규칙
// 그대로다 — "이 조합이 좋다"는 판단 문구가 아니라 지금 설정된 규칙 자체를
// 고정 텍스트로 서술한다(house rule §5).
const STRATEGY_RULES = [
  { label: '대상 종목', value: `${TARGET_NAME} (${TARGET_CODE}) 고정 · 1주` },
  { label: '진입 조건', value: 'ma_cross.state == "golden" AND volume_spike.is_spike == true' },
  {
    label: '청산(트레일링 스탑)',
    value: '진입가 대비 +1% 도달 시 트레일 모드 전환 → 이후 ma_cross.state == "dead" AND 진입가 대비 +0.5% 이하 도달 시 매도',
  },
  { label: '손절', value: '상태 무관, 진입가 대비 -1.5% 이하 도달 시 즉시 매도' },
]

function KillSwitch({ state, onToggle, busy }) {
  const enabled = !!state?.enabled
  return (
    <section
      style={{
        border: `2px solid ${enabled ? 'var(--down, #d9364a)' : 'var(--border, #333)'}`,
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
        background: enabled ? 'color-mix(in srgb, var(--down, #d9364a) 8%, transparent)' : 'transparent',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 4 }}>킬스위치</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: enabled ? 'var(--down, #d9364a)' : 'var(--text-muted)' }}>
            {enabled ? '켜짐 (ON)' : '꺼짐 (OFF)'}
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => onToggle(!enabled)}
          style={{
            padding: '12px 28px',
            fontSize: 16,
            fontWeight: 700,
            borderRadius: 999,
            border: 'none',
            cursor: busy ? 'default' : 'pointer',
            color: '#fff',
            background: enabled ? 'var(--down, #d9364a)' : 'var(--up, #1a7f37)',
            opacity: busy ? 0.6 : 1,
          }}
        >
          {enabled ? '지금 끄기' : '지금 켜기'}
        </button>
      </div>
      {enabled ? (
        <p style={{ marginTop: 12, marginBottom: 0, fontWeight: 600, color: 'var(--down, #d9364a)' }}>
          ⚠ 실제 주문이 자동으로 나갑니다 — 이 계좌는 실전 계좌(모의 아님)이며, 진입/청산 조건이
          충족되면 사람의 추가 확인 없이 즉시 매수/매도 주문이 제출됩니다.
        </p>
      ) : (
        <p style={{ marginTop: 12, marginBottom: 0, color: 'var(--text-muted)' }}>
          꺼져 있는 동안은 신호 평가 자체를 하지 않습니다 — 어떤 조건이 충족돼도 주문이 나가지
          않습니다.
        </p>
      )}
    </section>
  )
}

function StatusPanel({ state }) {
  const status = state?.status || 'idle'
  const holding = status === 'holding' || status === 'trailing'
  return (
    <section style={{ border: '1px solid var(--border, #333)', borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 8 }}>현재 상태</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginBottom: holding ? 12 : 0 }}>
        {STATUS_LABEL[status] || status}
      </div>
      {holding && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
          <div>
            <div className="stat-label">진입가</div>
            <div className="stat-value">{priceLabel(state.entry_price)}</div>
          </div>
          <div>
            <div className="stat-label">진입시각</div>
            <div className="stat-value" style={{ fontSize: 14 }}>
              {formatDateTime(state.entry_at)}
            </div>
          </div>
          <div>
            <div className="stat-label">현재가</div>
            <div className="stat-value">{priceLabel(state.current_price)}</div>
          </div>
          <div>
            <div className="stat-label">평가손익</div>
            <div className={`stat-value ${rateClass(state.unrealized_pnl_pct)}`}>
              {state.unrealized_pnl === null || state.unrealized_pnl === undefined
                ? '-'
                : `${numFmt.format(state.unrealized_pnl)}원 (${rateLabel(state.unrealized_pnl_pct)})`}
            </div>
          </div>
          {status === 'trailing' && (
            <div>
              <div className="stat-label">신고가(peak)</div>
              <div className="stat-value">{priceLabel(state.peak_price)}</div>
            </div>
          )}
          <div>
            <div className="stat-label">주문번호</div>
            <div className="stat-value" style={{ fontSize: 14 }}>
              {state.entry_order_no || '-'}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function StrategyPanel() {
  return (
    <section style={{ border: '1px solid var(--border, #333)', borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 8 }}>
        전략 규칙 요약 (사용자가 직접 확정한 값 — 아래는 지금 설정된 규칙 그대로이며, 매매 판단
        문구가 아닙니다)
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <tbody>
          {STRATEGY_RULES.map((r) => (
            <tr key={r.label}>
              <td
                style={{
                  padding: '6px 8px',
                  whiteSpace: 'nowrap',
                  color: 'var(--text-muted)',
                  verticalAlign: 'top',
                  borderBottom: '1px solid var(--border, #333)',
                }}
              >
                {r.label}
              </td>
              <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border, #333)' }}>{r.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function LogPanel({ rows, loading, error }) {
  return (
    <section style={{ border: '1px solid var(--border, #333)', borderRadius: 12, padding: 16 }}>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 8 }}>
        매매일지 (감사 로그, 최신이 위) — 실행뿐 아니라 판단 근거(신호값)까지 그대로 기록합니다.
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state">불러오기 실패: {error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">아직 기록이 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                {['시각', '이벤트', '가격', '판단 근거'].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      padding: '4px 8px',
                      borderBottom: '1px solid var(--border, #333)',
                      whiteSpace: 'nowrap',
                      fontWeight: 600,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border, #333)', whiteSpace: 'nowrap' }}>
                    {formatDateTime(r.ts)}
                  </td>
                  <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border, #333)', whiteSpace: 'nowrap' }}>
                    <span className={`badge ${r.event_type === 'error' ? 'badge-warn' : 'badge-info'}`}>
                      {EVENT_TYPE_LABEL[r.event_type] || r.event_type}
                    </span>
                  </td>
                  <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border, #333)', whiteSpace: 'nowrap' }}>
                    {priceLabel(r.price)}
                  </td>
                  <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border, #333)' }}>{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// 자동매매 전용 탭(PLAN.md §5.54) — 대시보드와 분리된 이유: 사용자 지적("완전자동매매인데
// 히스토리 관리해야, 탭 하나 따로 만들어야, 대시보드에서 관리하면 안 된다"). 킬스위치는
// 기본 꺼짐(서버 마이그레이션이 enabled=false로 시드)이며, 이 페이지는 그 상태를
// 조회/전환/열람만 한다 — 판단 로직은 전혀 없다(백엔드 collectors/auto_trader.py가 전담).
export default function AutoTradePage() {
  const [state, setState] = useState(null)
  const [stateError, setStateError] = useState(null)
  const [toggleBusy, setToggleBusy] = useState(false)
  const [toggleError, setToggleError] = useState(null)

  const [logRows, setLogRows] = useState([])
  const [logLoading, setLogLoading] = useState(true)
  const [logError, setLogError] = useState(null)

  const loadState = () => {
    setStateError(null)
    return fetchAutoTradeState()
      .then(setState)
      .catch((e) => setStateError(e.message))
  }

  const loadLog = () => {
    setLogLoading(true)
    setLogError(null)
    return fetchAutoTradeLog(200)
      .then((body) => setLogRows(body.rows || []))
      .catch((e) => setLogError(e.message))
      .finally(() => setLogLoading(false))
  }

  useEffect(() => {
    loadState()
    loadLog()
    // 실제 주문이 자동으로 나갈 수 있는 화면이라 상태/로그를 열어 둔 동안
    // 15초마다 새로고침한다 — 사용자가 진입/청산이 실제로 언제 일어났는지
    // 화면을 계속 보고 있을 수 있어야 한다(다른 탭들의 60초 폴링보다 짧게 잡음).
    const timer = setInterval(() => {
      loadState()
      loadLog()
    }, 15000)
    return () => clearInterval(timer)
  }, [])

  const handleToggle = async (nextEnabled) => {
    setToggleError(null)
    setToggleBusy(true)
    try {
      await toggleAutoTrade(nextEnabled)
      await loadState()
    } catch (err) {
      setToggleError(err.message)
    } finally {
      setToggleBusy(false)
    }
  }

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 16 }}>
        이 탭은 이 프로젝트 최초의 완전자동매매 실행 엔진입니다 — 실제 증권 계좌(실전)에
        연결되어 있으며, 킬스위치가 켜져 있으면 사람의 추가 확인 없이 실제 주문이 나갑니다.
      </div>

      {stateError && <div className="state">상태 조회 실패: {stateError}</div>}
      {toggleError && <div className="state">킬스위치 전환 실패: {toggleError}</div>}

      <KillSwitch state={state} onToggle={handleToggle} busy={toggleBusy} />
      <StatusPanel state={state} />
      <StrategyPanel />
      <LogPanel rows={logRows} loading={logLoading} error={logError} />
    </div>
  )
}
