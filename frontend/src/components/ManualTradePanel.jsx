import { useEffect, useId, useRef, useState } from 'react'
import { addExitPreset, analyzeExitRules, deleteExitPreset, fetchExitPresets } from '../api'
import {
  ACTIVATE_RANGE_TEXT,
  DEFAULT_ACTIVATE,
  KIND_RANGE,
  PRESET_OPTIONS,
  PRICE_KINDS,
  RECOMMENDED,
  describeRule,
  describeRules,
  hasStop,
  previewActivate,
  previewTrigger,
  sameRules,
  upsertRule,
} from '../exitRules'

const numFmt = new Intl.NumberFormat('ko-KR')
const MINUS = '−'
const priceLabel = (v) => (v === null || v === undefined ? '-' : `${numFmt.format(v)}원`)

const fmtDateTime = (iso) => {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const fmtHm = (iso) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
}

// 조건별 발동가 방향 — 이상(익절·목표가) / 이하(손절·손절가·고점 대비 하락).
const dirOf = (kind) => (kind === 'take_profit_pct' || kind === 'target_price' ? '이상' : '이하')

// 접힘 상태를 localStorage에 기억한다(기본 접힘).
function usePersistedOpen(key) {
  const [open, setOpen] = useState(() => {
    try {
      return window.localStorage.getItem(key) === '1'
    } catch {
      return false
    }
  })
  const set = (v) => {
    setOpen(v)
    try {
      window.localStorage.setItem(key, v ? '1' : '0')
    } catch {
      /* 저장 불가 환경은 세션 내 상태만 유지 */
    }
  }
  return [open, set]
}

