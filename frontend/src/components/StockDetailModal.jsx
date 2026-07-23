import { useEffect, useMemo, useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { STATIC_DATA, fetchStockIntraday, fetchStockSeries, fetchStockSignals } from '../api'
import { DEFAULT_INVESTORS, INTRADAY_OPTIONS, INVESTOR_COLOR_VAR } from '../constants'
import { formatDate, formatEok } from '../format'
import Badge from './Badge'
import CandleChart from './CandleChart'
import PeriodPicker from './PeriodPicker'

const numFmt = new Intl.NumberFormat('ko-KR')

const DEFAULT_STOCK_DAYS = 90

// 종목 상세 모달 기본 기간 — PLAN.md §6 3.7-2 "기간 선택(PeriodPicker, 기본 3M)".
function rateClass(rate) {
  if (rate === null || rate === undefined) return ''
  return rate > 0 ? 'up' : rate < 0 ? 'down' : ''
}

function rateLabel(rate) {
  if (rate === null || rate === undefined) return '-'
  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

// net_value/cum_net_value는 market_flow와 동일하게 백만원 단위로 내려온다
// (FlowChart.jsx eok() 관례 그대로) — 백만원 ÷ 100 = 억원. 소액(|1억원| 미만) 표시는
// format.js의 formatEok 공용 유틸에 위임한다(사용자 피드백: 중소형주 기관 순매수가
// 정수 반올림 탓에 "0억원"으로 뭉개져 데이터가 없는 것처럼 보였다).
const eok = formatEok

function eokClass(v) {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'up' : v < 0 ? 'down' : ''
}

function dateLabel(iso) {
  const digits = String(iso).replaceAll('-', '')
  return `${digits.slice(4, 6)}/${digits.slice(6, 8)}`
}

// MM-DD만 뽑는다(DashboardPage.jsx의 mmdd/StaleDate와 동일한 관례) — 회전율 기준일이
// 가격 기준일과 다를 때 작게 구분 표시하는 용도.
function mmdd(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? d.slice(5) : d
}

// 오늘(브라우저 로컬 날짜)과 같은 날짜 문자열인지 — 개인 수급 "집계 중" 판정용.
function isToday(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 && d === formatDate(new Date())
}

// 개인 수급 "집계 중" 판정 (PLAN.md §5.16-1): 실측 결과 오늘 날짜의 개인(ind_invsr)만
// 소스 자체가 0을 반환하는 구조적 특성이 있다(파싱 버그 아님) — 코드로 값을 억지로
// 채우지 않고, 조건을 만족할 때만 "0원" 대신 "집계 중"이라고 정직하게 구분해서 보여준다.
// 조건: (1) 이 타일이 "개인"이고 (2) 최신 행 날짜가 오늘이며 (3) net_value가 정확히
// 0인데 (4) 같은 날짜의 외국인 또는 기관계 중 하나라도 0이 아닌 값이 있을 때만 —
// 그 외(다른 투자자군도 다 0이거나 오늘 날짜가 아님)는 진짜 0원이므로 그대로 둔다.
function isPendingPersonalFlow(name, latest, flows) {
  if (name !== '개인' || !latest) return false
  if (latest.net_value !== 0 || !isToday(latest.date)) return false
  return DEFAULT_INVESTORS.some((other) => {
    if (other === '개인') return false
    const rows = flows?.[other] || []
    const otherLatest = rows.length > 0 ? rows[rows.length - 1] : null
    return (
      otherLatest &&
      otherLatest.net_value !== 0 &&
      otherLatest.net_value !== null &&
      formatDate(otherLatest.date) === formatDate(latest.date)
    )
  })
}

function flowLineTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      {DEFAULT_INVESTORS.map((name) => {
        const entry = payload.find((p) => p.dataKey === name)
        if (!entry || entry.value === undefined || entry.value === null) return null
        return (
          <div className="tooltip-row" key={name}>
            <span>
              <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
            </span>
            <strong className={eokClass(entry.value)}>{eok(entry.value)}</strong>
          </div>
        )
      })}
    </div>
  )
}

