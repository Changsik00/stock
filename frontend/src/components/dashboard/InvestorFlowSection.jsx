import { useState } from 'react'
import MetricTile, { Skel, heroEok } from './MetricTile'
import { StaleDate } from './shared'
import { INVESTOR_COLOR_VAR } from '../../constants'

// 투자자별 수급(현물) 섹션(PLAN.md Phase 5.124) — 값(얼마)은 타일 한 곳, 해석(어떻게)은 그 아래 접는 영역.
// 이전의 '투자자별 수급 요약' 타일 + '외인 매도 일관성' 카드를 한 섹션으로 합쳤다.
// 타일: 코스피+코스닥 합계(큰 숫자) + 코스피/코스닥 한 줄씩. 외국인·기관계에는 시장별 판정 배지.
// 일관 매도만 주황(--warn-text 계열 badge-warn), 나머지는 중립 회색. --up/--down은 ▲▼ 방향 표시에만 쓴다.
// 참고용 통계 — 매매 신호 아님.

const MARKET_NAME = { kospi: '코스피', kosdaq: '코스닥' }
const MARKETS = ['kospi', 'kosdaq']
const LABEL_SELL = '일관 매도'
const JUDGED = ['외국인', '기관계']
const pctFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const pct1Fmt = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

function mmdd(iso) {
  return iso ? iso.slice(5) : ''
}

function signedPct(v) {
  if (typeof v !== 'number') return '-'
  return `${v < 0 ? '−' : '+'}${pct1Fmt.format(Math.abs(v))}%`
}

// 접힘 상태를 localStorage에 기억한다(기본 접힘) — ManualTradePanel의 usePersistedOpen과 같은 관례.
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

// 분할 한 줄: 코스피/코스닥 값 + ▲▼(방향색은 화살표만)
function SplitLine({ name, value }) {
  const has = typeof value === 'number'
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : ''
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : ''
  return (
    <span className="ifs-line">
      <span className="ifs-line-name">{name}</span>
      <span className="ifs-line-val">
        {arrow && (
          <>
            <span className={`ifs-arrow ${cls}`} aria-hidden="true">{arrow}</span>
            <span className="mt-sr">{value > 0 ? '순매수' : '순매도'}</span>
          </>
        )}
        {has ? heroEok(value) ?? '-' : '-'}
      </span>
    </span>
  )
}

// 판정 배지: 두 시장이 같으면 하나로 합친다. 일관 매도만 주황.
function JudgeChips({ markets }) {
  const items = MARKETS.map((m) => ({ m, label: markets?.[m]?.label })).filter((x) => x.label)
  if (items.length === 0) return null
  const chips =
    items.length === 2 && items[0].label === items[1].label
      ? [{ key: 'both', text: `코스피·코스닥 ${items[0].label}`, label: items[0].label }]
      : items.map((x) => ({ key: x.m, text: `${MARKET_NAME[x.m]} ${x.label}`, label: x.label }))
  return (
    <span className="ifs-chips">
      {chips.map((c) => (
        <span key={c.key} className={`badge ${c.label === LABEL_SELL ? 'badge-warn' : 'badge-neutral'}`}>
          {c.text}
        </span>
      ))}
    </span>
  )
}

function Panel({ market, analysis, program, stats }) {
  const name = MARKET_NAME[market]
  if (!analysis) {
    return (
      <div className="vr-panel fc-panel">
        <h3 className="vr-name">{name}</h3>
        <span className="vr-state">표본 부족 (10분 단위 6개 미만)</span>
      </div>
    )
  }
  const isSell = analysis.label === LABEL_SELL
  const { final, peak, peak_time: peakTime } = analysis
  const hasPeak = typeof peak === 'number' && peakTime
  const atPeak = hasPeak && peak === final
  return (
    <div className="vr-panel fc-panel">
      <h3 className="vr-name">{name}</h3>
      <span className={`fc-verdict${isSell ? ' fc-label-sell' : ''}`}>{analysis.label}</span>
      {hasPeak && (
        <span className="ifs-peak">
          {atPeak
            ? `${peakTime} 이후 현재가 고점 ${heroEok(final)}`
            : `${peakTime} 고점 ${heroEok(peak)} → 현재 ${heroEok(final)} (고점 대비 ${heroEok(final - peak)})`}
        </span>
      )}
      <dl className="fc-list">
        <div>
          <dt>10분 단위 매도 비율</dt>
          <dd>{pctFmt.format(analysis.down_share * 100)}%</dd>
        </div>
        {typeof analysis.size_percentile === 'number' && (
          <div>
            <dt>과거 대비 순매수 하위</dt>
            <dd>{pctFmt.format(analysis.size_percentile)}%</dd>
          </div>
        )}
        {program && typeof program.non_arb === 'number' && (
          <div>
            <dt>참고: 비차익 프로그램 ({mmdd(program.date)} {program.provisional ? '장중 잠정' : '확정'})</dt>
            <dd>{heroEok(program.non_arb)}</dd>
          </div>
        )}
      </dl>
      {stats && stats.n > 0 && (
        <span className="vr-aside">
          과거 {stats.days}거래일 중 일관 매도 {stats.n}일 · 큰 일관 매도 {stats.n_large}일 이후 다음 거래일 평균{' '}
          {signedPct(stats.next_day_avg_pct)}, 상승 {typeof stats.next_day_up_pct === 'number' ? pctFmt.format(stats.next_day_up_pct) : '-'}% (전체 평균{' '}
          {signedPct(stats.baseline?.next_day_avg_pct)}, 상승{' '}
          {typeof stats.baseline?.next_day_up_pct === 'number' ? pctFmt.format(stats.baseline.next_day_up_pct) : '-'}%)
        </span>
      )}
    </div>
  )
}