// 조건 분석(서버 단일 소스) — 조건이 바뀌면 300ms 디바운스 후 analyze 호출(주문·상태 변경 없음).
// 결과에 key(분석한 조건)를 붙여 현재 조건과 일치할 때만 유효로 본다.
function useExitAnalysis(rules, { entryPrice, peak } = {}) {
  const [res, setRes] = useState(null)
  const [failed, setFailed] = useState(null)
  const seq = useRef(0)
  const key = JSON.stringify([rules, entryPrice ?? null, peak ?? null])
  useEffect(() => {
    const my = ++seq.current
    const t = setTimeout(() => {
      analyzeExitRules(rules, { entryPrice, peak })
        .then((r) => {
          if (seq.current !== my) return
          setRes({ ...r, key })
          setFailed(null)
        })
        .catch((e) => {
          if (seq.current === my) setFailed(e.message)
        })
    }, 300)
    return () => clearTimeout(t)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const fresh = res && res.key === key ? res : null
  return { analysis: fresh, stale: !fresh, failed }
}

// 접힌 줄 배지 문구 — "오류 N · 주의 N".
const countText = (a) => {
  if (!a) return ''
  const parts = []
  if (a.errors.length) parts.push(`오류 ${a.errors.length}`)
  if (a.warnings.length) parts.push(`주의 ${a.warnings.length}`)
  return parts.join(' · ')
}

const IconErr = () => (
  <svg className="at-an-ico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
    <circle cx="7" cy="7" r="6" fill="currentColor" />
    <path d="M7 3.8v3.9" stroke="var(--badge-warn-bg)" strokeWidth="1.6" strokeLinecap="round" />
    <circle cx="7" cy="10.2" r="0.95" fill="var(--badge-warn-bg)" />
  </svg>
)
const IconWarn = () => (
  <svg className="at-an-ico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
    <path d="M7 1.8L13 12H1z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    <path d="M7 5.6v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    <circle cx="7" cy="10.4" r="0.8" fill="currentColor" />
  </svg>
)

// 분석 결과 표시: 오류(주황 진하게·차단) / 주의(주황 연하게·확인 후 진행) / 안내(회색) / 시나리오(회색 표).
function AnalysisPanel({ analysis, stale, failed }) {
  if (failed && !analysis) return <div className="at-sub">조건 분석을 불러오지 못했습니다: {failed}. 매수·수정 시 서버가 다시 검증합니다.</div>
  if (!analysis) return <div className="at-sub">조건을 검증하는 중…</div>
  return (
    <div className="at-an" aria-live="polite" aria-busy={stale}>
      {analysis.errors.length > 0 && (
        <ul className="at-an-list" aria-label="조건 오류">
          {analysis.errors.map((m) => (
            <li key={m} className="at-an-err">
              <IconErr />
              <span><b>오류</b> {m}</span>
            </li>
          ))}
        </ul>
      )}
      {analysis.warnings.length > 0 && (
        <ul className="at-an-list" aria-label="조건 주의">
          {analysis.warnings.map((m) => (
            <li key={m} className="at-an-warn">
              <IconWarn />
              <span><b>주의</b> {m}</span>
            </li>
          ))}
        </ul>
      )}
      {analysis.infos?.map((m) => (
        <div key={m} className="at-sub">{m}</div>
      ))}
      {analysis.scenarios.length > 0 && (
        <div className="at-scn">
          <div className="at-k">가격이 이렇게 움직이면 (가상 경로 예시 — 예측 아님, 수수료·세금 미반영)</div>
          <ul className="at-scn-list">
            {analysis.scenarios.map((sc) => (
              <li key={sc.title} className="at-scn-row">
                <span className="at-scn-title">{sc.title}</span>
                <span className="at-scn-result">{sc.result}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// 조건 가이드 — 종류별 설명, 현재 기준가로 계산한 예시, OR 규칙, 추천 조합 적용 버튼. 기본 접힘.
function GuideBody({ base, onApply, current, disabled }) {
  const ex = (kind, value, extra) => {
    const px = previewTrigger({ kind, value, ...extra }, base)
    return px == null ? '' : ` 예: 기준가 ${priceLabel(base)} 기준 ${priceLabel(px)}.`
  }
  return (
    <div className="at-guide">
      <p className="at-guide-p">
        <b>조건은 먼저 닿는 쪽으로 청산합니다(OR).</b> 모든 조건을 동시에 만족해야 하는 것이 아닙니다. 같은 시점에
        둘 이상 닿으면 손절 → 익절 → 고점 대비 순으로 사유를 정합니다.
      </p>
      <ul className="at-guide-list">
        <li>
          <b>익절 (+%, {KIND_RANGE.take_profit_pct})</b> 진입가보다 이만큼 오르면 매도합니다.{ex('take_profit_pct', 1)}
        </li>
        <li>
          <b>손절 (−%, {KIND_RANGE.stop_loss_pct})</b> 진입가보다 이만큼 내리면 매도합니다.{ex('stop_loss_pct', 1)}
        </li>
        <li>
          <b>고점 대비 하락 (−%, {KIND_RANGE.trail_pct})</b> 보유 중 최고가에서 이만큼 내려오면 매도합니다. 이익을
          따라가며 지키는 조건입니다.{ex('trail_pct', 0.5)}
        </li>
        <li>
          <b>도달 후 (+%, {ACTIVATE_RANGE_TEXT})</b> 고점 대비 조건에 붙입니다. 진입가 +% 에 한 번 닿은 뒤부터만
          작동합니다. 비우면 진입 직후부터 작동해 고점이 진입가 근처일 때 손실로 팔릴 수 있습니다.
          {ex('take_profit_pct', 1)}
        </li>
        <li>
          <b>목표가·손절가 (원)</b> 가격을 직접 정합니다. 익절 %와 목표가, 손절 %와 손절가는 하나만 고를 수
          있습니다.
        </li>
      </ul>
      <p className="at-guide-p">
        <b>익절과 고점 대비의 관계</b> 익절은 상한입니다. 익절 값이 '도달 후' 값보다 클 때만 고점 대비 조건이 의미가
        있습니다. 예: +1% 도달 후 고점 −0.5% 에 익절 +3%를 더하면 +1%~+3% 구간에서는 고점을 따라가며 지키고, +3%에
        닿으면 바로 익절합니다. 익절이 '도달 후' 값 이하이면 고점 대비 조건은 작동하지 못합니다.
      </p>
      <p className="at-guide-p">
        <b>수동 포지션 추가 규칙</b> 15:20~15:30 장마감 강제청산(오버나잇 예외 없음)과 외인 수급 반전 청산도 함께 적용됩니다. 기본 손절은
        적용되지 않으므로 손절을 직접 거는 것이 안전합니다. 엔진은 약 30초마다 확인하므로 급변 시 체결가가 조건
        가격보다 불리할 수 있습니다.
      </p>
      <div className="at-k">추천 조합 (누르면 아래 조건이 이 조합으로 바뀝니다)</div>
      <div className="at-guide-recs" role="group" aria-label="추천 조합">
        {RECOMMENDED.map((r) => (
          <button
            key={r.id}
            type="button"
            className={`toggle-chip ${sameRules(current, r.rules) ? 'active' : ''}`}
            aria-pressed={sameRules(current, r.rules)}
            disabled={disabled}
            title={r.desc}
            onClick={() => onApply(r.rules)}
          >
            {r.name}
          </button>
        ))}
      </div>
      <ul className="at-guide-list">
        {RECOMMENDED.map((r) => (
          <li key={r.id}>
            <b>{r.name}</b> {r.desc}
          </li>
        ))}
      </ul>
      <div className="at-sub">참고용 설명이며 매매 신호가 아닙니다.</div>
    </div>
  )
}

// 접기/펼치기 — 접힌 상태에서도 현재 조건 요약 한 줄(손절 없으면 주황)을 보여준다.
export function Collapsible({ title, summary, warn, badge, storageKey, children }) {
  const [open, setOpen] = usePersistedOpen(storageKey)
  const bodyId = useId()
  return (
    <div className="at-cond">
      <button
        type="button"
        className="at-cond-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(!open)}
      >
        <svg className={`at-cond-chev ${open ? 'is-open' : ''}`} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M4 2l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="at-cond-title">{title}</span>
        {badge && <span className={`badge at-badge ${badge.error ? 'badge-warn at-badge-err' : 'at-badge-warn'}`}>{badge.text}</span>}
        <span className={`at-cond-summary ${warn ? 'at-warn-text' : ''}`}>{summary}</span>
      </button>
      <div id={bodyId} className="at-cond-body" hidden={!open}>
        {children}
      </div>
    </div>
  )
}

// 조건 선택 UI — 프리셋 select + 숫자 input + 조합 프리셋 칩. rules는 부모가 소유한다(controlled).
function RuleEditor({ rules, onChange, base, peak, baseNote, disabled, analysis, stale, failed }) {
  const [presetId, setPresetId] = useState('tp1')
  const [val, setVal] = useState('1')
  const [act, setAct] = useState('')
  const [presets, setPresets] = useState([])
  const [presetName, setPresetName] = useState('')
  const [msg, setMsg] = useState(null)
  const opt = PRESET_OPTIONS.find((o) => o.id === presetId) || PRESET_OPTIONS[0]
  const isPrice = PRICE_KINDS.includes(opt.kind)

  const loadPresets = () =>
    fetchExitPresets()
      .then(setPresets)
      .catch((e) => setMsg(`프리셋 불러오기 실패: ${e.message}`))
  useEffect(() => {
    loadPresets()
  }, [])

  const handlePreset = (id) => {
    const o = PRESET_OPTIONS.find((x) => x.id === id)
    setPresetId(id)
    setVal(o.value === '' ? '' : String(o.value))
    // 고점 대비 하락은 '도달 후 +1%'를 기본으로 채운다(비우면 즉시 작동 + 경고).
    setAct(o.kind === 'trail_pct' ? DEFAULT_ACTIVATE : '')
  }

  const handleAdd = () => {
    const v = Number(val)
    if (!(v > 0)) return setMsg('값은 0보다 커야 합니다.')
    const rule = { kind: opt.kind, value: v }
    if (opt.kind === 'trail_pct' && act !== '') {
      const a = Number(act)
      if (!(a > 0)) return setMsg('도달 후 값은 0보다 커야 합니다(비우면 즉시 작동).')
      rule.activate_pct = a
    }
    setMsg(null)
    onChange(upsertRule(rules, rule))
  }

  // 기본 이름(placeholder 겸 비워 둔 채 저장할 때 쓰는 이름): 가이드 추천 조합과 같으면
  // "조합 이름: 조건 요약", 아니면 "조건 요약". 입력칸 maxLength(60)에 맞춰 자른다.
  const guideName = RECOMMENDED.find((r) => sameRules(rules, r.rules))?.name || ''
  const defaultName = rules.length
    ? (guideName ? `${guideName}: ${describeRules(rules)}` : describeRules(rules)).slice(0, 60)
    : ''
  const saveName = presetName.trim() || defaultName

  const handleSavePreset = async () => {
    try {
      await addExitPreset(saveName, rules)
      setPresetName('')
      setMsg(null)
      await loadPresets()
    } catch (e) {
      setMsg(`프리셋 저장 실패: ${e.message}`)
    }
  }

  const handleDeletePreset = async (id) => {
    try {
      await deleteExitPreset(id)
      await loadPresets()
    } catch (e) {
      setMsg(`프리셋 삭제 실패: ${e.message}`)
    }
  }

  return (
    <div className="at-editor">
      <div className="at-add">
        <select
          className="er-input"
          aria-label="조건 종류·프리셋"
          value={presetId}
          disabled={disabled}
          onChange={(e) => handlePreset(e.target.value)}
        >
          {PRESET_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          className="er-input"
          type="number"
          inputMode="decimal"
          min="0"
          step={isPrice ? '1' : '0.1'}
          value={val}
          disabled={disabled}
          aria-label={isPrice ? '가격(원)' : '퍼센트'}
          placeholder={isPrice ? KIND_RANGE[opt.kind] : KIND_RANGE[opt.kind]}
          onChange={(e) => setVal(e.target.value)}
        />
        {opt.kind === 'trail_pct' && (
          <input
            className="er-input"
            type="number"
            inputMode="decimal"
            min="0"
            step="0.1"
            value={act}
            disabled={disabled}
            aria-label={`도달 후: 진입가 대비 +% 에 닿은 뒤부터 작동 (${ACTIVATE_RANGE_TEXT}, 비우면 즉시 작동)`}
            placeholder={`도달 후 +% (${ACTIVATE_RANGE_TEXT})`}
            onChange={(e) => setAct(e.target.value)}
          />
        )}
        <button type="button" className="btn btn-secondary btn-sm" disabled={disabled} onClick={handleAdd}>
          조건 추가
        </button>
      </div>
      <div className="at-sub">
        허용 범위: {KIND_RANGE[opt.kind]}
        {opt.kind === 'trail_pct' &&
          ` · 도달 후 ${ACTIVATE_RANGE_TEXT} (기본 +${DEFAULT_ACTIVATE}%, 비우면 진입 직후부터 작동하며 주의가 표시됩니다)`}
        {' · 최대 5개 · 같은 종류는 하나만(새로 추가하면 교체)'}
      </div>
      <Collapsible title="조건 가이드" summary="종류별 설명 · 추천 조합" storageKey="at-guide-open">
        <GuideBody base={base} current={rules} disabled={disabled} onApply={onChange} />
      </Collapsible>

      {presets.length > 0 && (
        <div className="at-combos" role="group" aria-label="조합 프리셋">
          <span className="at-k">조합 프리셋</span>
          {presets.map((p) => (
            <span key={p.id} className="at-combo">
              <button type="button" className="toggle-chip" disabled={disabled} onClick={() => onChange(p.rules)}>
                {p.name}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={`프리셋 삭제: ${p.name}`}
                disabled={disabled}
                onClick={() => handleDeletePreset(p.id)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {rules.length === 0 ? (
        <div className="at-sub">선택된 조건이 없습니다.</div>
      ) : (
        <ul className="at-sel-list" aria-label="선택된 조건">
          {rules.map((r) => {
            const px = previewTrigger(r, r.kind === 'trail_pct' ? Math.max(peak || 0, base || 0) : base)
            const actPx = previewActivate(r, base)
            return (
              <li key={r.kind} className="at-sel">
                <span className="at-sel-label">{describeRule(r)}</span>
                <span className="at-sel-price">
                  {px != null ? `${priceLabel(px)} ${dirOf(r.kind)}` : '-'}
                  {actPx != null && ` · ${priceLabel(actPx)} 도달 후 활성`}
                </span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  aria-label={`조건 제거: ${describeRule(r)}`}
                  disabled={disabled}
                  onClick={() => onChange(rules.filter((x) => x.kind !== r.kind))}
                >
                  제거
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {baseNote && <div className="at-sub">{baseNote}</div>}
      <AnalysisPanel analysis={analysis} stale={stale} failed={failed} />

      <div className="at-add">
        <input
          className="er-input"
          value={presetName}
          maxLength={60}
          disabled={disabled}
          aria-label="조합 프리셋 이름"
          placeholder={defaultName || '현재 조건을 프리셋으로 저장'}
          onChange={(e) => setPresetName(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={disabled || !saveName || rules.length === 0 || !!analysis?.errors?.length}
          onClick={handleSavePreset}
        >
          프리셋 저장
        </button>
      </div>
      {msg && <div className="at-sub at-warn-text" role="status">{msg}</div>}
    </div>
  )
}

const SELECTED_KEY = 'at-manual-rules-selected'
function loadSelected() {
  try {
    const v = JSON.parse(window.localStorage.getItem(SELECTED_KEY) || 'null')
    return Array.isArray(v) ? v : [{ kind: 'take_profit_pct', value: 1 }, { kind: 'stop_loss_pct', value: 1 }]
  } catch {
    return [{ kind: 'take_profit_pct', value: 1 }, { kind: 'stop_loss_pct', value: 1 }]
  }
}

export function engineRuleRows(state) {
  const R = state?.engine_rules
  if (!R) return []
  const status = state?.status
  const hasEntry = state?.entry_price != null
  const entry = state?.entry_price
  const peak = state?.peak_price
  const fl = (x) => Math.floor(x + 1e-6)
  const ce = (x) => Math.ceil(x - 1e-6)
  return [
    {
      key: 'stop',
      label: '손절',
      text: `진입가 ${MINUS}${R.stop_loss_pct}% 이하 (위험경보 중 ${MINUS}${R.stop_loss_pct_risk_alert}%)`,
      price: hasEntry
        ? `${priceLabel(fl(entry * (1 - R.stop_loss_pct / 100)))} 이하 · 경보 시 ${priceLabel(fl(entry * (1 - R.stop_loss_pct_risk_alert / 100)))} 이하`
        : '',
    },
    {
      key: 'trail-on',
      label: '트레일링 전환',
      text: `진입가 +${R.trail_activate_pct}% 도달`,
      price: status === 'trailing' ? '전환됨' : hasEntry ? `${priceLabel(ce(entry * (1 + R.trail_activate_pct / 100)))} 이상` : '',
    },
    {
      key: 'trail-exit',
      label: '트레일링 청산',
      text: `전환 후 고점 대비 ${MINUS}${R.trail_giveback_pct}% 반납`,
      price:
        status === 'trailing' && peak != null
          ? `고점 ${priceLabel(peak)} → ${priceLabel(fl(peak * (1 - R.trail_giveback_pct / 100)))} 이하`
          : hasEntry
            ? '전환 후 고점 기준으로 계산'
            : '',
    },
  ]
}

export function fixedRows(state, manual = false) {
  const R = state?.engine_rules
  const win = R ? `${R.eod_forced_exit_start}~${R.eod_forced_exit_end}` : '15:20~15:30'
  return [
    {
      key: 'eod',
      label: '장마감 강제청산',
      text: manual
        ? `${win} 보유 중이면 청산합니다. 수동 포지션은 트레일링 상태로 전환되지 않아 오버나잇 예외 없이 청산됩니다`
        : `${win} 보유 중이면 오버나잇 조건(수익 중·트레일링·코스닥 외인 연속매도 아님)을 모두 못 맞출 때 청산`,
      price: '',
    },
    {
      key: 'flow',
      label: '외인 수급 반전',
      text: '진입 시점 외인 현물 수급 부호가 반전되면 가격과 무관하게 조기 청산',
      price: '',
    },
  ]
}

export function RuleTable({ rows }) {
  return (
    <ul className="at-rule-list">
      {rows.map((r) => (
        <li key={r.key} className="at-rule">
          <span className="at-rule-label">{r.label}</span>
          <span className="at-rule-text">{r.text}</span>
          {r.price && <span className="at-rule-price">{r.price}</span>}
        </li>
      ))}
    </ul>
  )
}

// 수동 진입 보유 중 — 조건 가격 한 줄 요약(원 단위 발동가 포함).
const compactRules = (rules) =>
  rules.length
    ? rules.map((r) => (r.trigger_price != null ? `${r.label} (${priceLabel(r.trigger_price)} ${dirOf(r.kind)})` : r.label)).join(' · ')
    : '조건 없음'

export function ManualPosition({ state, onUpdateRules }) {
  const mr = state.manual_exit_rules
  const current = mr.rules.map(({ kind, value, activate_pct: a }) => ({ kind, value, ...(a != null ? { activate_pct: a } : {}) }))
  const sig = JSON.stringify(current)
  const [draft, setDraft] = useState(current)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => setDraft(current), [sig]) // eslint-disable-line react-hooks/exhaustive-deps

  const base = state.entry_price
  const peak = state.peak_price
  const modified = !!mr.modified_at
  const { analysis, stale, failed } = useExitAnalysis(draft, { entryPrice: base, peak })
  const nErr = analysis?.errors.length || 0

  const ruleRows = mr.rules.map((r) => {
    let price = ''
    if (r.kind === 'trail_pct') {
      if (r.active === false) price = `진입가 ${priceLabel(r.activate_price)} 도달 후 활성화 (그 뒤 고점 기준 ${MINUS}${r.value}%)`
      else price = `고점 ${priceLabel(Math.max(peak || 0, base || 0))} → ${priceLabel(r.trigger_price)} 이하`
    } else if (r.trigger_price != null) price = `${priceLabel(r.trigger_price)} ${dirOf(r.kind)}`
    return { key: r.kind, label: r.kind_label, text: r.label, price }
  })

  const handleApply = async () => {
    setErr(null)
    let a
    try {
      a = await analyzeExitRules(draft, { entryPrice: base, peak })
    } catch (e) {
      setErr(e.message)
      return
    }
    if (a.errors.length) return setErr(a.errors.join(' / '))
    const warn = a.warnings.length ? `\n\n주의:\n${a.warnings.map((w) => `- ${w}`).join('\n')}` : ''
    const msg =
      `보유 중인 수동 포지션의 청산 조건을 바꿉니다(주문은 나가지 않습니다).\n\n` +
      `변경 후: ${describeRules(draft)}${warn}\n\n계속할까요?`
    if (!window.confirm(msg)) return
    setBusy(true)
    try {
      await onUpdateRules(draft, true)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="at-rules at-rules-wide">
      <div className="at-rules-title">
        매수 시 조건
        {modified && <span className="badge badge-neutral at-badge">수정됨 {fmtHm(mr.modified_at)}</span>}
      </div>
      <div className="at-sub">
        {fmtDateTime(state.entry_at)} 진입 · 진입가 {priceLabel(state.entry_price)}
      </div>
      <div className="at-cond-line">{compactRules(mr.original)}</div>
      {modified && (
        <div className="at-sub">원래 조건입니다. 현재 적용 중인 조건은 아래 표를 보세요: {compactRules(mr.rules)}</div>
      )}

      <div className="at-rules-title at-k-gap">이 포지션은 아래 사용자 조건 + 장마감·외인반전으로 청산됩니다</div>
      <RuleTable rows={[...ruleRows, ...fixedRows(state, true)]} />
      {!mr.has_stop && (
        <div className="at-sub at-warn-text">
          손절 조건이 없습니다 — 손실 제한은 장마감 강제청산·외인 수급 반전뿐입니다.
        </div>
      )}
      <div className="at-sub">
        기본 손절({MINUS}{state.engine_rules?.stop_loss_pct ?? 1.5}%)·위험경보 손절({MINUS}
        {state.engine_rules?.stop_loss_pct_risk_alert ?? 0.8}%)은 이 포지션에 적용되지 않습니다. 실제 주문 · 수수료·세금 미반영.
      </div>

      <Collapsible
        title="조건 수정"
        summary={describeRules(current)}
        warn={!mr.has_stop}
        storageKey="at-cond-open-hold"
      >
        <RuleEditor
          rules={draft}
          onChange={setDraft}
          base={base}
          peak={peak}
          disabled={busy}
          analysis={analysis}
          stale={stale}
          failed={failed}
        />
        <div className="at-row at-row-start">
          <button type="button" className="btn btn-primary btn-sm" disabled={busy || nErr > 0} onClick={handleApply}>
            {busy ? '적용 중…' : '수정한 조건 적용'}
          </button>
          {nErr > 0 && <span className="at-sub-inline at-warn-text">조건 오류 {nErr}건을 고치면 적용할 수 있습니다.</span>}
          {err && <span className="at-sub-inline at-warn-text">수정 실패: {err}</span>}
        </div>
      </Collapsible>
    </div>
  )
}

// 수동 매수 박스(Phase 5.121) — 대기 중에만 렌더된다(보유 중엔 상위에서 통째로 숨김).
// 청산 조건 편집기는 접어 두고(기본 접힘), 매수 버튼은 서버 분석 확인창을 거쳐 주문한다.
export default function ManualBuyBox({ state, onBuy, buyBusy, buyNotice }) {
  const enabled = !!state?.enabled
  const status = state?.status || 'idle'
  const canBuy = enabled && status === 'idle'
  const buyDisabledReason = !enabled ? '엔진이 꺼져 있습니다(켜야 매수할 수 있습니다)' : '이미 보유 중'
  const target = state?.target
  const targetLabel = target ? `${target.name}(${target.code})` : '대상 종목'

  const [rules, setRules] = useState(loadSelected)
  useEffect(() => {
    try {
      window.localStorage.setItem(SELECTED_KEY, JSON.stringify(rules))
    } catch {
      /* 무시 */
    }
  }, [rules])

  const ref = state?.reference_price
  const stopless = !hasStop(rules)
  const { analysis, stale, failed } = useExitAnalysis(rules)
  const nErr = analysis?.errors.length || 0
  const nWarn = analysis?.warnings.length || 0
  const summary = describeRules(rules)
  const badge = analysis && (nErr || nWarn) ? { text: countText(analysis), error: nErr > 0 } : null
  const [checking, setChecking] = useState(false)
  const [checkErr, setCheckErr] = useState(null)

  const handleBuyClick = async () => {
    // 확인창에 최신 서버 분석(오류·주의)을 그대로 보여준다. 오류가 있으면 주문 요청 자체를 보내지 않는다.
    setCheckErr(null)
    setChecking(true)
    let a
    try {
      a = await analyzeExitRules(rules)
    } catch (e) {
      setCheckErr(`조건 분석 실패: ${e.message}`)
      return
    } finally {
      setChecking(false)
    }
    if (a.errors.length) return setCheckErr(`조건 오류: ${a.errors.join(' / ')}`)
    const warn = a.warnings.length ? `주의:\n${a.warnings.map((w) => `- ${w}`).join('\n')}\n\n` : ''
    const msg =
      `실제 계좌로 ${targetLabel} 1주 매수 주문이 나갑니다.\n\n` +
      `청산 조건(수동 매수 사용자 조건): ${describeRules(rules)}\n\n` +
      warn +
      `+ 장마감 강제청산(15:20~15:30)·외인 수급 반전 청산은 그대로 적용됩니다.\n` +
      `기본 손절(${MINUS}${state?.engine_rules?.stop_loss_pct ?? 1.5}%)·위험경보 손절은 적용되지 않습니다.\n\n계속할까요?`
    if (!window.confirm(msg)) return
    onBuy(rules, true)
  }

  return (
    <div className="at-manual-buy">
      <div className="at-k">직접 고른 청산 조건으로 {targetLabel} 1주를 매수합니다.</div>
      <Collapsible title="청산 조건" summary={summary} warn={stopless || nErr > 0} badge={badge} storageKey="at-cond-open-buy">
        <RuleEditor
          rules={rules}
          onChange={setRules}
          base={ref}
          baseNote={
            ref
              ? `발동가는 최근 1분봉 종가 ${priceLabel(ref)} 기준 예상치입니다. 실제 진입가(지정가 주문가)에 따라 달라집니다.`
              : '기준가를 불러오지 못해 발동가는 매수 후 표시됩니다.'
          }
          disabled={buyBusy}
          analysis={analysis}
          stale={stale}
          failed={failed}
        />
      </Collapsible>
      <div className="at-row at-row-start">
        <button
          type="button"
          className="btn at-btn-buy"
          disabled={!canBuy || buyBusy || checking || nErr > 0}
          onClick={handleBuyClick}
        >
          {buyBusy ? '매수 처리 중…' : checking ? '조건 확인 중…' : '수동 매수'}
        </button>
      </div>
      {!canBuy && <div className="at-sub">매수 불가: {buyDisabledReason}</div>}
      {nErr > 0 && <div className="at-sub at-warn-text">매수 불가: 청산 조건 오류 {nErr}건 — 위 '청산 조건'을 펼쳐 고치세요.</div>}
      {checkErr && <div className="at-sub at-warn-text" role="status">{checkErr}</div>}
      <div aria-live="polite">{buyNotice && <div className="at-sub at-notice">{buyNotice}</div>}</div>
    </div>
  )
}
