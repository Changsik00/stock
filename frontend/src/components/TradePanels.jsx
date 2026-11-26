import { useEffect, useMemo, useState } from 'react'
import { fetchStockIntraday } from '../api'
import CandleChart from './CandleChart'
import {
  BLOCK_LABEL,
  EVENT_LABEL,
  MODE_BADGE,
  arrowOf,
  dirClass,
  distances,
  entryReasonLine,
  fmtDuration,
  fmtPct,
  fmtPrice,
  fmtWon,
  heldMinutes,
  holdingOf,
  humanLogReason,
  inWindow,
  isEngineSession,
  kstHm,
  kstHms,
  kstMinutesOfDay,
  ladderTicks,
  minutesToForcedExit,
  modeOf,
  summarizeToday,
} from '../tradeView'
import { Collapsible, ManualPosition, RuleTable, engineRuleRows, fixedRows } from './ManualTradePanel'
import ManualBuyBox from './ManualTradePanel'

// 분 단위 갱신용 현재 시각(보유 시간·강제청산까지 남은 시간). 값이 바뀌는 것을 알리는 용도만.
function useNow(intervalMs = 30000) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

// 수량 표기 — 보유 중이면 실제 보유 수량, 대기 중이면 "최대 N주(예상) · 5만원 한도". 대기 중 N은
// 기준가(최근 1분봉 종가) 기준 추정치이며 실제 수량은 주문 시점 매도1호가·계좌 주문가능금액으로 정해진다.
function qtyLabel(state) {
  const t = state?.target
  if (!t) return ''
  if (t.qty) return `${t.qty}주`
  const cap = t.max_order_notional || 50000
  const budget = t.total_budget || cap
  const limit = Math.min(cap, budget)
  const ref = Number(state?.reference_price)
  const est = ref > 0 ? Math.floor(limit / ref) : null
  const limitText = `${Math.round(limit / 10000)}만원 한도`
  return est != null ? `최대 ${est}주(예상) · ${limitText}` : `최대 수량 · ${limitText}`
}

// ① 상태 바 — 모드 배지 + 대상·수량 + 엔진 ON/OFF 스위치 + 실주문 배지. 방향색은 쓰지 않는다.
export function StatusBar({ state, onToggle, busy }) {
  const mode = modeOf(state)
  const badge = mode ? MODE_BADGE[mode] : null
  const target = state?.target
  const enabled = !!state?.enabled
  const holding = holdingOf(state)
  const warnStopped = holding && !enabled
  let note = null
  if (state) {
    if (warnStopped) note = '엔진이 꺼져 있어 보유 포지션의 손절·청산 감시가 멈춰 있습니다.'
    else if (!enabled) note = '엔진이 꺼져 있어 신호 평가와 자동 주문을 하지 않습니다.'
    else if (holding) note = '청산 조건은 엔진이 자동으로 실행합니다(실전 계좌, 확인 없이 주문).'
    else note = '진입 조건이 충족되면 확인 없이 실제 주문이 나갑니다(실전 계좌).'
  }
  return (
    <div className="at-status-bar">
      <div className="at-sb-row">
        <div className="at-sb-main">
          {badge ? <span className={`badge at-mode ${badge.cls}`}>{badge.label}</span> : <span className="mt-skel at-sb-skel" aria-hidden="true" />}
          {warnStopped && <span className="badge at-mode badge-warn">엔진 정지</span>}
          <div className="at-sb-target">
            <span className="at-sb-name">{target ? target.name : '-'}</span>
            <span className="at-sub-inline">{target ? `${target.code} · ${qtyLabel(state)}` : ''}</span>
          </div>
        </div>
        <div className="at-sb-side">
          <span className="badge at-mode badge-warn">실주문</span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="자동매매 엔진"
            className={`at-switch ${enabled ? 'is-on' : ''}`}
            disabled={busy || !state}
            onClick={() => onToggle(!enabled)}
          >
            <span className="at-switch-track" aria-hidden="true">
              <span className="at-switch-thumb" />
            </span>
            <span className="at-switch-text">{enabled ? '엔진 켜짐' : '엔진 꺼짐'}</span>
          </button>
        </div>
      </div>
      {note && <p className={`at-sb-note ${!enabled && holding ? 'at-warn-text' : ''}`}>{note}</p>}
      {target && target.db_code && target.db_code !== target.code && (
        <p className="at-sb-note at-warn-text">
          주의: DB 상태 행의 종목코드({target.db_code})가 엔진 대상({target.code})과 다릅니다.
        </p>
      )}
    </div>
  )
}

