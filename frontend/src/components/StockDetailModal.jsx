import { useEffect, useMemo, useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { STATIC_DATA, fetchEtfWeightChanges, fetchStockIntraday, fetchStockSeries, fetchStockSignals } from '../api'
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

function etfWeightLabel(v) {
  return v === null || v === undefined ? '–' : `${v.toFixed(2)}%`
}

// "이 종목을 담은 ETF 중 최근 비중 변화" (PLAN.md §5.25) — 종목 상세 모달 전용
// 섹션. 이 파일의 다른 옵셔널 섹션(turnover 배지, 수급 섹션 등)과 동일하게 데이터가
// 없으면(fetch 실패 포함) 섹션 헤더/빈 상태 문구 없이 통째로 렌더하지 않는다("표시할
// 게 없으면 아예 안 보여준다"는 이 파일의 기존 관례를 그대로 따름). etf_holdings가
// top10 스냅샷이라는 한계(신규편입/편출이 top10 안팎 이동일 수 있음, 매수/매도 확정
// 아님)는 END USER가 코드 주석만으로는 알 수 없으므로 toggle-hint로 화면에 항상
// 노출한다(작업 지시 — "코드 주석에만 묻지 말 것").
function EtfWeightChangeSection({ data }) {
  const changes = data?.changes || []
  if (changes.length === 0) return null

  return (
    <div className="stock-detail-etf-weight">
      <div className="toggle-hint">
        담은 ETF 중 최근 비중 변화 ({formatDate(data.prev_date)} → {formatDate(data.curr_date)}) · etf_holdings는 각
        ETF의 top10 구성만 매일 스냅샷이라 "신규편입"/"편출"이 top10 안팎 이동일 수 있습니다 — 실제 매수/매도가
        시작됐다는 뜻은 아닙니다.
      </div>
      <ul className="stock-detail-etf-weight-list">
        {changes.map((c, i) => (
          <li className="stock-detail-etf-weight-row" key={`${c.etf_code}-${i}`}>
            <span className="stock-detail-etf-weight-name">
              {c.etf_name || c.etf_code}
              {c.is_active && <Badge kind="info">액티브</Badge>}
            </span>
            <span
              className={`etf-weight-event ${c.event === '비중확대' || c.event === '신규편입' ? 'up' : 'down'}`}
            >
              {c.event}
            </span>
            <span className="stock-detail-etf-weight-value">
              {etfWeightLabel(c.prev_weight)} → {etfWeightLabel(c.curr_weight)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// 종목별 프로그램매매(PLAN.md §5.29-4) — series 응답에 이미 포함돼 있어(§5.29-3,
// /{code}/series의 program_trade) EtfWeightChangeSection과 달리 별도 fetch가
// 필요 없다. ka90013은 차익/비차익 분리를 주지 않고 총계(total_net)만 준다
// (clients/kiwoom.py "ka90013 실측 확정" 절, collectors/program_stock.py 모듈
// docstring 참고) — 대시보드의 "프로그램 차익/비차익 순매수" 타일(시장 전체
// 집계, ka90010)과 소스/집계 단위가 다르다는 걸 오해하지 않도록 toggle-hint로
// 명시한다. EtfWeightChangeSection과 동일한 관례로, 데이터가 없으면(신규 종목이라
// 아직 백필 전이거나 조회 실패) 섹션 자체를 렌더하지 않는다.
function ProgramTradeSection({ rows }) {
  const withData = (rows || []).filter((r) => r.total_net !== null && r.total_net !== undefined)
  if (withData.length === 0) return null

  const recent = withData.slice(-10).reverse() // 최신이 위로 오게

  return (
    <div className="stock-detail-program-trade">
      <div className="toggle-hint">
        프로그램매매 순매수(최근 {recent.length}거래일) · 이 종목별 데이터(ka90013)는 차익/비차익 세부 구분 없이
        합계만 제공됩니다 — 위 대시보드의 "프로그램 차익/비차익 순매수" 타일은 시장 전체 집계(ka90010)로 별개
        소스입니다.
      </div>
      <ul className="stock-detail-program-trade-list">
        {recent.map((r) => (
          <li className="stock-detail-program-trade-row" key={r.date}>
            <span className="stock-detail-program-trade-date">{mmdd(r.date)}</span>
            <span className={`stock-detail-program-trade-value ${eokClass(r.total_net)}`}>
              {eok(r.total_net)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// 종목별 공매도(PLAN.md §5.32) — series 응답에 이미 포함돼 있어(§5.32-3,
// /{code}/series의 short_selling) EtfWeightChangeSection과 달리 별도 fetch가
// 필요 없다. ProgramTradeSection과 동일한 "row-list, 최근 10거래일, 데이터
// 없으면 섹션 자체를 렌더하지 않음" 관례를 그대로 따르되, 값이 순매수(부호 있음)가
// 아니라 거래대금(항상 양수)이라 up/down 색상을 붙이지 않는다. 순보유잔고는
// 보고의무 발생 종목만 T+2 지연으로 채워지므로(clients/krx_short_selling.py 모듈
// docstring) 있는 행만 부가로 보여준다 — 대시보드의 "대차잔고"/"공매도 비중"
// 타일(시장 전체 집계)과 소스·범위가 다르다는 걸 오해하지 않도록 toggle-hint로
// 명시한다.
function ShortSellingSection({ rows }) {
  const withData = (rows || []).filter((r) => r.value !== null && r.value !== undefined)
  if (withData.length === 0) return null

  const recent = withData.slice(-10).reverse() // 최신이 위로 오게

  return (
    <div className="stock-detail-short-selling">
      <div className="toggle-hint">
        공매도 거래대금(최근 {recent.length}거래일) · KRX 정보데이터시스템 실측 — 위 대시보드의 "대차잔고"/"공매도
        비중" 타일(시장 전체 집계)과는 별개로 이 종목의 실제 공매도 거래입니다. 순보유잔고는 보고의무가 발생한
        경우만 2거래일 지연으로 표시됩니다.
      </div>
      <ul className="stock-detail-short-selling-list">
        {recent.map((r) => (
          <li className="stock-detail-short-selling-row" key={r.date}>
            <span className="stock-detail-short-selling-date">{mmdd(r.date)}</span>
            <span className="stock-detail-short-selling-value">{eok(r.value)}</span>
            <span className="stock-detail-short-selling-balance">
              {r.balance_value !== null && r.balance_value !== undefined ? `잔고 ${eok(r.balance_value)}` : ''}
            </span>
          </li>
        ))}
      </ul>
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
  // ETF 비중 변화(PLAN.md §5.25) — "이 종목을 담은 ETF 중 최근 비중이 바뀐 곳".
  // isEtf(아래에서 계산)가 true인 동안은 조회 자체를 건너뛴다 — ETF 자신이 ETF에
  // 담기는 경우는 이 기능의 대상이 아니다(작업 지시 그대로).
  const [etfWeightChanges, setEtfWeightChanges] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    // initial(검색/랭킹 카드가 이미 갖고 있던 {name, market, is_etf})을 힌트로
    // 넘긴다 — stocks 마스터에 아직 없는 종목(§5.28, 예: 0156T0)이면 백엔드가
    // 이 힌트로 stub 마스터 행을 채운다(없으면 name=code/market=KOSPI 기본값).
    fetchStockSeries(code, days, { name: initial?.name, market: initial?.market, is_etf: initial?.is_etf })
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

  // isEtf는 series(비동기)가 오기 전에는 initial(검색/랭킹 행이 미리 넘겨준 값)로
  // 먼저 판단할 수 있다 — series?.is_etf가 나중에 도착해도 Boolean 값이 바뀌지
  // 않으면(대개 그렇다) 아래 effect가 다시 실행되지 않는다.
  const isEtfNow = Boolean(series?.is_etf ?? initial?.is_etf)

  // ETF 비중 변화(PLAN.md §5.25) — ETF 자신을 보는 중이면(isEtfNow) 애초에 "이
  // 종목을 담은 ETF" 조회가 의미 없으므로 요청 자체를 보내지 않는다.
  useEffect(() => {
    if (isEtfNow) {
      setEtfWeightChanges(null)
      return undefined
    }
    let cancelled = false
    fetchEtfWeightChanges({ code })
      .then((body) => {
        if (!cancelled) setEtfWeightChanges(body)
      })
      .catch(() => {
        if (!cancelled) setEtfWeightChanges(null)
      })
    return () => {
      cancelled = true
    }
  }, [code, isEtfNow])

  const name = series?.name || initial?.name
  const market = series?.market || initial?.market
  const isEtf = isEtfNow
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

      <ProgramTradeSection rows={series?.program_trade} />

      <ShortSellingSection rows={series?.short_selling} />

      {!isEtf && <EtfWeightChangeSection data={etfWeightChanges} />}
    </div>
  )
}
