// 매매 탭(Phase 5.121) 표시용 순수 함수 모음 — 판단 로직은 전혀 없다(백엔드가 전담).
// 상태(mode/entry_reason/exit_levels/last_eval)를 사람이 읽는 문구·숫자로 바꾸기만 한다.

const MINUS = '−'
export const numFmt = new Intl.NumberFormat('ko-KR')

export const holdingOf = (state) => state?.status === 'holding' || state?.status === 'trailing'

// 엔진 모드 배지. 색 의미: 정지만 주황(경고), 나머지는 중립/정보 — 방향색(--up/--down)은 쓰지 않는다.
export const MODE_BADGE = {
  stopped: { label: '정지', cls: 'badge-warn' },
  watching: { label: '자동 감시 중', cls: 'badge-info' },
  auto_holding: { label: '자동 진입 보유', cls: 'badge-info' },
  manual_holding: { label: '수동 진입 보유', cls: 'badge-neutral-strong' },
}

export function modeOf(state) {
  if (!state) return null
  if (state.mode) return state.mode
  // 구버전 응답 폴백
  if (holdingOf(state)) return state.entry_source === 'manual' ? 'manual_holding' : 'auto_holding'
  return state.enabled ? 'watching' : 'stopped'
}

export const fmtPrice = (v) => (v === null || v === undefined ? '-' : `${numFmt.format(v)}원`)

export function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const abs = Math.abs(v).toFixed(digits)
  return `${v > 0 ? '+' : v < 0 ? MINUS : ''}${abs}%`
}

export const arrowOf = (v) => (v > 0 ? '▲' : v < 0 ? '▼' : '')
export const dirClass = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')

export function fmtWon(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const r = Math.round(v)
  return `${r > 0 ? '+' : r < 0 ? MINUS : ''}${numFmt.format(Math.abs(r))}원`
}

// KST 기준 시각 조각. Intl로 계산해 브라우저 시간대와 무관하게 한국 시각을 쓴다.
const kstParts = (date) => {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Seoul',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(date)
  const o = {}
  for (const p of parts) o[p.type] = p.value
  if (o.hour === '24') o.hour = '00'
  return o
}

export const kstDateStr = (date = new Date()) => {
  const p = kstParts(date)
  return `${p.year}-${p.month}-${p.day}`
}

export function kstHm(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = kstParts(d)
  return `${p.hour}:${p.minute}`
}

export function kstHms(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = kstParts(d)
  return `${p.hour}:${p.minute}:${p.second}`
}

export function kstMinutesOfDay(date = new Date()) {
  const p = kstParts(date)
  return Number(p.hour) * 60 + Number(p.minute)
}

const hmToMin = (hm) => {
  const [h, m] = String(hm).split(':').map(Number)
  return h * 60 + m
}

// 'HH:MM' 두 개로 된 구간 안인지(시작 포함, 끝 미포함).
export const inWindow = (nowMin, [start, end]) => nowMin >= hmToMin(start) && nowMin < hmToMin(end)

// NXT 마감(20:00~08:00 KST)이면 엔진이 평가하지 않는다(backend is_nxt_closed와 같은 구간, 표시용).
export const isEngineSession = (nowMin) => nowMin >= 8 * 60 && nowMin < 20 * 60

export function fmtDuration(totalMin) {
  const m = Math.max(0, Math.floor(totalMin))
  const h = Math.floor(m / 60)
  const mm = m % 60
  if (h > 0) return mm > 0 ? `${h}시간 ${mm}분` : `${h}시간`
  return `${mm}분`
}

// 보유 시간(분). entry_at은 ISO(UTC).
export function heldMinutes(entryAt, now = new Date()) {
  const t = new Date(entryAt).getTime()
  if (Number.isNaN(t)) return null
  return (now.getTime() - t) / 60000
}

// 장마감 강제청산 시작(예: '15:20')까지 남은 분. 이미 지났으면 음수.
export function minutesToForcedExit(engineRules, now = new Date()) {
  const start = engineRules?.eod_forced_exit_start
  if (!start) return null
  return hmToMin(start) - kstMinutesOfDay(now)
}