// 가격 사다리 — 손절 ─ 진입 ─ 현재 ─ 트레일 전환/익절을 한 줄 막대 위에 눈금으로 표시.
function PriceLadder({ state, ticks }) {
  const entry = state.entry_price
  const cur = state.current_price
  const pts = [
    ...ticks.map((t) => ({ ...t, kind: 'level' })),
    { key: 'entry', label: '진입', price: entry, kind: 'entry' },
    ...(cur != null ? [{ key: 'cur', label: '현재', price: cur, kind: 'cur' }] : []),
  ].filter((p) => p.price != null)
  if (entry == null || pts.length < 2) return null
  const lo = Math.min(...pts.map((p) => p.price))
  const hi = Math.max(...pts.map((p) => p.price))
  const span = hi - lo || 1
  const pos = (price) => 14 + ((price - lo) / span) * 72 // 좌우 14% 여백(라벨 잘림 방지)
  const fixed = pts.filter((p) => p.kind !== 'cur')
  const curPt = pts.find((p) => p.kind === 'cur')
  return (
    <div className="at-ladder" aria-label="가격 사다리">
      {curPt && (
        <div className="at-lad-cur">
          <div className="at-lad-curpos" style={{ left: `${pos(curPt.price)}%` }}>
            <span className="at-lad-lab">현재 {numberText(curPt.price)}</span>
            <span className="at-lad-caret" aria-hidden="true" />
          </div>
        </div>
      )}
      <div className="at-lad-bar" aria-hidden="true">
        {fixed.map((p) => (
          <span key={p.key} className={`at-lad-tick is-${p.kind === 'entry' ? 'entry' : p.side}`} style={{ left: `${pos(p.price)}%` }} />
        ))}
      </div>
      <div className="at-lad-labels">
        {fixed.map((p) => (
          <div key={p.key} className="at-lad-fix" style={{ left: `${pos(p.price)}%` }}>
            <span className="at-lad-lab">{p.label}</span>
            <span className="at-lad-price">{numberText(p.price)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const numberText = (v) => new Intl.NumberFormat('ko-KR').format(v)

// 오늘 1분봉 캔들 — 다른 화면(종목 상세·지수 모달)과 같은 CandleChart를 쓰고, 진입가(중립 실선)·
// 손절가(주황 점선)를 기준선으로 겹친다(Phase 5.125, 차트 일관성).
function MiniChart({ code, state, ticks }) {
  const [bars, setBars] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let alive = true
    const load = () =>
      fetchStockIntraday(code, 1)
        .then((b) => {
          if (alive) {
            setBars(b.bars || [])
            setErr(false)
          }
        })
        .catch(() => alive && setErr(true))
    load()
    const t = setInterval(load, 60000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [code])

  const entry = state.entry_price
  const stop = ticks.find((t) => t.key === 'stop_loss' || (t.key.startsWith('manual_') && t.side === 'down' && !t.key.endsWith('trail_pct')))
  const stopPrice = stop?.price ?? null
  const stopLabel = stop?.label ?? '손절'
  // 매 초 리렌더(보유 시간 표시)마다 기준선을 다시 그리지 않도록 값이 바뀔 때만 새 배열을 만든다.
  const markLines = useMemo(
    () =>
      [
        entry != null && { price: Number(entry), label: `진입 ${numberText(entry)}`, tone: 'neutral' },
        stopPrice != null && { price: Number(stopPrice), label: `${stopLabel} ${numberText(stopPrice)}`, tone: 'warn' },
      ].filter(Boolean),
    [entry, stopPrice, stopLabel],
  )

  if (err && !bars) return <div className="at-sub">분봉을 불러오지 못했습니다.</div>
  if (!bars) return <div className="mt-skel at-chart-skel" aria-hidden="true" />
  if (bars.length < 2) return <div className="at-sub">오늘 분봉이 아직 없습니다.</div>

  return (
    <div className="at-chart">
      <CandleChart
        key={code}
        data={bars}
        height={240}
        intraday
        title="캔들 · 거래량 (1분봉 · 오늘)"
        markLines={markLines}
      />
    </div>
  )
}

// ② 보유 중 — 포지션 카드.
export function PositionCard({ state, onSell, sellBusy, onUpdateRules }) {
  const now = useNow()
  const manual = state.entry_source === 'manual' && state.manual_exit_rules
  const ticks = ladderTicks(state)
  const dist = distances(state, ticks)
  const pct = state.unrealized_pnl_pct
  const held = state.entry_at ? heldMinutes(state.entry_at, now) : null
  const toExit = minutesToForcedExit(state.engine_rules, now)
  const reasonLine = entryReasonLine(state.entry_reason, state)

  const handleSell = () => {
    if (!window.confirm('실제 계좌로 보유 포지션을 매도합니다. 계속할까요?')) return
    onSell()
  }

  return (
    <div className="at-card at-position">
      <div className="at-pos-head">
        <div>
          <div className="at-k">평가손익{state.status === 'trailing' ? ' · 트레일링 중' : ''}</div>
          {state.unrealized_pnl == null ? (
            <div className="at-hero">-</div>
          ) : (
            <div className={`at-hero ${dirClass(pct)}`}>
              {arrowOf(pct) && <span aria-hidden="true">{arrowOf(pct)} </span>}
              {fmtWon(state.unrealized_pnl)}
              <span className="at-hero-pct"> ({fmtPct(pct)})</span>
            </div>
          )}
        </div>
        <button type="button" className="btn at-btn-sell" disabled={sellBusy} onClick={handleSell}>
          {sellBusy ? '매도 처리 중…' : '지금 매도'}
        </button>
      </div>

      <PriceLadder state={state} ticks={ticks} />
      {(dist.down || dist.up) && (
        <div className="at-dist">
          {dist.down && <span>{dist.down.label}까지 {fmtPct(dist.down.pct, 1)}</span>}
          {dist.down && dist.up && <span className="at-dist-sep" aria-hidden="true"> / </span>}
          {dist.up && <span>{dist.up.label}까지 {fmtPct(dist.up.pct, 1)}</span>}
        </div>
      )}

      <MiniChart code={state.code} state={state} ticks={ticks} />

      <dl className="at-facts">
        <div>
          <dt>진입가 · 수량</dt>
          <dd>{fmtPrice(state.entry_price)} · {state.entry_qty ?? '-'}주</dd>
        </div>
        <div>
          <dt>현재가</dt>
          <dd>{fmtPrice(state.current_price)}</dd>
        </div>
        <div>
          <dt>보유 시간</dt>
          <dd>{held == null ? '-' : fmtDuration(held)}{state.entry_at ? ` (${kstHm(state.entry_at)} 진입)` : ''}</dd>
        </div>
        <div>
          <dt>장마감 강제청산</dt>
          <dd>
            {toExit == null
              ? '-'
              : toExit > 0
                ? `${state.engine_rules.eod_forced_exit_start}까지 ${fmtDuration(toExit)}`
                : '강제청산 시간대'}
          </dd>
        </div>
      </dl>
      <div className="at-reason-line">
        <span className="at-k">진입 근거</span>
        <span className="at-reason-text">{reasonLine || '기록 없음'}</span>
      </div>

      <Collapsible title="청산 조건" summary={manual ? '내 조건 + 공통 안전 청산' : '엔진 규칙 + 공통 안전 청산'} storageKey="at-exit-open">
        {manual ? (
          <ManualPosition state={state} onUpdateRules={onUpdateRules} />
        ) : (
          <>
            <div className="at-rules-title">기본 청산 (엔진 규칙)</div>
            <RuleTable rows={engineRuleRows(state)} />
            <div className="at-rules-title at-k-gap">공통 안전 청산 (장마감 강제청산 · 외인 수급 반전)</div>
            <RuleTable rows={fixedRows(state)} />
            <div className="at-sub">자동 진입 포지션의 규칙은 화면에서 바꿀 수 없습니다. 실제 주문 · 수수료·세금 미반영.</div>
          </>
        )}
      </Collapsible>
    </div>
  )
}

const FLAG = (v) => (v === true ? { text: '충족', cls: 'badge-info' } : v === false ? { text: '미충족', cls: 'badge-neutral' } : { text: '-', cls: 'badge-neutral' })

// ② 대기 중 왼쪽 — 자동 진입 엔진이 지금 왜 안 사는지(last_eval 기반, 조회 전용).
function EngineWatch({ state }) {
  const now = useNow(15000)
  const ev = state.last_eval
  const nowMin = kstMinutesOfDay(now)
  const windows = state.engine_rules?.entry_block_windows || []
  const activeWindow = windows.find((w) => inWindow(nowMin, w))
  const ageMs = ev?.at ? now.getTime() - new Date(ev.at).getTime() : null
  const stale = ageMs != null && ageMs > 180000
  const sessionOpen = isEngineSession(nowMin)

  let body
  if (!state.enabled) {
    body = <p className="at-sub">엔진이 꺼져 있어 평가하지 않습니다.</p>
  } else if (!ev) {
    body = <p className="at-sub">{sessionOpen ? '평가 기록 없음 — 서버 재시작 직후이거나 아직 첫 평가 전입니다.' : '엔진 평가 시간 아님 (08:00~20:00 KST 평가)'}</p>
  } else if (!sessionOpen || stale) {
    body = (
      <p className="at-sub">
        {sessionOpen ? '최근 평가가 지연되고 있습니다' : '엔진 평가 시간 아님 (08:00~20:00 KST 평가)'} · 마지막 평가 {kstHms(ev.at)}
      </p>
    )
  } else {
    const g = FLAG(ev.golden_recent)
    const s = FLAG(ev.spike_recent)
    const block = ev.block_reason ? BLOCK_LABEL[ev.block_reason] : activeWindow ? `진입 제한 시간대 (${activeWindow[0]}~${activeWindow[1]})` : null
    body = (
      <dl className="at-eval">
        <div>
          <dt>골든크로스(최근 봉)</dt>
          <dd><span className={`badge at-mode ${g.cls}`}>{g.text}</span></dd>
        </div>
        <div>
          <dt>거래량 급증(최근 봉)</dt>
          <dd><span className={`badge at-mode ${s.cls}`}>{s.text}</span></dd>
        </div>
        <div>
          <dt>차단 요인</dt>
          <dd className={block ? 'at-warn-text' : ''}>{block || '없음'}</dd>
        </div>
        <div>
          <dt>마지막 평가</dt>
          <dd>{kstHms(ev.at)}{ev.current_price != null ? ` · 현재가 ${fmtPrice(ev.current_price)}` : ''}</dd>
        </div>
      </dl>
    )
  }
  return (
    <div className="at-eng">
      <div className="at-rules-title">자동 진입 엔진</div>
      {body}
      <p className="at-sub">두 조건이 모두 충족되고 차단 요인이 없을 때 진입합니다. 참고용 현황이며 매수 권유가 아닙니다.</p>
    </div>
  )
}

// ② 대기 중 — 진입 대기 카드(왼쪽 엔진 감시 / 오른쪽 수동 매수). 보유 중엔 렌더하지 않는다.
export function WaitingCard({ state, onBuy, buyBusy, buyNotice }) {
  return (
    <div className="at-card at-waiting">
      <EngineWatch state={state} />
      <div className="at-wait-manual">
        <div className="at-rules-title">수동 매수</div>
        <ManualBuyBox state={state} onBuy={onBuy} buyBusy={buyBusy} buyNotice={buyNotice} />
      </div>
    </div>
  )
}

// ③ 오늘 — 한 줄 요약 + 펼치면 오늘 일지.
export function TodayLine({ rows, loading, error, qty }) {
  if (loading) return <div className="at-card"><div className="mt-skel at-today-skel" aria-hidden="true" /></div>
  if (error) return <div className="at-card"><div className="at-sub at-warn-text">오늘 기록을 불러오지 못했습니다: {error}</div></div>
  const sum = summarizeToday(rows, qty)
  let pnlText
  if (sum.pnl != null) pnlText = fmtWon(sum.pnl)
  else if (sum.openEntry) pnlText = '미청산'
  else pnlText = '-'
  const summary = (
    <span className="at-today-sum">
      오늘 거래 {sum.trades}건 · 실현손익{' '}
      <span className={dirClass(sum.pnl)}>{sum.pnl != null && arrowOf(sum.pnl) ? `${arrowOf(sum.pnl)} ` : ''}{pnlText}</span>
      {' '}· 차단 {sum.blocked}건{sum.errors > 0 ? ` · 오류 ${sum.errors}건` : ''}
    </span>
  )
  return (
    <div className="at-today">
      <Collapsible title="오늘" summary={summary} storageKey="at-today-open">
        {rows.length === 0 ? (
          <div className="at-sub">오늘 기록이 없습니다.</div>
        ) : (
          <ul className="at-today-list">
            {rows.map((r) => (
              <li key={r.id} className="at-today-row">
                <span className="at-today-time">{kstHm(r.ts)}</span>
                <span className="at-today-ev">{EVENT_LABEL[r.event_type] || r.event_type}</span>
                <span className="at-today-price">{r.price != null ? fmtPrice(r.price) : ''}</span>
                <span className="at-today-human">
                  {humanLogReason(r)}
                  {r.reason && (
                    <details className="at-raw">
                      <summary>원문</summary>
                      <span>{r.reason}</span>
                    </details>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {sum.unpaired > 0 && <div className="at-sub">전일 진입분 청산 {sum.unpaired}건은 실현손익에서 제외했습니다.</div>}
        <div className="at-sub">실현손익은 오늘 로그의 진입가·청산가 차이(수수료·세금 미반영)입니다.</div>
      </Collapsible>
    </div>
  )
}
