// 수동 매수 청산 조건(Phase 5.118) 표시·미리보기 유틸. 판정은 서버(quant/exit_rules.py)가 하며,
// 여기 계산은 매수 전 "대략 이 가격" 미리보기와 라벨용이다(읽기 전용).
export const KIND_LABEL = {
  take_profit_pct: '익절',
  stop_loss_pct: '손절',
  trail_pct: '고점 대비 하락',
  target_price: '목표가',
  stop_price: '손절가',
}

export const PRICE_KINDS = ['target_price', 'stop_price']

const n = new Intl.NumberFormat('ko-KR')
const MINUS = '−'

// 프리셋 select 옵션 — value가 비어 있으면 숫자를 직접 입력한다.
export const PRESET_OPTIONS = [
  { id: 'tp1', kind: 'take_profit_pct', value: 1, label: '익절 +1%' },
  { id: 'tp2', kind: 'take_profit_pct', value: 2, label: '익절 +2%' },
  { id: 'tp3', kind: 'take_profit_pct', value: 3, label: '익절 +3%' },
  { id: 'sl1', kind: 'stop_loss_pct', value: 1, label: `손절 ${MINUS}1%` },
  { id: 'sl2', kind: 'stop_loss_pct', value: 2, label: `손절 ${MINUS}2%` },
  { id: 'sl3', kind: 'stop_loss_pct', value: 3, label: `손절 ${MINUS}3%` },
  { id: 'tr05', kind: 'trail_pct', value: 0.5, label: `고점 대비 ${MINUS}0.5%` },
  { id: 'tr1', kind: 'trail_pct', value: 1, label: `고점 대비 ${MINUS}1%` },
  { id: 'tr2', kind: 'trail_pct', value: 2, label: `고점 대비 ${MINUS}2%` },
  { id: 'tpc', kind: 'take_profit_pct', value: '', label: '익절 % 직접 입력' },
  { id: 'slc', kind: 'stop_loss_pct', value: '', label: '손절 % 직접 입력' },
  { id: 'trc', kind: 'trail_pct', value: '', label: '고점 대비 하락 % 직접 입력' },
  { id: 'tgt', kind: 'target_price', value: '', label: '목표가 (원) 직접 입력' },
  { id: 'stp', kind: 'stop_price', value: '', label: '손절가 (원) 직접 입력' },
]

export function describeRule(r) {
  const v = Number(r.value)
  switch (r.kind) {
    case 'take_profit_pct':
      return `익절 +${v}%`
    case 'stop_loss_pct':
      return `손절 ${MINUS}${v}%`
    case 'trail_pct': {
      const base = `고점 대비 ${MINUS}${v}%`
      return r.activate_pct != null ? `진입가 +${r.activate_pct}% 도달 후 ${base}` : base
    }
    case 'target_price':
      return `목표가 ${n.format(v)}원`
    case 'stop_price':
      return `손절가 ${n.format(v)}원`
    default:
      return r.kind
  }
}

export const describeRules = (rules) => (rules.length ? rules.map(describeRule).join(' · ') : '조건 없음')
export const hasStop = (rules) => rules.some((r) => r.kind === 'stop_loss_pct' || r.kind === 'stop_price')

// 매수 전 미리보기 발동가(원) — base는 기준가(대기 중엔 최근 1분봉 종가, 실제 진입가와 다를 수 있음).
export function previewTrigger(r, base) {
  if (!base) return null
  const v = Number(r.value)
  if (r.kind === 'take_profit_pct') return Math.ceil(base * (1 + v / 100) - 1e-6)
  if (r.kind === 'stop_loss_pct' || r.kind === 'trail_pct') return Math.floor(base * (1 - v / 100) + 1e-6)
  if (PRICE_KINDS.includes(r.kind)) return Math.round(v)
  return null
}

export function previewActivate(r, base) {
  if (!base || r.activate_pct == null) return null
  return Math.ceil(base * (1 + Number(r.activate_pct) / 100) - 1e-6)
}

// 같은 종류는 하나만 — 새 조건이 이전 같은 종류를 대체한다.
export function upsertRule(rules, rule) {
  return [...rules.filter((r) => r.kind !== rule.kind), rule]
}

// 종류별 허용 범위 — 서버(quant/exit_rules.py PCT_RANGE/ACTIVATE_RANGE)와 같은 값. 검증의 기준은 항상 서버이고
// 여기 값은 입력 힌트(placeholder·안내)용이다.
export const KIND_RANGE = {
  take_profit_pct: '0.2~30%',
  stop_loss_pct: '0.2~30%',
  trail_pct: '0.1~10%',
  target_price: '현재가보다 높은 가격(원)',
  stop_price: '현재가보다 낮은 가격(원)',
}
export const ACTIVATE_RANGE_TEXT = '0.1~30%'
export const DEFAULT_ACTIVATE = '1'

// 추천 조합 3개 — 마이그레이션 시드(d9a4f2b6c1e8)와 같은 조합.
export const RECOMMENDED = [
  {
    id: 'fixed',
    name: '① 고정 익절형',
    desc: '+1%에 닿으면 익절, -1%에 닿으면 손절',
    rules: [
      { kind: 'take_profit_pct', value: 1 },
      { kind: 'stop_loss_pct', value: 1 },
    ],
  },
  {
    id: 'trend',
    name: '② 추세 추종형',
    desc: '+1% 도달 후 고점 대비 -0.5%에서 청산, -1% 손절',
    rules: [
      { kind: 'stop_loss_pct', value: 1 },
      { kind: 'trail_pct', value: 0.5, activate_pct: 1 },
    ],
  },
  {
    id: 'trend-cap',
    name: '③ 추세+상한',
    desc: '② 조합에 +3% 익절 상한을 더함',
    rules: [
      { kind: 'stop_loss_pct', value: 1 },
      { kind: 'trail_pct', value: 0.5, activate_pct: 1 },
      { kind: 'take_profit_pct', value: 3 },
    ],
  },
]

// 두 조건 목록이 같은지(순서 무관) — 추천 조합 적용 표시용.
export const sameRules = (a, b) => {
  const key = (rs) =>
    JSON.stringify(
      [...rs]
        .map((r) => ({ kind: r.kind, value: Number(r.value), activate_pct: r.activate_pct ?? null }))
        .sort((x, y) => x.kind.localeCompare(y.kind)),
    )
  return key(a) === key(b)
}