// 진입 근거 한 줄 — signal 필드에서 파생(원문 문자열은 보여주지 않는다).
export function entryReasonLine(entryReason, state) {
  if (!entryReason) return null
  const hm = entryReason.ts ? kstHm(entryReason.ts) : ''
  const prefix = hm ? `${hm} ` : ''
  if (entryReason.event_type === 'manual_entry') return `${prefix}수동 매수 (직접 고른 청산 조건)`
  const sig = entryReason.signal
  const parts = []
  if (sig?.golden_cross_recent) parts.push('골든크로스')
  if (sig?.volume_spike_recent) parts.push('거래량 급증')
  let line = parts.length ? `${prefix}${parts.join(' + ')}` : `${prefix}자동 진입 신호 충족`
  const flow = state?.entry_foreign_flow_sign
  if (flow === 'positive') line += ' · 진입 시 외인 현물 순매수'
  else if (flow === 'negative') line += ' · 진입 시 외인 현물 순매도'
  return line
}

// exit_levels(백엔드 발동가) -> 가격 사다리 틱. manual 키는 `manual_{i}_{kind}`.
const KIND_SHORT = {
  stop_loss_pct: '손절',
  stop_price: '손절가',
  take_profit_pct: '익절',
  target_price: '목표가',
  trail_pct: '트레일',
}

export function ladderTicks(state) {
  const levels = state?.exit_levels || []
  const out = []
  for (const lv of levels) {
    if (lv.key === 'stop_loss_risk_alert') continue // 경보 시 임시 손절선은 사다리에서 제외(문구로만 안내)
    let label = lv.label
    let side = lv.pct >= 0 ? 'up' : 'down'
    if (lv.key === 'stop_loss') label = '손절'
    else if (lv.key === 'trail_activate') label = '트레일 전환'
    else if (lv.key.startsWith('manual_')) {
      const kind = lv.key.replace(/^manual_\d+_/, '')
      label = KIND_SHORT[kind] || lv.label
      side = kind === 'stop_loss_pct' || kind === 'stop_price' || kind === 'trail_pct' ? 'down' : 'up'
    }
    out.push({ key: lv.key, label, price: lv.price, pct: lv.pct, side })
  }
  // 트레일링 중이면 고점 기준 청산선을 추가(엔진 상수 trail_giveback_pct 재사용)
  const R = state?.engine_rules
  if (state?.status === 'trailing' && state.peak_price != null && R && state.entry_source !== 'manual') {
    const price = Math.floor(state.peak_price * (1 - R.trail_giveback_pct / 100) + 1e-6)
    out.push({ key: 'trail_exit', label: '트레일 청산', price, pct: null, side: 'down' })
  }
  return out
}

// 현재가에서 가장 가까운 아래(손절 쪽)/위(익절·트레일 전환 쪽) 발동가까지 거리(%).
export function distances(state, ticks) {
  const cur = state?.current_price
  if (cur == null || !cur) return { down: null, up: null }
  const lower = ticks.filter((t) => t.side === 'down' && t.price < cur).sort((a, b) => b.price - a.price)[0]
  const upper = ticks.filter((t) => t.side === 'up' && t.price > cur).sort((a, b) => a.price - b.price)[0]
  return {
    down: lower ? { label: lower.label, pct: ((lower.price - cur) / cur) * 100 } : null,
    up: upper ? { label: upper.label, pct: ((upper.price - cur) / cur) * 100 } : null,
  }
}

export const BLOCK_LABEL = {
  entry_blocked_time: '진입 제한 시간대',
  entry_blocked_risk: '리스크 경보 활성',
  budget_blocked: '예산 한도 초과',
  stale_data: '오늘 분봉 없음(휴장 등)',
  no_bars: '분봉 데이터 없음',
  error: '분봉 조회 오류',
}

