import { useEffect, useState } from 'react'
import {
  addTradeCandidate,
  deleteTradeCandidate,
  fetchAutoTradeLog,
  fetchAutoTradePerformance,
  fetchAutoTradeState,
  getTradeCandidates,
  manualBuyAutoTrade,
  manualSellAutoTrade,
  syncAutoTradeState,
  toggleAutoTrade,
  updateManualExitRules,
} from '../api'
import { Collapsible } from '../components/ManualTradePanel'
import { PositionCard, StatusBar, TodayLine, WaitingCard } from '../components/TradePanels'
import { applyDevMock } from '../devMock'
import { holdingOf, kstDateStr } from '../tradeView'

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

const EVENT_TYPE_LABEL = {
  entry: '진입',
  trail_activate: '트레일 전환',
  exit_trail: '트레일 청산',
  exit_stop_loss: '손절',
  // PLAN.md §5.55(2026-08-06, 실제 손실 사고 이후 안전 규칙) 추가분.
  exit_eod_forced: '장마감 강제청산',
  exit_flow_reversal: '수급반전 조기청산',
  entry_blocked_time: '진입 차단(시간)',
  entry_blocked_risk: '진입 차단(리스크 경보)',
  error: '오류/차단',
  // PLAN.md §5.56(2026-08-11) — 자동 감지 엔진 위에 얹는 수동 매수/매도 버튼.
  manual_entry: '수동 매수',
  exit_manual: '수동 매도',
  // Phase 5.118 — 수동 매수 시 고른 사용자 조건으로 엔진이 낸 청산 / 보유 중 조건 수정 기록.
  exit_manual_rule: '수동 조건 청산',
  manual_rules_update: '수동 조건 수정',
  // PLAN.md §5.71(2026-08-19) — 주문은 접수됐지만 체결 미확인(취소 시도함).
  buy_unconfirmed: '매수 체결 미확인',
  sell_unconfirmed: '매도 체결 미확인',
  sell_pending_auction: '동시호가 매도 대기',
}

// 이 페이지가 보여주는 값들은 전부 PLAN.md §5.54/§5.55에서 사용자가 직접 확정한
// 규칙 그대로다 — "이 조합이 좋다"는 판단 문구가 아니라 지금 설정된 규칙 자체를
// 고정 텍스트로 서술한다(house rule §5).
// 수량 표기 — 보유 중이면 실제 수량, 대기 중이면 한도 안내(수량은 주문 시점 호가·주문가능금액으로 결정).
const qtyRuleText = (target) =>
  target.qty ? `${target.qty}주 보유` : `최대 ${Math.round((target.max_order_notional || 50000) / 10000)}만원 한도 내 최대 수량`

const buildStrategyRules = (target) => [
  {
    label: '대상 종목',
    value: target ? `${target.name} (${target.code}) 고정 · ${qtyRuleText(target)}` : '-',
  },
  { label: '진입 조건', value: 'ma_cross.state == "golden" AND volume_spike.is_spike == true' },
  {
    label: '청산(자동 진입 · 트레일링 스탑)',
    value: '진입가 대비 +1% 도달 시 트레일 모드 전환 → 이후 고점(peak) 대비 -0.5% 이상 반납 시 매도 (§5.83, ratchet 방식)',
  },
  { label: '손절', value: '상태 무관, 진입가 대비 -1.5% 이하 도달 시 즉시 매도(리스크 경보 활성 중엔 -0.8%로 임시 축소)' },
  {
    label: '진입 시간 제한 (§5.55-1)',
    value: '개장 후 10분(09:00~09:10)·마감 전 20분(15:10~15:30) KST엔 신규 진입만 금지 — 손절/청산은 이 시간에도 항상 작동',
  },
  {
    label: '장마감 전 조건부 오버나잇 청산 (§5.55-2)',
    value:
      '15:10 KST 이후 보유 중이면: 리스크 경보 활성 시 무조건 청산. 아니면 (평가손익>0) AND (status=="trailing") AND (코스닥 외국인 연속매도 아님) 셋 다 충족해야 오버나잇 허용, 하나라도 실패 시 무조건 청산',
  },
  {
    label: '리스크 경보 연동 (§5.55-3)',
    value: '서킷브레이커/사이드카/거래량급증/수급가속도 경보 활성 중엔 신규 진입 금지 + 보유 중 손절선 -0.8%로 임시 축소',
  },
  {
    label: '수급 방향 전환 조기청산 (§5.55-4)',
    value: '진입 시점 외인 현물 누적 순매수 부호가 반전되면(매수→매도 등) 가격 조건과 무관하게 조기 청산',
  },
]