// 투자자별 누적 순매수 콤보 라인차트 — FlowChart.jsx는 투자자당 막대+라인 2패널을
// 세로로 쌓는 무거운 그리드라 모달에는 과하다(작업 지시). 여기서는 기본 3분류
// (개인/외국인/기관계)만 한 차트에 겹쳐 "누적 수급 추세"만 가볍게 보여준다.
function FlowLineChart({ flows }) {
  const byDate = new Map()
  for (const name of DEFAULT_INVESTORS) {
    for (const row of flows?.[name] || []) {
      const entry = byDate.get(row.date) || { date: row.date, label: dateLabel(row.date) }
      entry[name] = row.cum_net_value
      byDate.set(row.date, entry)
    }
  }
  const data = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1))

  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
        <XAxis dataKey="label" hide />
        <ReferenceLine y={0} stroke="var(--axis)" strokeDasharray="3 3" />
        <Tooltip content={flowLineTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
        {DEFAULT_INVESTORS.map((name) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            stroke={INVESTOR_COLOR_VAR[name]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            activeDot={{ r: 3 }}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function FlowStatTiles({ flows }) {
  return (
    <div className="stock-detail-flow-tiles">
      {DEFAULT_INVESTORS.map((name) => {
        const rows = flows?.[name] || []
        const latest = rows.length > 0 ? rows[rows.length - 1] : null
        const pending = isPendingPersonalFlow(name, latest, flows)
        return (
          <div className="stock-detail-flow-tile" key={name}>
            <span className="stock-detail-flow-tile-label">
              <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
            </span>
            {pending ? (
              <span
                className="stock-detail-flow-tile-value stock-detail-flow-tile-pending"
                title="개인 수급은 장중 소스 집계가 늦어 당일 값이 아직 반영되지 않았습니다"
              >
                집계 중
              </span>
            ) : (
              <span className={`stock-detail-flow-tile-value ${eokClass(latest?.net_value)}`}>
                {latest ? eok(latest.net_value) : '-'}
              </span>
            )}
            <span className={`stock-detail-flow-tile-sub ${eokClass(latest?.cum_net_value)}`}>
              기간 누적 {latest ? eok(latest.cum_net_value) : '-'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// 분봉으로 캔들 위에 겹칠 VWAP "라인"(누적 곡선)을 만든다 — 백엔드
// GET /{code}/signals는 "지금까지 누적" 최종값 하나(vwap.value)만 주므로(§5.3
// 배지 문구용), 곡선 자체는 이미 화면에 있는 intradayBars로 프런트에서 봉마다
// 누적 계산한다. 전형가격(고+저+종가)/3 가중평균 — backend app/quant/signals.py
// compute_vwap과 동일한 산식을 그대로 미러링(시각화 전용, 배지 수치의 근거는
// 항상 서버 signals 응답 쪽을 쓴다).
function computeVwapCurve(bars) {
  let cumPv = 0
  let cumVol = 0
  const out = []
  for (const b of bars || []) {
    if (b.high == null || b.low == null || b.close == null) continue
    const typical = (b.high + b.low + b.close) / 3
    const vol = b.volume ?? 0
    cumPv += typical * vol
    cumVol += vol
    out.push({ timestamp: b.timestamp, value: cumVol > 0 ? cumPv / cumVol : null })
  }
  return out
}

function fmtSignedPct1(v) {
  if (v === null || v === undefined) return null
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

// 시그널 응답 -> 서술형 배지 목록(PLAN.md §5.3 "전부 관찰 서술, 지시 문구 금지").
// 계산 불가/중립("none") 상태는 배지를 아예 안 만든다 — "관측된 사실"만 나열한다는
// 원칙상 "아무 일 없음"까지 배지로 나열하면 오히려 신호처럼 읽힐 수 있어서다.
function buildSignalBadges(signals) {
  if (!signals) return []
  const badges = []

  const dev = signals.vwap?.deviation_pct
  if (dev !== null && dev !== undefined) {
    const side = dev >= 0 ? 'VWAP 상단' : 'VWAP 하단'
    badges.push({ key: 'vwap', label: `${side} ${fmtSignedPct1(dev)}` })
  }

  const dir = signals.breakout?.direction
  if (dir === 'high') badges.push({ key: 'breakout', label: '당일 신고가 돌파' })
  else if (dir === 'low') badges.push({ key: 'breakout', label: '당일 신저가 돌파' })

  const cross = signals.ma_cross?.state
  if (cross === 'golden') badges.push({ key: 'ma_cross', label: '골든크로스 (5분/20분)' })
  else if (cross === 'dead') badges.push({ key: 'ma_cross', label: '데드크로스 (5분/20분)' })

  if (signals.volume_spike?.is_spike && signals.volume_spike?.ratio != null) {
    badges.push({ key: 'volume_spike', label: `거래량 급증 ${signals.volume_spike.ratio.toFixed(1)}배` })
  }

  const mom = signals.momentum?.return_pct
  const win = signals.momentum?.window_minutes
  if (mom !== null && mom !== undefined && win) {
    badges.push({ key: 'momentum', label: `${win}분 모멘텀 ${fmtSignedPct1(mom)}` })
  }

  return badges
}

// 분봉 모드 전용 "시그널" 섹션(PLAN.md §5.3) — VWAP 오버레이는 CandleChart가 그리고,
// 여기서는 서술형 배지 목록 + 고정 안내 문구만 담당한다. 일봉 모드에서는 아예
// 렌더링하지 않는다(§5.4 "일봉 모드엔 시그널 섹션 숨김" — 호출부에서 조건부 렌더).
function SignalPanel({ loading, error, signals }) {
  if (loading) return <div className="state">시그널 계산 중…</div>
  if (error) return <div className="state error">시그널 불러오기 실패</div>
  const badges = buildSignalBadges(signals)
  return (
    <div className="stock-detail-signals">
      <div className="stock-detail-signals-badges">
        {badges.length > 0 ? (
          badges.map((b) => (
            <Badge kind="info" key={b.key}>
              {b.label}
            </Badge>
          ))
        ) : (
          <span className="stock-detail-signals-empty">특이 시그널 없음</span>
        )}
      </div>
      <div className="stock-detail-signals-disclaimer">참고용 기술적 관찰 — 매매 신호 아님</div>
    </div>
  )
}

// 종목 상세 모달 (PLAN.md §6 3.7-2) — StockSearch에서 종목을 고르면 뜬다. initial은
// 검색 결과 행({code, name, market, is_etf})을 그대로 넘겨받아, 헤더(이름/코드/배지)를
// 시리즈 응답을 기다리지 않고 즉시 그릴 수 있게 한다 — 첫 조회는 외부 API를 거쳐
// ~1.5초 걸리므로(작업 지시) 헤더가 그 사이 빈 화면으로 보이는 것을 막는다. Modal.jsx가
// open=false일 때 children을 아예 리컨사일하지 않으므로, 이 컴포넌트의 useEffect는
// 모달이 실제로 열릴 때(마운트 시)만 실행된다 — 열 때마다 최신 데이터를 새로 받는다.
export default function StockDetailModal({ code, initial }) {
  const [days, setDays] = useState(DEFAULT_STOCK_DAYS)
  const [series, setSeries] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // 분봉 토글(PLAN.md §5.1) — 'daily'면 기존 일봉 로직 그대로, 정수 분이면 아래
  // intraday state가 CandleChart를 대체한다(오늘 하루치만, DB 미저장 온디맨드).
  const [intradayMode, setIntradayMode] = useState('daily')
  const [intradayBars, setIntradayBars] = useState([])
  const [intradayDate, setIntradayDate] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)
  // 진입 타이밍 시그널(PLAN.md §5.3) — 분봉 모드에서만 별도 요청. intraday 캔들과
  // 독립된 API(GET /{code}/signals)라 로딩 상태도 따로 둔다(캔들은 배지 없이도
  // 먼저 그려질 수 있게).
  const [signals, setSignals] = useState(null)
  const [signalsLoading, setSignalsLoading] = useState(false)
  const [signalsError, setSignalsError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchStockSeries(code, days)
      .then((body) => {
        if (!cancelled) setSeries(body)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [code, days])

  useEffect(() => {
    if (STATIC_DATA || intradayMode === 'daily') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchStockIntraday(code, intradayMode)
      .then((body) => {
        if (!cancelled) {
          setIntradayBars(body.bars || [])
          setIntradayDate(body.date)
        }
      })
      .catch((e) => {
        if (!cancelled) setIntradayError(e.message)
      })
      .finally(() => {
        if (!cancelled) setIntradayLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [code, intradayMode])

  useEffect(() => {
    if (STATIC_DATA || intradayMode === 'daily') {
      setSignals(null)
      return undefined
    }
    let cancelled = false
    setSignalsLoading(true)
    setSignalsError(null)
    fetchStockSignals(code, intradayMode)
      .then((body) => {
        if (!cancelled) setSignals(body)
      })
      .catch((e) => {
        if (!cancelled) setSignalsError(e.message)
      })
      .finally(() => {
        if (!cancelled) setSignalsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [code, intradayMode])

  // 모달을 닫았다 다른 종목으로 다시 열 때 이전 종목의 분봉 모드가 남아있지 않도록
  // — code가 바뀌면 항상 일봉으로 되돌린다(사용자가 매번 다시 고르게 함, 놀람 방지).
  useEffect(() => {
    setIntradayMode('daily')
  }, [code])

  const name = series?.name || initial?.name
  const market = series?.market || initial?.market
  const isEtf = series?.is_etf ?? initial?.is_etf
  const prices = series?.prices || []
  const latestPrice = prices.length > 0 ? prices[prices.length - 1] : null
  const flowsError = series?.meta?.flows_error
  const hasFlows = !flowsError && series?.flows && Object.keys(series.flows).length > 0
  const vwapCurve = useMemo(() => computeVwapCurve(intradayBars), [intradayBars])

  return (
    <div className="stock-detail">
      <div className="stock-detail-header">
        <span className="stock-detail-name">{name}</span>
        <span className="stock-detail-code">{code}</span>
        {market && <Badge kind={market.toLowerCase()} />}
        {isEtf && <Badge kind="etf" />}
        {latestPrice && (
          <span className="stock-detail-price">
            {numFmt.format(latestPrice.close)}
            <span className={`stock-detail-rate ${rateClass(latestPrice.changeRate)}`}>
              {rateLabel(latestPrice.changeRate)}
            </span>
          </span>
        )}
        {series?.turnover && (
          <span className="stock-detail-turnover">
            회전율 {series.turnover.value.toFixed(2)}%
            {latestPrice && formatDate(series.turnover.date) !== formatDate(latestPrice.date) && (
              <span className="stale-date" title={formatDate(series.turnover.date)}>
                {' '}
                {mmdd(series.turnover.date)}
              </span>
            )}
          </span>
        )}
      </div>

      {!STATIC_DATA && (
        <div className="toggle-row">
          {INTRADAY_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              className={`toggle-chip ${intradayMode === opt.key ? 'active' : ''}`}
              onClick={() => setIntradayMode(opt.key)}
            >
              {opt.label}
            </button>
          ))}
          <span className="toggle-hint">
            {intradayMode === 'daily' ? '분봉은 오늘 하루치만 제공' : '오늘 하루치 · 참고용'}
          </span>
        </div>
      )}

      {intradayMode === 'daily' && <PeriodPicker value={days} onChange={setDays} />}

      {intradayMode === 'daily' && (
        <>
          {loading && <div className="state">불러오는 중…</div>}
          {error && <div className="state error">{error}</div>}
          {!loading && !error && prices.length > 0 && <CandleChart data={prices} height={280} />}
          {!loading && !error && prices.length === 0 && (
            <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
          )}
        </>
      )}

      {intradayMode !== 'daily' && (
        <>
          {intradayLoading && <div className="state">불러오는 중…</div>}
          {intradayError && <div className="state error">{intradayError}</div>}
          {!intradayLoading && !intradayError && intradayBars.length === 0 && (
            <div className="state">오늘 분봉 데이터가 없습니다(장 시작 전이거나 휴장일 수 있음).</div>
          )}
          {!intradayLoading && !intradayError && intradayBars.length > 0 && (
            <>
              <CandleChart
                key={`${code}-${intradayMode}`}
                data={intradayBars}
                height={280}
                intraday
                title={`캔들 · 거래량 (${intradayMode}분봉 · ${formatDate(intradayDate)})`}
                overlay={vwapCurve}
                overlayLabel="VWAP"
              />
              <SignalPanel loading={signalsLoading} error={signalsError} signals={signals} />
            </>
          )}
        </>
      )}

      {!loading && !error && flowsError && <div className="state">수급 일시 불가</div>}
      {!loading && !error && !flowsError && hasFlows && (
        <div className="stock-detail-flow">
          <FlowStatTiles flows={series.flows} />
          <FlowLineChart flows={series.flows} />
        </div>
      )}
    </div>
  )
}