function Interpretation({ data, loading }) {
  const [open, setOpen] = usePersistedOpen('stock.dashboard.flowInterpOpen')
  const [hintOpen, setHintOpen] = useState(false)
  const hasData = data && data.date && data.markets && Object.keys(data.markets).length > 0
  const foreign = (m) => data?.markets?.[m]?.['외국인']
  const anySell = hasData && MARKETS.some((m) => foreign(m)?.label === LABEL_SELL)
  const summary = hasData
    ? MARKETS.map((m) => `${MARKET_NAME[m]} ${foreign(m)?.label ?? '표본 부족'}`).join(' · ')
    : null
  return (
    <div className="ifs-interp">
      <button
        type="button"
        className="ifs-interp-head"
        aria-expanded={open}
        aria-controls="ifs-interp-body"
        disabled={!hasData && !loading}
        onClick={() => setOpen(!open)}
      >
        <span className="ifs-interp-title">
          <span className="ifs-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
          외국인 장중 흐름 해석
        </span>
        <span className={`ifs-interp-summary${anySell ? ' fc-label-sell' : ''}`}>
          {loading ? <Skel w={140} h={14} /> : hasData ? summary : '데이터 없음'}
        </span>
      </button>
      {open && hasData && (
        <div id="ifs-interp-body" className="ifs-interp-body">
          <div className="vr-panels fc-panels">
            {MARKETS.map((m) => (
              <Panel key={m} market={m} analysis={foreign(m)} program={data.program?.[m]} stats={data.history?.[m]} />
            ))}
          </div>
          <div className="ifs-interp-foot">
            <span className="vr-aside">
              {data.market_closed ? `장 마감 · ${mmdd(data.date)} 기준` : `장중 잠정 · ${data.as_of} 기준`}
            </span>
            <button type="button" className="btn btn-link btn-sm" aria-expanded={hintOpen} onClick={() => setHintOpen((v) => !v)}>
              {hintOpen ? '설명 닫기' : '설명'}
            </button>
          </div>
          {hintOpen && (
            <p className="vr-foot">
              10분 단위로 본 외국인 누적 순매수가 하루 종일 거의 한 방향(순매도)으로만 이어지고 중간 반등이 작으면
              {' '}일관 매도, 혼조, 일관 매수로 분류합니다. 일정한 속도로 나눠 파는 바스켓(프로그램) 매도 성격의 날을 가려내려는 것이며
              개별 사유(반대매매·공매도 등)를 구분하지는 않습니다. 비차익 프로그램은 장중엔 잠정치, 20:05 이후 확정치입니다.
              과거 통계는 표본이 작아 참고용입니다 — 매매 신호 아님.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// tiles: [{name, total, kospi, kosdaq, source:'live'|'closed'|'eod', date}] (금액 백만원, 없으면 total undefined)
// liveTime: 'HH:MM' | null (장중 잠정 기준 시각), consistency: GET /api/markets/flow/consistency 본문 | null
export default function InvestorFlowSection({ tiles, liveActive, liveTime, consistency, consistencyEnabled, loading, baseDate, onTileClick }) {
  const hasConsistency = consistency && consistency.markets && Object.keys(consistency.markets).length > 0
  return (
    <>
      <div className="section-title mt-title">
        투자자별 수급 (현물)
        {liveActive && <span className="narr-meta">{liveTime ? ` 장중 잠정 · ${liveTime} 기준` : ' 장중 잠정'}</span>}
      </div>
      <div className="mt-row">
        {tiles.map((t) => {
          const hasValue = typeof t.total === 'number'
          const judged = JUDGED.includes(t.name) && t.source === 'live' && hasConsistency
          const markets = judged
            ? { kospi: consistency.markets.kospi?.[t.name], kosdaq: consistency.markets.kosdaq?.[t.name] }
            : null
          return (
            <MetricTile
              key={t.name}
              label={t.name}
              dot={INVESTOR_COLOR_VAR[t.name]}
              loading={!hasValue && loading}
              value={hasValue ? heroEok(t.total) : null}
              dir={hasValue ? (t.total > 0 ? 'up' : t.total < 0 ? 'down' : '') : undefined}
              delta={hasValue ? (t.total > 0 ? '순매수' : t.total < 0 ? '순매도' : '보합') : undefined}
              sub={
                t.source === 'live' ? (
                  '합계 (코스피+코스닥)'
                ) : (
                  <>
                    합계 · 확정
                    <StaleDate date={t.date} baseDate={baseDate} prefix=" · " />
                  </>
                )
              }
              extra={
                <>
                  <span className="ifs-lines">
                    <SplitLine name="코스피" value={t.kospi} />
                    <SplitLine name="코스닥" value={t.kosdaq} />
                  </span>
                  {markets && <JudgeChips markets={markets} />}
                </>
              }
              title={t.source !== 'live' && t.date ? t.date : undefined}
              onClick={onTileClick}
            />
          )
        })}
      </div>
      {consistencyEnabled && <Interpretation data={consistency} loading={consistency === null && loading} />}
    </>
  )
}