function multipleBadge(multiple) {
  // 한국 관행: 레버리지(상승 베팅)=빨강, 인버스(하락 베팅)=파랑
  if (multiple > 1) return { label: `레버리지${multiple}X`, cls: 'up' }
  if (multiple < 0) return { label: '인버스', cls: 'down' }
  return { label: '1배', cls: '' }
}

function MultipleBadge({ multiple }) {
  const b = multipleBadge(multiple)
  return <span className={`at-mult ${b.cls}`}>{b.label}</span>
}

// 수동 매수 종목 후보 관리 (PLAN.md §5.89-1) — 추천(읽기전용) / 후보(사용자 고정, 추가·삭제).
function CandidatePanel() {
  const [data, setData] = useState({ recommended: [], manual: [] })
  const [error, setError] = useState(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      setData(await getTradeCandidates())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleAdd = async (e) => {
    e.preventDefault()
    const c = code.trim().toUpperCase()
    if (!c) return
    setBusy(true)
    try {
      await addTradeCandidate(c)
      setCode('')
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (id) => {
    try {
      await deleteTradeCandidate(id)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="at-block">
      {error && <div className="state">후보 처리 실패: {error}</div>}
      <div className="at-k">추천 종목 (시스템 시나리오, 읽기 전용)</div>
      {data.recommended.length === 0 ? (
        <div className="at-sub">오늘 추천 없음</div>
      ) : (
        <div className="at-list">
          {data.recommended.map((r) => (
            <div key={r.id} className="at-item">
              <strong>{r.name}</strong>
              <span className="at-sub-inline">{r.code}</span>
              <MultipleBadge multiple={r.multiple} />
              {r.scenario && <span>{r.scenario}</span>}
              {r.reason && <span className="at-sub-inline">{r.reason}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="at-k at-k-gap">후보 종목 (직접 관리)</div>
      <form onSubmit={handleAdd} className="at-item">
        <input
          className="er-input"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="종목코드 (예: 233740)"
          aria-label="후보 종목코드"
          maxLength={6}
        />
        <button type="submit" className="btn btn-secondary btn-sm" disabled={busy || !code.trim()}>
          {busy ? '추가 중…' : '추가'}
        </button>
      </form>
      {data.manual.length === 0 ? (
        <div className="at-sub">등록된 후보 없음</div>
      ) : (
        data.manual.map((r) => (
          <div key={r.id} className="at-item">
            <strong>{r.name}</strong>
            <span className="at-sub-inline">{r.code}</span>
            <MultipleBadge multiple={r.multiple} />
            <button type="button" className="btn btn-danger btn-sm" onClick={() => handleDelete(r.id)}>
              삭제
            </button>
          </div>
        ))
      )}
    </div>
  )
}

// 동기화 버튼(PLAN.md §5.82) — DB status와 실계좌(kt00018) 보유수량이
// `_confirm_holdings_qty`의 재시도(같은 Phase)로도 못 잡을 만큼 어긋났을 때
// 쓰는 수동 재점검. 2026-09-04 실사고(실제론 팔렸는데 DB는 trailing으로
// 남아있던 것)처럼 발견까지 며칠 걸리는 걸 막으려고, 사용자가 이상하다 싶을
// 때 아무 때나 눌러서 바로 확인할 수 있게 한다. 킬스위치 상태와 무관하게
// 항상 활성(수동 매도와 같은 이유 — 사실을 확인/정정하는 조회 작업이지
// 새 주문이 아니다).
function SyncButton({ onSync, busy, result, error }) {
  return (
    <div className="at-block">
      <div className="at-k">실계좌 동기화 — 위 보유 현황이 실제 계좌와 다른 것 같을 때 (잔고만 조회·정정, 새 주문 없음)</div>
      <div className="at-row at-row-start">
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={onSync}>
          {busy ? '확인 중…' : '동기화'}
        </button>
        {error && <span className="at-sub-inline at-warn-text">정정 실패: {error}</span>}
        {!error && result && (
          <span className="at-sub-inline">
            {result.corrected
              ? `정정함 — ${result.detail}`
              : `일치 확인 — 실보유 ${result.actual_qty}주, 변경 없음`}
          </span>
        )}
      </div>
    </div>
  )
}

// 엔진 전략 상세 — 청산 요약은 위 수동 매매 블록에 있으므로 여기는 접어 둔다.
function StrategyPanel({ target }) {
  const rules = buildStrategyRules(target)
  return (
    <details className="at-block at-details">
      <summary>전략 규칙 상세 (진입 조건·시간 제한·리스크 연동 포함)</summary>
      <p className="at-note">사용자가 직접 확정한 값입니다. 지금 설정된 규칙 그대로이며 매매 판단 문구가 아닙니다.</p>
      <div className="at-scroll">
        <table className="at-table at-table-rules">
          <tbody>
            {rules.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                <td>{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

const ENTRY_SOURCE_LABEL = { auto: '자동 진입', manual: '수동 진입' }

// 진입 방식별(자동/수동) 성과 요약(PLAN.md §5.76) — AutoTradeLog를 그대로
// 집계한 결과를 보여줄 뿐, "자동매매를 꺼라/켜라" 같은 판단 문구는 만들지
// 않는다(숫자만 표시, 종합 판단은 사용자 몫).
function PerformancePanel({ data, loading, error }) {
  return (
    <div className="at-block">
      <div className="at-k">진입 방식별 성과 — 완결된 거래만 집계합니다(청산 안 된 보유 중 포지션은 제외).</div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state">불러오기 실패: {error}</div>}
      {!loading && !error && data && (
        <>
          <div className="at-scroll">
            <table className="at-table">
              <thead>
                <tr>
                  {['진입 방식', '거래 수', '승률', '평균 수익률', '누적 수익률'].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {['auto', 'manual'].map((source) => {
                  const g = data.by_source[source]
                  return (
                    <tr key={source}>
                      <td className="nowrap">{ENTRY_SOURCE_LABEL[source]}</td>
                      <td>{g.n}건</td>
                      <td>{g.win_rate_pct === null ? '-' : `${g.win_rate_pct.toFixed(1)}%`}</td>
                      <td className={rateClass(g.avg_return_pct)}>{rateLabel(g.avg_return_pct)}</td>
                      <td className={rateClass(g.cumulative_return_pct)}>{rateLabel(g.cumulative_return_pct)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {data.anomalies.length > 0 && (
            <div className="at-sub">
              집계에서 제외된 이상 항목 {data.anomalies.length}건이 있습니다(진입·청산 짝을 찾지 못함).
            </div>
          )}
        </>
      )}
    </div>
  )
}

// 페이지당 행 수 — api.js fetchAutoTradeLog 기본값과 맞춘다.
const LOG_PAGE_SIZE = 30

function LogPanel({
  rows,
  loading,
  error,
  date,
  onDateChange,
  offset,
  total,
  onPrevPage,
  onNextPage,
  showAll,
  onShowAllChange,
}) {
  const hasPrev = offset > 0
  const hasNext = offset + rows.length < total
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + rows.length
  return (
    <div className="at-block">
      <div className="at-k">매매일지 (감사 로그, 최신이 위) — 실행뿐 아니라 판단 근거(신호값)까지 그대로 기록합니다.</div>
      <div className="at-row at-row-start at-log-bar">
        <label className="at-date">
          날짜
          <input className="er-input" type="date" value={date} onChange={(e) => onDateChange(e.target.value)} />
        </label>
        {date && (
          <button type="button" className="toggle-chip" onClick={() => onDateChange('')}>
            전체 기간
          </button>
        )}
        {/* PLAN.md §5.73 — 기본은 실제 체결된 거래(진입/청산/수동매수/수동매도)만.
            차단(entry_blocked 계열)/오류/체결미확인/트레일전환은 반복적으로
            쌓여 판단 근거만 장황해지고 정보량이 적다는 지적으로 기본 숨김
            처리, 필요할 때만 이 토글로 펼쳐본다. */}
        <button
          type="button"
          className={`toggle-chip ${showAll ? 'active' : ''}`}
          aria-pressed={showAll}
          onClick={() => onShowAllChange(!showAll)}
        >
          차단·오류 포함
        </button>
        <span className="at-sub-inline">{total === 0 ? '0건' : `${rangeStart}–${rangeEnd} / 총 ${total}건`}</span>
        <div className="at-row at-row-start">
          <button type="button" className="toggle-chip" disabled={!hasPrev} onClick={onPrevPage}>
            이전
          </button>
          <button type="button" className="toggle-chip" disabled={!hasNext} onClick={onNextPage}>
            다음
          </button>
        </div>
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state">불러오기 실패: {error}</div>}
      {!loading && !error && rows.length === 0 && (
        <div className="state">
          {date ? '이 날짜엔 기록이 없습니다.' : '아직 기록이 없습니다.'}
          {!showAll && ' (차단·오류 포함으로 보면 나올 수 있습니다)'}
        </div>
      )}
      {!loading && !error && rows.length > 0 && (
        <div className="at-scroll">
          <table className="at-table at-table-log">
            <thead>
              <tr>
                {['시각', '이벤트', '가격', '판단 근거'].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap">{formatDateTime(r.ts)}</td>
                  <td className="nowrap">
                    <span className={`badge ${r.event_type === 'error' ? 'badge-warn' : 'badge-info'}`}>
                      {EVENT_TYPE_LABEL[r.event_type] || r.event_type}
                    </span>
                  </td>
                  <td className="nowrap">{priceLabel(r.price)}</td>
                  <td className="at-reason">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
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
  const [logDate, setLogDate] = useState('')
  const [logOffset, setLogOffset] = useState(0)
  const [logTotal, setLogTotal] = useState(0)
  // PLAN.md §5.73 — 기본은 실제 체결된 거래만(showAll=false -> tradesOnly=true).
  const [logShowAll, setLogShowAll] = useState(false)

  // PLAN.md §5.76 — 진입 방식별 성과 요약. 로그 전체를 훑는 집계라 15초
  // 타이머엔 얹지 않고, 마운트 시 1회 + 로그가 실제로 바뀔 만한 시점(수동
  // 매수/매도 직후)에만 다시 불러온다.
  const [perfData, setPerfData] = useState(null)
  const [perfLoading, setPerfLoading] = useState(true)
  const [perfError, setPerfError] = useState(null)

  // PLAN.md §5.56 — 자동 감지 엔진 위에 얹는 수동 매수/매도 버튼 상태.
  const [manualBuyBusy, setManualBuyBusy] = useState(false)
  const [manualBuyError, setManualBuyError] = useState(null)
  const [manualBuyNotice, setManualBuyNotice] = useState(null)
  const [manualSellBusy, setManualSellBusy] = useState(false)
  const [manualSellError, setManualSellError] = useState(null)

  // PLAN.md §5.82 — DB status와 실계좌 잔고를 직접 비교하는 동기화 버튼.
  const [syncBusy, setSyncBusy] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const [syncError, setSyncError] = useState(null)

  // ③ 오늘 — KST 오늘 날짜의 전체 일지(차단·오류 포함)로 거래 수·실현손익·차단 수를 계산한다.
  const [todayRows, setTodayRows] = useState([])
  const [todayLoading, setTodayLoading] = useState(true)
  const [todayError, setTodayError] = useState(null)

  const loadState = () => {
    setStateError(null)
    return fetchAutoTradeState()
      .then((s) => setState(applyDevMock(s)))
      .catch((e) => setStateError(e.message))
  }

  const loadToday = () =>
    fetchAutoTradeLog({ date: kstDateStr(), limit: 200, offset: 0, tradesOnly: false })
      .then((body) => {
        setTodayRows(body.rows || [])
        setTodayError(null)
      })
      .catch((e) => setTodayError(e.message))
      .finally(() => setTodayLoading(false))

  const loadLog = () => {
    setLogLoading(true)
    setLogError(null)
    return fetchAutoTradeLog({
      date: logDate || undefined,
      limit: LOG_PAGE_SIZE,
      offset: logOffset,
      tradesOnly: !logShowAll,
    })
      .then((body) => {
        setLogRows(body.rows || [])
        setLogTotal(body.total || 0)
      })
      .catch((e) => setLogError(e.message))
      .finally(() => setLogLoading(false))
  }

  const loadPerformance = () => {
    setPerfLoading(true)
    setPerfError(null)
    return fetchAutoTradePerformance()
      .then(setPerfData)
      .catch((e) => setPerfError(e.message))
      .finally(() => setPerfLoading(false))
  }

  const handleLogDateChange = (nextDate) => {
    setLogDate(nextDate)
    setLogOffset(0)
  }

  const handleLogShowAllChange = (nextShowAll) => {
    setLogShowAll(nextShowAll)
    setLogOffset(0)
  }

  const handleLogPrevPage = () => setLogOffset((o) => Math.max(0, o - LOG_PAGE_SIZE))
  const handleLogNextPage = () => setLogOffset((o) => o + LOG_PAGE_SIZE)

  useEffect(() => {
    loadState()
    loadLog()
    loadToday()
    // 실제 주문이 자동으로 나갈 수 있는 화면이라 상태/로그를 열어 둔 동안
    // 15초마다 새로고침한다 — 사용자가 진입/청산이 실제로 언제 일어났는지
    // 화면을 계속 보고 있을 수 있어야 한다(다른 탭들의 60초 폴링보다 짧게 잡음).
    // logDate/logOffset/logShowAll이 바뀌면(날짜 필터/페이지 이동/차단·오류
    // 포함 토글) 이 effect가 다시 돌면서 그 필터로 즉시 다시 불러오고, 15초
    // 타이머도 그 필터를 유지한 채 재시작된다 — 과거 페이지를 보고 있는데
    // 최신 페이지로 튕기지 않는다.
    const timer = setInterval(() => {
      loadState()
      loadLog()
      loadToday()
    }, 15000)
    return () => clearInterval(timer)
  }, [logDate, logOffset, logShowAll])

  // 성과 요약은 로그 필터(날짜/페이지/차단·오류 포함 토글)와 무관하게 로그
  // 전체를 훑는 집계라 마운트 시 한 번만 불러온다(위 15초 타이머엔 얹지
  // 않음 — 매매일지 자체가 바뀔 때만 다시 불러오면 충분).
  useEffect(() => {
    loadPerformance()
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

  const handleManualBuy = async (rules, acknowledged = false) => {
    setManualBuyError(null)
    setManualBuyNotice(null)
    setManualBuyBusy(true)
    try {
      const next = await manualBuyAutoTrade(rules, acknowledged)
      const applied = next?.manual_exit_rules
      setManualBuyNotice(
        `수동 매수 완료 — 진입가 ${priceLabel(next?.entry_price)}. 적용 조건: ${
          applied ? applied.summary : '엔진 고정 규칙'
        }${applied && !applied.has_stop ? ' (손절 조건 없음)' : ''} + 장마감 강제청산·외인 수급 반전.`,
      )
      await Promise.all([loadState(), loadLog(), loadToday(), loadPerformance()])
    } catch (err) {
      setManualBuyError(err.message)
    } finally {
      setManualBuyBusy(false)
    }
  }

  const handleUpdateRules = async (rules, acknowledged = false) => {
    await updateManualExitRules(rules, acknowledged)
    await Promise.all([loadState(), loadLog(), loadToday()])
  }

  const handleManualSell = async () => {
    setManualSellError(null)
    setManualBuyNotice(null)
    setManualSellBusy(true)
    try {
      await manualSellAutoTrade()
      await Promise.all([loadState(), loadLog(), loadToday(), loadPerformance()])
    } catch (err) {
      setManualSellError(err.message)
    } finally {
      setManualSellBusy(false)
    }
  }

  const handleSync = async () => {
    setSyncError(null)
    setSyncResult(null)
    setSyncBusy(true)
    try {
      const result = await syncAutoTradeState()
      setSyncResult(result)
      await Promise.all([loadState(), loadLog(), loadToday(), loadPerformance()])
    } catch (err) {
      setSyncError(err.message)
    } finally {
      setSyncBusy(false)
    }
  }

  const holding = holdingOf(state)
  const qty = state?.entry_qty || state?.target?.qty || 1

  return (
    <div className="at-page">
      {stateError && <div className="state">상태 조회 실패: {stateError}</div>}
      {toggleError && <div className="state">엔진 전환 실패: {toggleError}</div>}
      {manualBuyError && <div className="state">수동 매수 실패: {manualBuyError}</div>}
      {manualSellError && <div className="state">수동 매도 실패: {manualSellError}</div>}

      <StatusBar state={state} onToggle={handleToggle} busy={toggleBusy} />

      {!state && !stateError && <div className="at-card"><div className="mt-skel at-chart-skel" aria-hidden="true" /></div>}
      {state && holding && (
        <PositionCard state={state} onSell={handleManualSell} sellBusy={manualSellBusy} onUpdateRules={handleUpdateRules} />
      )}
      {state && !holding && (
        <WaitingCard state={state} onBuy={handleManualBuy} buyBusy={manualBuyBusy} buyNotice={manualBuyNotice} />
      )}
      {holding && manualBuyNotice && <div className="at-sub at-notice" role="status">{manualBuyNotice}</div>}

      <TodayLine rows={todayRows} loading={todayLoading} error={todayError} qty={qty} />

      <Collapsible title="기록·설정" summary="성과 · 전체 일지 · 엔진 규칙 · 후보 종목 · 실계좌 동기화" storageKey="at-records-open">
        <p className="at-note">이 영역에서는 주문이 나가지 않습니다(동기화는 잔고 조회·정정만).</p>
        <PerformancePanel data={perfData} loading={perfLoading} error={perfError} />
        <LogPanel
          rows={logRows}
          loading={logLoading}
          error={logError}
          date={logDate}
          onDateChange={handleLogDateChange}
          offset={logOffset}
          total={logTotal}
          onPrevPage={handleLogPrevPage}
          onNextPage={handleLogNextPage}
          showAll={logShowAll}
          onShowAllChange={handleLogShowAllChange}
        />
        <StrategyPanel target={state?.target} />
        <CandidatePanel />
        <SyncButton onSync={handleSync} busy={syncBusy} result={syncResult} error={syncError} />
      </Collapsible>
    </div>
  )
}