// 로그 행을 사람이 읽는 한 줄로(원문은 호출부가 펼침 상세로 따로 보여준다).
export const EVENT_LABEL = {
  entry: '자동 진입',
  trail_activate: '트레일 전환',
  exit_trail: '트레일 청산',
  exit_stop_loss: '손절',
  exit_eod_forced: '장마감 강제청산',
  exit_flow_reversal: '수급반전 청산',
  entry_blocked_time: '진입 차단(시간)',
  entry_blocked_risk: '진입 차단(리스크 경보)',
  error: '오류/차단',
  manual_entry: '수동 매수',
  exit_manual: '수동 매도',
  exit_manual_rule: '내 조건 청산',
  manual_rules_update: '조건 수정',
  buy_unconfirmed: '매수 체결 미확인',
  sell_unconfirmed: '매도 체결 미확인',
}

const HUMAN_REASON = {
  entry_blocked_time: '진입 신호가 있었지만 진입 제한 시간대라 보류했습니다.',
  entry_blocked_risk: '진입 신호가 있었지만 리스크 경보 중이라 보류했습니다.',
  exit_stop_loss: '손절선에 도달해 청산했습니다.',
  exit_trail: '고점 대비 반납폭에 도달해 청산했습니다.',
  exit_eod_forced: '장마감 시간대 청산 조건에 따라 청산했습니다.',
  exit_flow_reversal: '진입 시 외인 현물 수급 방향이 반전돼 청산했습니다.',
  exit_manual: '직접 매도했습니다.',
  exit_manual_rule: '직접 고른 청산 조건에 도달해 청산했습니다.',
  trail_activate: '진입가 대비 전환 수익률에 도달해 트레일링으로 전환했습니다.',
  manual_rules_update: '보유 중 청산 조건을 수정했습니다.',
  buy_unconfirmed: '매수 주문을 냈지만 체결을 확인하지 못해 취소를 시도했습니다.',
  sell_unconfirmed: '매도 주문을 냈지만 체결을 확인하지 못해 취소를 시도했습니다.',
}

export function humanLogReason(row) {
  if (HUMAN_REASON[row.event_type]) return HUMAN_REASON[row.event_type]
  if (row.event_type === 'manual_entry') return '직접 매수했습니다.'
  if (row.event_type === 'entry') {
    let sig = null
    try {
      sig = typeof row.signal_snapshot === 'string' ? JSON.parse(row.signal_snapshot) : row.signal_snapshot
    } catch {
      sig = null
    }
    const parts = []
    if (sig?.golden_cross_recent) parts.push('골든크로스')
    if (sig?.volume_spike_recent) parts.push('거래량 급증')
    return parts.length ? `${parts.join(' + ')} 신호로 진입했습니다.` : '진입 신호가 충족돼 진입했습니다.'
  }
  return row.reason || '-'
}

const ENTRY_TYPES = ['entry', 'manual_entry']
const EXIT_TYPES = ['exit_stop_loss', 'exit_trail', 'exit_eod_forced', 'exit_flow_reversal', 'exit_manual', 'exit_manual_rule']
const BLOCK_TYPES = ['entry_blocked_time', 'entry_blocked_risk']

// 오늘 로그 행(최신순) -> 요약. 진입·청산 짝은 같은 날 안에서만 맞춘다(전일 진입 청산은 손익을 계산하지 않는다).
export function summarizeToday(rows, qty = 1) {
  const asc = [...rows].sort((a, b) => new Date(a.ts) - new Date(b.ts))
  let open = null
  let pnl = 0
  let pairs = 0
  let unpaired = 0
  for (const r of asc) {
    if (ENTRY_TYPES.includes(r.event_type)) open = r
    else if (EXIT_TYPES.includes(r.event_type)) {
      if (open && open.price != null && r.price != null) {
        pnl += (Number(r.price) - Number(open.price)) * qty
        pairs += 1
      } else unpaired += 1
      open = null
    }
  }
  return {
    trades: asc.filter((r) => ENTRY_TYPES.includes(r.event_type) || EXIT_TYPES.includes(r.event_type)).length,
    blocked: asc.filter((r) => BLOCK_TYPES.includes(r.event_type)).length,
    errors: asc.filter((r) => r.event_type === 'error').length,
    pairs,
    unpaired,
    pnl: pairs > 0 ? pnl : null,
    openEntry: open,
  }
}
