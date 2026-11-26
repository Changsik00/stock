// 개발 서버 전용 화면 확인용 — `?mock=auto|manual|stopped`(dev 빌드에서만 동작, 프로덕션 번들에선 제거).
// 서버 상태·DB는 바꾸지 않고 응답만 화면에서 덮어쓴다. 이 모드에서 매수/매도/전환 버튼은 누르지 않는다.
export function applyDevMock(s) {
  if (!import.meta.env.DEV || !s) return s
  const mock = new URLSearchParams(window.location.search).get('mock')
  if (!mock) return s
  if (mock === 'stopped') return { ...s, enabled: false, mode: 'stopped', last_eval: null }
  if (mock !== 'auto' && mock !== 'manual') return s
  const entry = 10900
  const cur = 10960
  const manual = mock === 'manual'
  const at = new Date(Date.now() - 95 * 60000).toISOString()
  const rules = [
    { kind: 'take_profit_pct', value: 1.5 },
    { kind: 'stop_loss_pct', value: 1 },
  ]
  const trig = (r) =>
    Math.round(r.kind === 'take_profit_pct' ? entry * (1 + r.value / 100) : entry * (1 - r.value / 100))
  return {
    ...s,
    enabled: true,
    status: 'holding',
    mode: manual ? 'manual_holding' : 'auto_holding',
    entry_source: manual ? 'manual' : 'auto',
    entry_price: entry,
    entry_qty: 1,
    entry_at: at,
    current_price: cur,
    unrealized_pnl: cur - entry,
    unrealized_pnl_pct: ((cur - entry) / entry) * 100,
    entry_foreign_flow_sign: 'positive',
    entry_reason: {
      ts: at,
      event_type: manual ? 'manual_entry' : 'entry',
      reason: '',
      signal: { golden_cross_recent: true, volume_spike_recent: true },
    },
    exit_levels: manual
      ? rules.map((r, i) => ({
          key: `manual_${i}_${r.kind}`,
          label: r.kind === 'take_profit_pct' ? `익절 +${r.value}%` : `손절 −${r.value}%`,
          price: trig(r),
          pct: ((trig(r) / entry) - 1) * 100,
        }))
      : [
          { key: 'stop_loss', label: '손절', price: Math.floor(entry * 0.985), pct: -1.5 },
          { key: 'stop_loss_risk_alert', label: '손절(리스크 경보 시)', price: Math.floor(entry * 0.992), pct: -0.8 },
          { key: 'trail_activate', label: '트레일 전환', price: Math.ceil(entry * 1.01), pct: 1 },
        ],
    manual_exit_rules: manual
      ? {
          rules: rules.map((r) => ({
            ...r,
            label: r.kind === 'take_profit_pct' ? `+${r.value}% 도달 시 익절` : `−${r.value}% 도달 시 손절`,
            kind_label: r.kind === 'take_profit_pct' ? '익절' : '손절',
            trigger_price: trig(r),
          })),
          original: rules.map((r) => ({
            ...r,
            label: r.kind === 'take_profit_pct' ? `+${r.value}% 도달 시 익절` : `−${r.value}% 도달 시 손절`,
          })),
          modified_at: null,
          has_stop: true,
          summary: '익절 +1.5% · 손절 −1%',
        }
      : null,
  }
}

