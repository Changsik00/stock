import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchAttention,
  fetchBasis,
  fetchBreadth,
  fetchBreadthLive,
  fetchDerivativeFlow,
  fetchFlowLive,
  fetchFlowPath,
  fetchFlowRank,
  fetchGroups,
  fetchMacroSeries,
  fetchMarketSeries,
  fetchSentiment,
  fetchValueRank,
} from '../api'
import Badge from '../components/Badge'
import BreadthBadge from '../components/BreadthBadge'
import CandleChart from '../components/CandleChart'
import EtfDirectionCard from '../components/EtfDirectionCard'
import ForeignPositionChart from '../components/ForeignPositionChart'
import FlowChart from '../components/FlowChart'
import FlowPathTable from '../components/FlowPathTable'
import FlowRankTable from '../components/FlowRankTable'
import GroupTreemap from '../components/GroupTreemap'
import MacroChart from '../components/MacroChart'
import MarketFundChart from '../components/MarketFundChart'
import Modal from '../components/Modal'
import PeriodPicker from '../components/PeriodPicker'
import SentimentGauge from '../components/SentimentGauge'
import StockDetailModal from '../components/StockDetailModal'
import StockSearch from '../components/StockSearch'
import ValueRankTable from '../components/ValueRankTable'
import { DEFAULT_INVESTORS, INVESTOR_COLOR_VAR, MACRO_SERIES, MARKETS, MARKET_FUND_IDS } from '../constants'
import { formatDate } from '../format'

// 대시보드 탭 (PLAN.md §6 3.7-1, 사용자 원문: "장황하게 정보가 노출됨. 디테일은 뒤로
// 숨기고 핵심 숫자만. 차트는 모달/탭으로. 100개짜리 리스트도 뒤로.") — 기본 탭.
// 시장 탭(MarketPage)의 상세 컨트롤(투자자·기간·시장 토글이 있는 표/차트 전부)을
// 그대로 옮기지 않고, 여기서는 "핵심 숫자만" 골라 타일로 보여준 뒤 클릭 시에만 그
// 상세(차트/전체 리스트)를 모달로 연다. 데이터는 전부 기존 api.js 함수를 그대로
// 재사용한다 — 이 작업에서 백엔드/api.js는 건드리지 않는다(병렬 작업 중인 백엔드
// 검색/종목 API와 충돌 방지, PLAN.md §6 3.7-1 작업 지시).

const numFmt = new Intl.NumberFormat('ko-KR')
const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const joFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const scoreFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const countFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
// 환율(원/달러 1자리)·유가(달러 2자리) 타일 포맷 — 매크로 탭 통합(환율/WTI 타일 +
// 모달, 사용자 원문: "환율·유가 2~3개 차트만으로 탭 하나는 과하다").
const fxFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const oilFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
// 베이시스(K200 선물-현물, pt) 타일 포맷 — PLAN.md §4.5-3/-5.
const basisFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

// KPI 타일 초기 캔들 모달 기본 기간 — MarketPage와 동일하게 90일(3M)에서 시작한다.
const DEFAULT_CANDLE_DAYS = 90
// 자금(예탁금/대차잔고/신용융자) 모달 차트 기본 기간 — 추세를 보려면 90일보다 넉넉해야
// 자연스럽다.
const DEFAULT_FUND_DAYS = 180
// 매크로(환율/유가) 모달 차트 기본 기간 — 옛 MacroPage.jsx와 동일하게 1Y로 시작한다
// (환율/유가는 자금 지표보다 변동 주기가 길어 1년 창이 자연스럽다는 기존 판단 유지).
const DEFAULT_MACRO_DAYS = 365
// 환율/WTI 타일의 최신값·전일비 계산용 — 최근 2거래일만 있으면 되므로 짧게 잡는다
// (fundSeries가 fundLatest/fundPrev용으로 10일을 쓰는 관례와 동일).
const MACRO_TILE_DAYS = 10
// 투자자별 수급 요약 타일 + 모달 — 시장 탭과 동일하게 3M 기본.
const DEFAULT_FLOW_DAYS = 90
// 외인 양손(현물·선물·베이시스) 섹션 — PLAN.md §4.5-5. 타일 최신값 계산용으로는
// 짧은 창(10일)이면 충분하고(마지막 값 + 전일 비교), 상세 모달(ForeignPositionModal)은
// 시장 탭과 동일하게 90일 기본으로 시작한다.
const FOREIGN_POSITION_TILE_DAYS = 10
const DEFAULT_FOREIGN_POSITION_DAYS = 90
// 프로그램매매 차익 순매수(macro_series prog_arb_*, PLAN.md §4.5-4) — 코스피+코스닥
// 합산해서 "프로그램 차익 순매수" 타일 하나로 보여준다(신용융자 타일의 creditLoanSum과
// 동일한 관례).
const PROGRAM_ARB_IDS = ['prog_arb_kospi', 'prog_arb_kosdaq']
// 만기 임박 시그널 배지 기준(D-n 이내, PLAN.md §4.5-5 "만기 D-3 이내").
const EXPIRY_SOON_D_DAY = 3
const GROUP_TYPE_OPTIONS = [
  { key: 'upjong', label: '업종' },
  { key: 'theme', label: '테마' },
]
const VALUE_RANK_MARKET_OPTIONS = [
  { key: 'all', label: '전체' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]
const FLOW_RANK_LOOKBACK_DAYS = 7
// 등락 종목수(breadth/live) 자동 갱신 주기 — 백엔드 60초 캐시(routers/markets.py
// GET /api/markets/breadth/live)와 맞춘다. flow/live·attention과 동일한 값이지만
// 소스가 달라 독립 폴링을 쓴다. 2026-07-20까지는 이 값을 쓰는 두 useEffect
// (DashboardPage 본문 + BreadthModal) 모두 최초 1회만 fetch하고 재폴링이 없어서
// 아무도 요청하지 않으면 화면이 멈춰 있는 버그가 있었다 — 서버 측 능동 60초 갱신
// 작업(PLAN.md)과 함께 수정.
const BREADTH_LIVE_POLL_MS = 60_000
// 장중 잠정 수급(PLAN.md §6 3.7-3) 자동 갱신 주기 — 백엔드 60초 캐시(routers/markets.py
// GET /api/markets/flow/live)와 맞춘다. 그보다 짧게 폴링해도 캐시 히트라 낭비만 커진다.
const FLOW_LIVE_POLL_MS = 60_000
// 실시간 관심 종목 TOP20 자동 갱신 주기 — 백엔드 60초 캐시(routers/markets.py
// GET /api/markets/attention)와 맞춘다. FLOW_LIVE_POLL_MS와 동일한 값이지만 소스가
// 달라(플로우 vs 조회수 순위) 각자 독립 폴링/캐시를 갖는다.
const ATTENTION_POLL_MS = 60_000

function eokLabel(million) {
  if (typeof million !== 'number') return '-'
  return `${eokFmt.format(million / 100)}억원`
}

function trillion(million) {
  if (million === null || million === undefined) return null
  return million / 1e6
}

function trillionLabel(million) {
  const t = trillion(million)
  return t === null ? '-' : `${joFmt.format(t)}조`
}

function fxLabel(value) {
  if (typeof value !== 'number') return '-'
  return `${fxFmt.format(value)}원`
}

function oilLabel(value) {
  if (typeof value !== 'number') return '-'
  return `$${oilFmt.format(value)}`
}

function basisLabel(value) {
  if (typeof value !== 'number') return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${basisFmt.format(value)}pt`
}

function scoreClass(score) {
  if (score === null || score === undefined) return ''
  if (score > 2) return 'up'
  if (score < -2) return 'down'
  return 'flat'
}

function scoreLabel(score) {
  if (score === null || score === undefined) return '-'
  const sign = score > 0 ? '+' : ''
  return `${sign}${scoreFmt.format(score)}`
}

function rateClass(rate) {
  if (rate === null || rate === undefined) return ''
  return rate > 0 ? 'up' : rate < 0 ? 'down' : ''
}

function rateLabel(rate) {
  if (rate === null || rate === undefined) return '-'
  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

// MM-DD만 뽑는다 (StaleDate/TOP5 "…기준" 라벨 공용) — formatDate가 이미
// 'YYYY-MM-DD'로 정규화하므로 뒤 5글자만 자르면 된다.
function mmdd(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? d.slice(5) : d
}

// 여러 후보 날짜 중 최신값 — 대표 기준일(대시보드 상단 1회 표시) 계산용.
// 소스마다 'YYYYMMDD'/'YYYY-MM-DD'가 섞여 올 수 있어 formatDate로 정규화한 뒤
// 문자열 비교한다(정규화된 'YYYY-MM-DD'는 사전순 비교 = 날짜순 비교).
function latestOf(...rawDates) {
  const normalized = rawDates.map((d) => formatDate(d)).filter((d) => typeof d === 'string' && d.length === 10)
  if (normalized.length === 0) return null
  return normalized.reduce((a, b) => (b > a ? b : a))
}

// 대표 기준일(baseDate)보다 뒤처진 타일에만 붙는 작은 회색 날짜(MM-DD) — "뒤처짐" 신호.
// 사용자 피드백: "타일마다 날짜가 있는데 중복이다. 어차피 마지막 거래일일 텐데" —
// 실제로는 소스별 시차가 있어 다를 수 있으므로, 최신인 타일은 아무것도 표시하지
// 않고(대표 기준일과 같다고 간주) 뒤처진 타일에만 예외적으로 이 배지를 남긴다.
// 정확한 날짜는 항상 타일의 title 속성(hover)으로 확인 가능하다.
function StaleDate({ date, baseDate, prefix = '' }) {
  const d = formatDate(date)
  if (typeof d !== 'string' || d.length !== 10 || !baseDate || d >= baseDate) return null
  return (
    <span className="stale-date" title={d}>
      {prefix}
      {mmdd(date)}
    </span>
  )
}

// TOP5 카드 "…기준" 라벨 — 대표 기준일과 같으면 생략(중복), 다르면 MM-DD만 붙인다.
// suffix가 있으면(예: ETF 경유 상위의 "유입") 날짜가 없을 때도 suffix만 별도로
// 붙일 수 있도록 호출부에서 처리한다(이 함수는 "뒤처졌을 때"만 문자열을 낸다).
function staleHintLabel(date, baseDate, suffix) {
  const d = formatDate(date)
  if (typeof d !== 'string' || d.length !== 10 || !baseDate || d >= baseDate) return null
  return suffix ? `${mmdd(date)} 기준 · ${suffix}` : `${mmdd(date)} 기준`
}

// 전일比 화살표 — prev가 없으면(첫 값) 표시하지 않는다.
// neutral=true면 up/down 색상 클래스를 붙이지 않는다(중립 회색) — 환율 타일 전용.
// 환율 상승이 "좋은 것"이 아니라 주가 등락(빨강=상승/파랑=하락) 관례와 혼동될 수
// 있어, 화살표 방향·값은 그대로 두고 색만 뺀다(다른 타일은 예탁금 타일과 동일하게
// up/down 색을 그대로 쓴다).
function DiffArrow({ current, prev, formatter, neutral = false }) {
  if (current === null || current === undefined || prev === null || prev === undefined) return null
  const diff = current - prev
  if (diff === 0) return <span className="kpi-tile-sub">보합</span>
  const up = diff > 0
  const cls = neutral ? '' : up ? 'up' : 'down'
  return (
    <span className={`kpi-tile-sub ${cls}`}>
      {up ? '▲' : '▼'} {formatter(Math.abs(diff))}
    </span>
  )
}

// 두 시장(코스피/코스닥)의 flows(투자자 -> [{date, net_value, net_volume}])를 투자자·
// 날짜 기준으로 합산한다 — market_flow는 시장별로만 적재되고 백엔드에 "합계" 엔드포인트가
// 없어(routers/markets.py MARKETS={kospi,kosdaq,futures}, FLOW_MARKETS={kospi,kosdaq})
// "시장 종합 수급"을 보여주려면 클라이언트에서 더해야 한다.
function mergeFlows(flowsA, flowsB) {
  const investors = new Set([...Object.keys(flowsA || {}), ...Object.keys(flowsB || {})])
  const merged = {}
  for (const inv of investors) {
    const byDate = new Map()
    for (const arr of [flowsA?.[inv] || [], flowsB?.[inv] || []]) {
      for (const e of arr) {
        const row = byDate.get(e.date) || { date: e.date, net_value: 0, net_volume: 0 }
        row.net_value += e.net_value || 0
        row.net_volume += e.net_volume || 0
        byDate.set(e.date, row)
      }
    }
    merged[inv] = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1))
  }
  return merged
}

// flows(투자자 -> [{date, net_value, net_volume}])에서 특정 투자자의 가장 최근 행을
// 뽑는다 — market_flow 계열 응답을 다루는 여러 곳(외인 현물/선물 타일)에서 공용으로 쓴다.
function latestFlowRow(flows, investor) {
  const rows = flows?.[investor]
  return rows && rows.length > 0 ? rows[rows.length - 1] : null
}

// 차트 X축 라벨 — 'YYYY-MM-DD' -> 'MM/DD' (MacroChart.jsx/MarketFundChart.jsx의
// dateLabel과 동일한 관례, 여기서는 formatDate로 먼저 정규화해 'YYYYMMDD' 등도 대응).
function chartDateLabel(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : d
}

// ---------------------------------------------------------------------------
// KPI 타일 — 클릭 가능한 순수 버튼. 값 자체 계산/포맷은 호출부가 마친 뒤 넘긴다.
// ---------------------------------------------------------------------------
function KpiTile({ label, value, valueClass, sub, onClick, title }) {
  return (
    <button type="button" className="kpi-tile" onClick={onClick} title={title}>
      <span className="kpi-tile-label">{label}</span>
      <span className={`kpi-tile-value ${valueClass || ''}`}>{value}</span>
      {sub}
    </button>
  )
}

// ---------------------------------------------------------------------------
// 모달 본문 컴포넌트 — Modal이 open=false일 때 렌더 트리에서 완전히 빠지므로(Modal.jsx
// 주석 참고) 아래 컴포넌트들은 "마운트될 때(=모달이 열릴 때)"만 데이터를 불러온다.
// MarketPage.jsx의 동일 섹션 로직을 모달 안에서 재현한 것으로, 백엔드 응답 스키마·
// 상태 관리 패턴은 그대로 가져왔다(중복이지만 페이지 단위로 완전히 분리하라는
// 작업 지시에 따라 MarketPage를 import하지 않고 이 파일 안에서 자기완결로 둔다).
// ---------------------------------------------------------------------------

function CandleModal({ market }) {
  const label = MARKETS.find((m) => m.key === market)?.label || market
  const [days, setDays] = useState(DEFAULT_CANDLE_DAYS)
  const [prices, setPrices] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMarketSeries(market, days)
      .then((body) => {
        if (!cancelled) setPrices(body.prices || [])
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
  }, [market, days])

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        {label} · 캔들 + 거래량
      </div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && prices && prices.length > 0 && <CandleChart data={prices} height={320} />}
      {!loading && !error && prices && prices.length === 0 && (
        <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
      )}
    </div>
  )
}

function SentimentModal() {
  const [sentiment, setSentiment] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
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
  }, [])

  return (
    <SentimentGauge
      loading={loading}
      error={error}
      score={sentiment?.score ?? null}
      approx={sentiment?.approx ?? true}
      components={sentiment?.components ?? null}
    />
  )
}

function BreadthModal() {
  const [breadth, setBreadth] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const toCamel = (row) =>
      row
        ? {
            adv: row.adv,
            dec: row.dec,
            flat: row.flat,
            limitUp: row.limit_up ?? row.limitUp,
            limitDown: row.limit_down ?? row.limitDown,
          }
        : null

    async function load() {
      try {
        const body = await fetchBreadthLive()
        if (cancelled) return
        setBreadth({
          kospi: toCamel(body.kospi),
          kosdaq: toCamel(body.kosdaq),
          live: body.live !== false,
          date: body.kospi?.date || body.kosdaq?.date || null,
        })
        return
      } catch {
        // 장중 소스 실패 — 일별 확정치로 폴백 (MarketPage와 동일 패턴).
      }
      try {
        const [kospiBody, kosdaqBody] = await Promise.all([
          fetchBreadth('kospi', 30).catch(() => null),
          fetchBreadth('kosdaq', 30).catch(() => null),
        ])
        if (cancelled) return
        const last = (body) => {
          const series = body?.series
          return series && series.length > 0 ? series[series.length - 1] : null
        }
        const kospiRow = last(kospiBody)
        const kosdaqRow = last(kosdaqBody)
        if (!kospiRow && !kosdaqRow) {
          setError('등락 종목수 데이터를 불러오지 못했습니다.')
          return
        }
        setBreadth({
          kospi: toCamel(kospiRow),
          kosdaq: toCamel(kosdaqRow),
          live: false,
          date: kospiRow?.date || kosdaqRow?.date || null,
        })
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }

    load()
    // 모달이 열려 있는 동안에도 백엔드 60초 캐시와 맞춰 계속 갱신한다 — 이전에는
    // 최초 1회만 fetch하고 끝나서 모달을 오래 띄워둬도 값이 갱신되지 않았다
    // (DashboardPage 본문의 동일 패턴 useEffect와 같은 수정, PLAN.md 서버 측 능동
    // 60초 갱신 작업 참고).
    const intervalId = setInterval(load, BREADTH_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [])

  return (
    <div>
      {breadth && (
        <div className="toggle-hint" style={{ marginBottom: 8 }}>
          등락 종목수 — {breadth.live ? '장중 잠정치 (60초 캐시)' : '일별 확정치'}
        </div>
      )}
      {error && <div className="state error">{error}</div>}
      {breadth && <BreadthBadge kospi={breadth.kospi} kosdaq={breadth.kosdaq} date={breadth.live ? null : breadth.date} />}
      {!breadth && !error && <div className="state">불러오는 중…</div>}
    </div>
  )
}

function FundModal() {
  const [days, setDays] = useState(DEFAULT_FUND_DAYS)
  const [seriesMap, setSeriesMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(MARKET_FUND_IDS, days)
      .then((body) => {
        if (!cancelled) setSeriesMap(body.series || {})
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && <MarketFundChart seriesMap={seriesMap} />}
    </div>
  )
}

// 환율(USD/KRW) · WTI · 브렌트 라인차트 3개 + 기간 선택 — 옛 MacroPage.jsx를 그대로
// 모달로 옮긴 것이다(차트 렌더 로직은 components/MacroChart.jsx로 뽑아 공용화). 타일은
// 환율/WTI 2개만 두지만(브렌트는 타일 생략 지시) 모달에서는 기존과 동일하게 3개 라인을
// 전부 보여준다.
function MacroModal() {
  const [days, setDays] = useState(DEFAULT_MACRO_DAYS)
  const [seriesMap, setSeriesMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(
      MACRO_SERIES.map((s) => s.id),
      days
    )
      .then((body) => {
        if (!cancelled) setSeriesMap(body.series || {})
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (
        <div className="chart-stack">
          {MACRO_SERIES.map((s) => (
            <MacroChart key={s.id} label={s.label} unit={s.unit} points={seriesMap[s.id] || []} />
          ))}
        </div>
      )}
    </div>
  )
}

function FlowSummaryModal() {
  const [days, setDays] = useState(DEFAULT_FLOW_DAYS)
  const [flows, setFlows] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([fetchMarketSeries('kospi', days), fetchMarketSeries('kosdaq', days)])
      .then(([kospiBody, kosdaqBody]) => {
        if (!cancelled) setFlows(mergeFlows(kospiBody.flows, kosdaqBody.flows))
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
  }, [days])

  const hasFlows = Object.keys(flows || {}).length > 0

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        코스피+코스닥 합계 (선물 제외 — 투자자별 수급 미수집)
      </div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && hasFlows && <FlowChart flows={flows} />}
      {!loading && !error && !hasFlows && <div className="state">표시할 데이터가 없습니다.</div>}
    </div>
  )
}

// 외인 양손 상세 — 외인 현물 vs 선물 순매수 시계열 + 베이시스 오버레이(PLAN.md §4.5-5
// 시그널 배지 클릭 시 열리는 모달). 코스피+코스닥(현물) + 선물 + 베이시스 3개 소스를
// 날짜 기준으로 병합한다 — CreditLoanChart(MarketFundChart.jsx)와 동일한 "여러 시리즈를
// Map으로 합친 뒤 라인 여러 개를 겹쳐 그리는" 패턴.
function ForeignPositionModal() {
  const [days, setDays] = useState(DEFAULT_FOREIGN_POSITION_DAYS)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchMarketSeries('kospi', days),
      fetchMarketSeries('kosdaq', days),
      fetchMarketSeries('futures', days),
      fetchBasis(days),
    ])
      .then(([kospiBody, kosdaqBody, futuresBody, basisBody]) => {
        if (cancelled) return
        const spotRows = mergeFlows(kospiBody.flows, kosdaqBody.flows)['외국인'] || []
        const futuresRows = futuresBody.flows?.['외국인'] || []
        const basisRows = basisBody.series || []

        const byDate = new Map()
        const get = (date) => {
          if (!byDate.has(date)) byDate.set(date, { date, label: chartDateLabel(date) })
          return byDate.get(date)
        }
        for (const r of spotRows) get(r.date).spot = (r.net_value ?? 0) / 100
        for (const r of futuresRows) get(r.date).futures = (r.net_value ?? 0) / 100
        for (const r of basisRows) get(r.date).basis = r.basis

        setRows([...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1)))
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
  }, [days])

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        외인 현물(코스피+코스닥) · 선물(K200) 순매수 + 베이시스 — 참고 지표(중립 계기판, 함정 탐지기 아님)
      </div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && <ForeignPositionChart data={rows} />}
    </div>
  )
}

// 개인 파생ETF 방향성 게이지 상세 — EtfDirectionCard(순수 프레젠테이션, PLAN.md §4.5-1)에
// derivative-flow 데이터를 붙여주는 자기완결 래퍼. 컴팩트 타일에서 클릭했을 때만
// 마운트되므로(Modal.jsx 주석 참고) 열 때마다 최신 데이터를 새로 받는다.
function DerivativeEtfModal() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchDerivativeFlow(90)
      .then((body) => {
        if (!cancelled) setData(body)
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
  }, [])

  return (
    <EtfDirectionCard
      loading={loading}
      error={error}
      universe={data?.universe}
      latest={data?.latest}
      series={data?.series ?? []}
    />
  )
}

function FlowRankFullModal({ onRowClick }) {
  const [investor, setInvestor] = useState('foreign')
  const [side, setSide] = useState('buy')
  const [dates, setDates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowRank(investor, side, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setDates(body.dates || [])
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
  }, [investor, side])

  return (
    <FlowRankTable
      investor={investor}
      onInvestorChange={setInvestor}
      side={side}
      onSideChange={setSide}
      loading={loading}
      error={error}
      dates={dates}
      onRowClick={onRowClick}
    />
  )
}

function ValueRankFullModal({ onRowClick }) {
  const [market, setMarket] = useState('all')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchValueRank(market, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) {
          setDate(body.date)
          setRows(body.rows || [])
        }
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
  }, [market])

  return (
    <div>
      <div className="toggle-row">
        {VALUE_RANK_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${market === opt.key ? 'active' : ''}`}
            onClick={() => setMarket(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <ValueRankTable rows={rows} loading={loading} error={error} date={date} onRowClick={onRowClick} />
    </div>
  )
}

function FlowPathFullModal({ onRowClick }) {
  const [direction, setDirection] = useState('in')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 30, direction)
      .then((body) => {
        if (!cancelled) {
          setDate(body.date)
          setRows(body.rows || [])
        }
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
  }, [direction])

  return (
    <FlowPathTable
      loading={loading}
      error={error}
      date={date}
      rows={rows}
      direction={direction}
      onDirectionChange={setDirection}
      onRowClick={onRowClick}
    />
  )
}

// 실시간 관심 종목 TOP20 전체 보기 — ValueRankFullModal/FlowPathFullModal과 동일하게
// 마운트 시(모달이 열릴 때) 자기 데이터를 불러오는 자기완결 컴포넌트다. 다른
// FullModal들과 달리 행이 클릭 가능해야 하므로(종목 상세 모달로 전환) 호출부
// (DashboardPage)가 onSelectStock 콜백을 넘겨준다 — 이 컴포넌트 자체는 setModal을
// 모르므로 이 방식으로만 상위 상태를 바꿀 수 있다. 20행짜리 단순 목록이라 별도
// 테이블 컴포넌트 파일(ValueRankTable처럼)로 뽑지 않고 여기 인라인으로 둔다.
function AttentionFullModal({ onSelectStock }) {
  const [attention, setAttention] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchAttention()
      .then((body) => {
        if (!cancelled) setAttention(body)
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
  }, [])

  const rows = attention?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        조회수 기준 · 60초 갱신
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row) => (
            <Top5RowTile key={row.code} clickable onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank ?? '-'}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TOP5 카드 행 — clickable=true면 <button>(hover 배경 + 클릭), false면 기존과 동일한
// <div>(정적 텍스트). 모든 랭킹 행 클릭 → 종목 상세 모달 통일(사용자 요구, 이전엔
// 실시간 관심 TOP5만 클릭됐다) 작업에서 4개 TOP5 카드가 공유한다. 정적 배포
// (STATIC_DATA)에서는 호출부가 clickable=false를 넘겨 행을 비활성으로 둔다 —
// fetchStockSeries가 정적 스냅샷을 지원하지 않아(§ 정적 모드 판단, api.js 참고)
// 클릭해봤자 항상 에러만 뜨므로, 차라리 클릭 자체를 막는 쪽이 낫다고 판단했다.
// ---------------------------------------------------------------------------
function Top5RowTile({ clickable, onClick, children }) {
  const Tag = clickable ? 'button' : 'div'
  return (
    <Tag
      type={clickable ? 'button' : undefined}
      className={`top5-row ${clickable ? 'top5-row-clickable' : ''}`}
      onClick={clickable ? onClick : undefined}
    >
      {children}
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// TOP5 요약 행 — 표(FlowRankTable 등)를 그대로 축소하지 않고, "종목명·핵심 숫자·배지"
// 만 남긴 가벼운 목록을 별도로 그린다(사용자 요구: "100개짜리 리스트도 뒤로").
// ---------------------------------------------------------------------------
function Top5Card({ title, hint, rows, onMore, renderRow, emptyText = '표시할 데이터가 없습니다.', hoverDate }) {
  return (
    <div className="top5-card" title={hoverDate}>
      <div className="top5-card-header">
        <span className="top5-card-title">{title}</span>
        <button type="button" className="top5-more" onClick={onMore}>
          전체 보기 ›
        </button>
      </div>
      {hint && <div className="toggle-hint" style={{ marginBottom: 6 }}>{hint}</div>}
      {(!rows || rows.length === 0) && <div className="state" style={{ padding: '16px 0' }}>{emptyText}</div>}
      {rows && rows.length > 0 && <div>{rows.slice(0, 5).map(renderRow)}</div>}
    </div>
  )
}

export default function DashboardPage() {
  // 지수 3종(코스피/코스닥/선물) — 타일 표시용 + 캔들 모달 기본 데이터를 겸한다.
  const [marketData, setMarketData] = useState({})
  const [marketLoading, setMarketLoading] = useState(true)

  const [sentiment, setSentiment] = useState(null)
  const [breadth, setBreadth] = useState(null)

  // 장중 잠정 수급(PLAN.md §6 3.7-3) — null이면 "라이브 없음"(폴링 전 최초 로딩 중이거나
  // 실패)이라 아래 투자자별 수급 요약 타일은 항상 기존 확정치(flowInvestorSummary)로
  // 폴백한다. 정적 배포(STATIC_DATA)에서는 이 state가 항상 null로 남아 기존 동작 그대로다.
  const [flowLive, setFlowLive] = useState(null)

  const [fundSeries, setFundSeries] = useState({})
  // 환율(USD/KRW)·WTI 타일의 최신값/전일비/기준일 계산용 — 모달(MacroModal)은 별도로
  // 자기 기간(기본 1Y)만큼 다시 불러오므로 이 state와 무관하다.
  const [macroSeries, setMacroSeries] = useState({})

  // 외인 양손 · 현선물 섹션(PLAN.md §4.5-5) — 베이시스/만기(basisData), 파생ETF
  // 방향성 게이지(derivativeFlow), 프로그램 차익 순매수(programFlow) 타일용 데이터.
  // 외인 현물/선물 순매수 자체는 새로 fetch하지 않고 위 marketData(선물 포함)와
  // 아래 flowInvestorSummary/flowLiveSummary(현물, 코스피+코스닥)를 그대로 재사용한다.
  const [basisData, setBasisData] = useState(null)
  const [derivativeFlow, setDerivativeFlow] = useState(null)
  const [programFlow, setProgramFlow] = useState({})

  const [groupType, setGroupType] = useState('upjong')
  const [groupItems, setGroupItems] = useState([])
  const [groupLoading, setGroupLoading] = useState(false)
  const [groupError, setGroupError] = useState(null)

  const [flowRankTop, setFlowRankTop] = useState(null)
  const [valueRankTop, setValueRankTop] = useState(null)
  const [flowPathTop, setFlowPathTop] = useState(null)
  // 실시간 관심 종목 TOP20(PLAN.md 사용자 지시, live-only) — API 응답 바디를 그대로
  // 담는다({ rows, qry_tp, queried_at }). flowLive와 동일하게 정적 배포에서는 항상
  // null로 남는다.
  const [attentionTop, setAttentionTop] = useState(null)

  const [modal, setModal] = useState(null) // { type, title, ...params } | null

  // 지수 3종 — 타일(최신 종가/등락률) + 캔들 모달 기본 기간(90일) 데이터를 한 번에
  // 담는다. 병렬 요청(Promise.all)이라 시장 하나가 느려도 나머지 타일은 먼저 뜬다.
  useEffect(() => {
    let cancelled = false
    setMarketLoading(true)
    Promise.all(MARKETS.map((m) => fetchMarketSeries(m.key, DEFAULT_CANDLE_DAYS).catch(() => null))).then(
      (results) => {
        if (cancelled) return
        const next = {}
        MARKETS.forEach((m, i) => {
          next[m.key] = results[i] || { prices: [], flows: {} }
        })
        setMarketData(next)
        setMarketLoading(false)
      }
    )
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const toCamel = (row) =>
      row
        ? {
            adv: row.adv,
            dec: row.dec,
            flat: row.flat,
            limitUp: row.limit_up ?? row.limitUp,
            limitDown: row.limit_down ?? row.limitDown,
          }
        : null

    async function load() {
      try {
        const body = await fetchBreadthLive()
        if (cancelled) return
        setBreadth({ kospi: toCamel(body.kospi), kosdaq: toCamel(body.kosdaq) })
        return
      } catch {
        // 폴백으로 진행
      }
      try {
        const [kospiBody, kosdaqBody] = await Promise.all([
          fetchBreadth('kospi', 30).catch(() => null),
          fetchBreadth('kosdaq', 30).catch(() => null),
        ])
        if (cancelled) return
        const last = (body) => {
          const series = body?.series
          return series && series.length > 0 ? series[series.length - 1] : null
        }
        setBreadth({ kospi: toCamel(last(kospiBody)), kosdaq: toCamel(last(kosdaqBody)) })
      } catch {
        // 배지는 데이터 없음 상태로 자연히 표시된다.
      }
    }
    load()
    // flow/live·attention 폴링(아래 두 useEffect)과 동일한 60초 간격 — 이전에는 최초
    // 1회만 fetch하고 setInterval이 없어서 breadthTotals 배지(상단 등락 종목수)가
    // 페이지를 오래 열어둬도 갱신되지 않는 버그가 있었다(PLAN.md 서버 측 능동 60초
    // 갱신 작업에서 발견/수정).
    const intervalId = setInterval(load, BREADTH_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [])

  // 장중 잠정 수급 폴링 (PLAN.md §6 3.7-3) — 정적 배포에서는 애초에 시도하지 않는다
  // (로컬 전용 기능, api.js fetchFlowLive 주석 참고). 60초 간격 setInterval, 페이지를
  // 벗어나면(언마운트) 정리한다. 실패해도 조용히 null로 두고 기존 확정치 타일로
  // 폴백하므로 별도 에러 상태를 만들지 않는다.
  useEffect(() => {
    if (STATIC_DATA) return undefined
    let cancelled = false
    function load() {
      fetchFlowLive()
        .then((body) => {
          if (!cancelled) setFlowLive(body)
        })
        .catch(() => {
          if (!cancelled) setFlowLive(null)
        })
    }
    load()
    const intervalId = setInterval(load, FLOW_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [])

  // 실시간 관심 종목 TOP20 폴링 — flowLive 폴링과 동일한 패턴(정적 배포에서는 시도하지
  // 않음, 60초 간격 setInterval, 언마운트 시 정리, 실패 시 조용히 null로 두고 카드가
  // "표시할 데이터가 없습니다"를 자연히 보여주게 둔다).
  useEffect(() => {
    if (STATIC_DATA) return undefined
    let cancelled = false
    function load() {
      fetchAttention()
        .then((body) => {
          if (!cancelled) setAttentionTop(body)
        })
        .catch(() => {
          if (!cancelled) setAttentionTop(null)
        })
    }
    load()
    const intervalId = setInterval(load, ATTENTION_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(MARKET_FUND_IDS, 10)
      .then((body) => {
        if (!cancelled) setFundSeries(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(
      MACRO_SERIES.map((s) => s.id),
      MACRO_TILE_DAYS
    )
      .then((body) => {
        if (!cancelled) setMacroSeries(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 베이시스 + 다음 만기(PLAN.md §4.5-3/-5) — 짧은 창(타일용)이면 충분하지만, 최근
  // 며칠치가 있어야 "직전 값과 비교" 없이도(베이시스는 전일비 화살표를 두지 않는다)
  // 최소한 latest가 안정적으로 채워진다.
  useEffect(() => {
    let cancelled = false
    fetchBasis(FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setBasisData(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 개인 방향성(파생ETF, PLAN.md §4.5-1) 타일 최신값 — 상세는 DerivativeEtfModal이
  // 별도로(90일) 다시 불러온다.
  useEffect(() => {
    let cancelled = false
    fetchDerivativeFlow(FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setDerivativeFlow(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 프로그램매매 차익 순매수(PLAN.md §4.5-4) — 코스피+코스닥 두 시리즈를 신용융자
  // 타일(creditLoanSum)과 동일하게 합산해서 보여준다.
  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(PROGRAM_ARB_IDS, FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setProgramFlow(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setGroupLoading(true)
    setGroupError(null)
    fetchGroups(groupType)
      .then((items) => {
        if (!cancelled) setGroupItems(items || [])
      })
      .catch((e) => {
        if (!cancelled) setGroupError(e.message)
      })
      .finally(() => {
        if (!cancelled) setGroupLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [groupType])

  useEffect(() => {
    let cancelled = false
    fetchFlowRank('foreign', 'buy', FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setFlowRankTop((body.dates && body.dates[0]) || null)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchValueRank('all', FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setValueRankTop(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 5, 'in')
      .then((body) => {
        if (!cancelled) setFlowPathTop(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const closeModal = () => setModal(null)

  // 종목 상세 모달을 여는 공용 헬퍼 — 실시간 관심 TOP5(기존)와 수급/거래대금/ETF경유
  // 랭킹 3종(이번 작업으로 클릭 가능해짐)이 전부 이 함수 하나로 모인다(사용자 요구:
  // "모든 랭킹 행 클릭 → 종목 상세 모달"). 열려 있던 리스트 모달을 별도로 닫을
  // 필요는 없다 — setModal은 단일 상태라 새 타입으로 덮어쓰면 그 자체로 "닫고
  // 교체"가 된다(Modal.jsx가 modal?.type에 따라 자식을 조건부로만 렌더).
  const openStockModal = (code, name, extra = {}) =>
    setModal({
      type: 'stock',
      title: `${name || code} · 종목 상세`,
      code,
      stock: { code, name, ...extra },
    })

  // 지수 타일 + baseDate 계산이 공유하는 헬퍼 — marketData[key].prices의 마지막 값.
  const latestPriceOf = (key) => {
    const data = marketData[key]
    return data?.prices?.length ? data.prices[data.prices.length - 1] : null
  }

  // 등락 종목수 압축 칩 — 코스피+코스닥 합계 (BreadthBadge.splitTotals와 동일 규칙:
  // 상승 = adv+limitUp, 하락 = dec+limitDown).
  const breadthTotals = (() => {
    if (!breadth) return null
    const rows = [breadth.kospi, breadth.kosdaq].filter(Boolean)
    if (rows.length === 0) return null
    return rows.reduce(
      (acc, r) => ({
        up: acc.up + (r.adv ?? 0) + (r.limitUp ?? 0),
        down: acc.down + (r.dec ?? 0) + (r.limitDown ?? 0),
      }),
      { up: 0, down: 0 }
    )
  })()

  const fundLatest = (id) => {
    const points = fundSeries[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const fundPrev = (id) => {
    const points = fundSeries[id] || []
    return points.length > 1 ? points[points.length - 2].value : null
  }
  const macroLatest = (id) => {
    const points = macroSeries[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const macroPrev = (id) => {
    const points = macroSeries[id] || []
    return points.length > 1 ? points[points.length - 2].value : null
  }
  const macroDate = (id) => {
    const points = macroSeries[id] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }
  // 신용융자 타일 — 코스피+코스닥 두 시리즈를 합산해 단일 숫자로 보여준다(MARKET_FUND_IDS
  // 관례상 별도 "합계" id가 없다). 둘 다 없으면 null(대시 표시), 하나라도 있으면 0으로
  // 보정해 더한다.
  function creditLoanSum(pick) {
    const kospi = pick('credit_loan_kospi')
    const kosdaq = pick('credit_loan_kosdaq')
    if (kospi === null && kosdaq === null) return null
    return trillion((kospi ?? 0) + (kosdaq ?? 0))
  }
  const creditLoanLatest = creditLoanSum(fundLatest)
  const creditLoanPrev = creditLoanSum(fundPrev)

  const etfComponent = sentiment?.components?.etf

  // 장중 잠정 수급 — 코스피+코스닥 investors를 투자자별로 합산한다(기존 확정치
  // flowInvestorSummary와 같은 모양: {투자자명: net_value}). 장 마감(market_closed)
  // 이후이거나 두 시장 다 provisional이 아니면(=백엔드가 이미 DB 확정치로 폴백한
  // 경우) "장중 잠정" 타일을 켤 이유가 없으므로 flowLiveActive를 false로 둔다 —
  // PLAN.md §6 3.7-3 지시: "장중엔 live 값 + 배지, 실패·마감 후엔 기존 확정치 + 라벨".
  const flowLiveActive = Boolean(
    flowLive && flowLive.market_closed === false && (flowLive.kospi?.provisional || flowLive.kosdaq?.provisional)
  )
  const flowLiveSummary = (() => {
    if (!flowLiveActive) return null
    const out = {}
    for (const name of DEFAULT_INVESTORS) {
      const kospiVal = flowLive.kospi?.investors?.[name]?.net_value
      const kosdaqVal = flowLive.kosdaq?.investors?.[name]?.net_value
      if (kospiVal === undefined && kosdaqVal === undefined) continue
      out[name] = (kospiVal ?? 0) + (kosdaqVal ?? 0)
    }
    return out
  })()

  const flowInvestorSummary = (() => {
    const kospi = marketData.kospi
    const kosdaq = marketData.kosdaq
    if (!kospi || !kosdaq) return null
    const merged = mergeFlows(kospi.flows, kosdaq.flows)
    const out = {}
    for (const name of DEFAULT_INVESTORS) {
      const rows = merged[name] || []
      out[name] = rows.length > 0 ? rows[rows.length - 1] : null
    }
    return out
  })()

  // 외인 양손 · 현선물 섹션(PLAN.md §4.5-5) — 현물은 위에서 이미 계산한
  // flowInvestorSummary/flowLiveSummary의 '외국인' 값을 그대로 재사용하고(중복 fetch
  // 없음), 선물은 marketData.futures(지수 3종 fetch가 이미 flows까지 받아온다)의
  // '외국인' 마지막 행을 쓴다.
  const foreignSpotLiveValue = flowLiveSummary?.['외국인']
  const foreignSpotIsLive = foreignSpotLiveValue !== undefined
  const foreignSpotRow = flowInvestorSummary?.['외국인']
  const foreignSpotValue = foreignSpotIsLive ? foreignSpotLiveValue : foreignSpotRow?.net_value
  const foreignFuturesRow = latestFlowRow(marketData.futures?.flows, '외국인')

  const basisLatest = basisData?.latest
  const expiry = basisData?.expiry
  const derivativeLatest = derivativeFlow?.latest
  const derivativeUniverse = derivativeFlow?.universe

  // 프로그램 차익 순매수 — 코스피+코스닥 합산(신용융자 타일 creditLoanSum과 동일한 관례).
  const programLatest = (id) => {
    const points = programFlow[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const programDate = (id) => {
    const points = programFlow[id] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }
  const programArbKospi = programLatest('prog_arb_kospi')
  const programArbKosdaq = programLatest('prog_arb_kosdaq')
  const programArbLatest =
    programArbKospi === null && programArbKosdaq === null ? null : (programArbKospi ?? 0) + (programArbKosdaq ?? 0)
  const programArbDate = latestOf(programDate('prog_arb_kospi'), programDate('prog_arb_kosdaq'))

  // 시그널 배지(PLAN.md §4.5-5, 중립 표현 — "함정" 단정 금지) — ① 외인 현물·선물 방향
  // 대치, ② 백워데이션, ③ 만기 D-3 이내. 값이 없거나(0 포함) 한쪽이 없으면 판단하지
  // 않는다(오검 방지 — Math.sign(0)===0이라 자연히 걸러진다).
  const foreignSpotSign = foreignSpotValue === null || foreignSpotValue === undefined ? 0 : Math.sign(foreignSpotValue)
  const foreignFuturesSign =
    foreignFuturesRow?.net_value === null || foreignFuturesRow?.net_value === undefined
      ? 0
      : Math.sign(foreignFuturesRow.net_value)
  const directionMismatch = foreignSpotSign !== 0 && foreignFuturesSign !== 0 && foreignSpotSign !== foreignFuturesSign
  const backwardationSignal = basisLatest?.backwardation === true
  const expirySoonSignal = typeof expiry?.d_day === 'number' && expiry.d_day >= 0 && expiry.d_day <= EXPIRY_SOON_D_DAY

  const foreignSignals = [
    directionMismatch && { key: 'direction', kind: 'warn', label: '현·선 방향 상이' },
    backwardationSignal && { key: 'backwardation', kind: 'info', label: '백워데이션' },
    expirySoonSignal && { key: 'expiry', kind: 'warn', label: '만기 임박' },
  ].filter(Boolean)

  // 대표 기준일(대시보드 상단 1회 표시) — 소스별로 수집 시차가 있어(지수/수급/ETF 등)
  // 항상 같은 날짜라는 보장이 없으므로, 각 타일 데이터 날짜들 중 최신값 하나만 뽑는다.
  // 사용자 피드백: "타일마다 날짜가 있는데 중복이다. 어차피 마지막 거래일일 텐데" —
  // 실제로는 다를 수 있어 예외 표시(StaleDate/staleHintLabel)로 보완한다.
  const investorDates = DEFAULT_INVESTORS.map((name) => flowInvestorSummary?.[name]?.date).filter(Boolean)
  // flowLive.date는 market_closed===false(장중)일 때만 대표 기준일 계산에 넣는다.
  // 휴장일(주말 등)에도 /api/markets/flow/live가 market_closed:true 상태로 오늘 날짜를
  // 되돌려주는 경우가 있어, 그 날짜를 그대로 latestOf에 섞으면 baseDate가 실제
  // 마지막 거래일보다 앞으로(오늘로) 부풀어 다른 모든 타일이 "뒤처진 것처럼"
  // StaleDate 배지가 붙는 왜곡이 생긴다 — 장이 닫힌 날은 확정치 날짜들만으로
  // 기준일을 정한다(수급 타일의 "장중 잠정" 배지 게이트인 flowLiveActive와 동일하게
  // market_closed로 판단).
  const flowLiveOpen = flowLive?.market_closed === false
  // attentionTop.queried_at은 의도적으로 baseDate 계산에 넣지 않는다 — flowLive.date와
  // 달리 "거래일 날짜"가 아니라 조회 시각을 담은 전체 ISO 타임스탬프(예:
  // "2026-07-18T17:40:47.354047+00:00")라 formatDate가 'YYYY-MM-DD'로 정규화하지
  // 못한다(8자리 숫자가 아님). 억지로 넣어도 latestOf의 length===10 필터에 걸러져
  // 아무 효과가 없고, 의미상으로도 "당일 거래 기준일"과 "지금 이 순간"은 다른
  // 개념이라 섞으면 오히려 baseDate 해석이 혼란스러워진다.
  const baseDate = latestOf(
    ...MARKETS.map((m) => latestPriceOf(m.key)?.date),
    etfComponent?.date,
    ...investorDates,
    flowLiveOpen ? flowLive?.kospi?.date : null,
    flowLiveOpen ? flowLive?.kosdaq?.date : null,
    valueRankTop?.date,
    flowPathTop?.date,
    flowRankTop?.date,
    macroDate('usdkrw'),
    macroDate('wti'),
    foreignFuturesRow?.date,
    basisLatest?.date,
    derivativeLatest?.date,
    programArbDate
  )

  return (
    <div className="dashboard-page">
      <div className="dashboard-header-row">
        {/* 종목 검색 (PLAN.md §6 3.7-2) — 온디맨드 API(/api/stocks/search)라 정적
            스냅샷 대상이 아니다. 정적 배포(STATIC_DATA=1)에서는 검색을 서빙할 수 없으므로
            같은 자리에 비활성 입력만 남겨 레이아웃이 흔들리지 않게 한다. */}
        {STATIC_DATA ? (
          <input
            type="text"
            className="dashboard-search"
            placeholder="종목 검색 — 준비 중"
            disabled
            title="정적 배포에선 종목 검색 미지원"
            aria-label="종목 검색 (정적 배포 미지원)"
          />
        ) : (
          <StockSearch onSelect={(stock) => openStockModal(stock.code, stock.name, stock)} />
        )}
        {/* 대표 기준일 — 타일별 개별 날짜 대신 여기 한 번만 노출한다(위 baseDate 계산 참고). */}
        {baseDate && (
          <span
            className="dashboard-base-date"
            title={`대표 기준일 — 각 타일 데이터 날짜 중 최신값 (${baseDate})`}
          >
            기준일 {formatDate(baseDate)}
          </span>
        )}
      </div>

      {/* 1. 지수 3종 */}
      <div className="section-title" style={{ marginTop: 16 }}>
        지수
      </div>
      <div className="kpi-grid">
        {MARKETS.map((m) => {
          const latest = latestPriceOf(m.key)
          return (
            <KpiTile
              key={m.key}
              label={m.label}
              value={latest ? numFmt.format(latest.close) : marketLoading ? '…' : '-'}
              valueClass={latest ? rateClass(latest.changeRate) : ''}
              sub={
                latest && (
                  <span className={`kpi-tile-sub ${rateClass(latest.changeRate)}`}>
                    {rateLabel(latest.changeRate)}
                    <StaleDate date={latest.date} baseDate={baseDate} prefix=" · " />
                  </span>
                )
              }
              title={latest?.date ? formatDate(latest.date) : undefined}
              onClick={() => setModal({ type: 'candle', market: m.key, title: `${m.label} · 캔들차트` })}
            />
          )
        })}
      </div>

      {/* 2. 투자자별 수급 요약 — 장중에는 라이브 잠정치(PLAN.md §6 3.7-3) + "장중 잠정"
          배지, 그 외(장마감 후·라이브 실패·정적 배포)에는 기존 확정치 + "확정" 라벨로
          자동 전환된다(flowLiveActive, 위 계산부 참고). 라이브 배지가 날짜 역할을
          겸하므로 라이브 타일에는 StaleDate를 붙이지 않는다(작업 지시). */}
      <div className="section-title">투자자별 수급 요약</div>
      <div className="kpi-grid">
        {DEFAULT_INVESTORS.map((name) => {
          const liveValue = flowLiveSummary?.[name]
          const isLive = liveValue !== undefined
          const row = flowInvestorSummary?.[name]
          const value = isLive ? liveValue : row?.net_value
          const hasValue = value !== undefined && value !== null
          return (
            <KpiTile
              key={name}
              label={
                <>
                  <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
                </>
              }
              value={hasValue ? eokLabel(value) : marketLoading ? '…' : '-'}
              valueClass={hasValue ? (value >= 0 ? 'up' : 'down') : ''}
              sub={
                isLive ? (
                  <span className="kpi-tile-sub">
                    <Badge kind="live" />
                  </span>
                ) : (
                  row?.date && (
                    <span className="kpi-tile-sub">
                      확정
                      <StaleDate date={row.date} baseDate={baseDate} prefix=" · " />
                    </span>
                  )
                )
              }
              title={!isLive && row?.date ? formatDate(row.date) : undefined}
              onClick={() => setModal({ type: 'flowSummary', title: '투자자별 수급 (코스피+코스닥)' })}
            />
          )
        })}
      </div>

      {/* 2.5 외인 양손 · 현선물 (PLAN.md §4.5-5) — 중립적 상태 계기판, "함정" 단정 표현
          금지(§4.5 배경). 시그널 배지는 있을 때만 렌더되고, 클릭하면 전부 같은 상세
          모달(ForeignPositionModal — 현물/선물 시계열 + 베이시스 오버레이)을 연다. */}
      <div className="section-title">외인 양손 · 현선물</div>
      {foreignSignals.length > 0 && (
        <div className="signal-row">
          {foreignSignals.map((s) => (
            <button
              key={s.key}
              type="button"
              className={`badge badge-${s.kind}`}
              onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
              title="외인 현물·선물 순매수와 베이시스 시계열 — 참고 지표(중립 계기판)"
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="kpi-grid">
        <KpiTile
          label="외인 현물"
          value={
            foreignSpotValue !== undefined && foreignSpotValue !== null
              ? eokLabel(foreignSpotValue)
              : marketLoading
                ? '…'
                : '-'
          }
          valueClass={
            foreignSpotValue !== undefined && foreignSpotValue !== null ? (foreignSpotValue >= 0 ? 'up' : 'down') : ''
          }
          sub={
            foreignSpotIsLive ? (
              <span className="kpi-tile-sub">
                <Badge kind="live" />
              </span>
            ) : (
              foreignSpotRow?.date && (
                <span className="kpi-tile-sub">
                  확정
                  <StaleDate date={foreignSpotRow.date} baseDate={baseDate} prefix=" · " />
                </span>
              )
            )
          }
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="외인 선물 (K200)"
          value={foreignFuturesRow ? eokLabel(foreignFuturesRow.net_value) : marketLoading ? '…' : '-'}
          valueClass={foreignFuturesRow ? (foreignFuturesRow.net_value >= 0 ? 'up' : 'down') : ''}
          sub={
            foreignFuturesRow?.date && (
              <span className="kpi-tile-sub">
                확정
                <StaleDate date={foreignFuturesRow.date} baseDate={baseDate} prefix=" · " />
              </span>
            )
          }
          title={foreignFuturesRow?.date ? formatDate(foreignFuturesRow.date) : undefined}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="개인 방향성(파생ETF)"
          value={derivativeLatest ? eokLabel(derivativeLatest.net_bet) : '…'}
          valueClass={derivativeLatest ? (derivativeLatest.net_bet >= 0 ? 'up' : 'down') : ''}
          sub={
            <span className="kpi-tile-sub">
              레버리지 {derivativeUniverse?.leverage ?? 0} · 인버스 {derivativeUniverse?.inverse ?? 0}종목
              <StaleDate date={derivativeLatest?.date} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title={derivativeLatest?.date ? formatDate(derivativeLatest.date) : undefined}
          onClick={() => setModal({ type: 'derivativeEtf', title: '개인 방향성(파생ETF) · 순베팅' })}
        />
        <KpiTile
          label="베이시스"
          value={basisLabel(basisLatest?.basis)}
          valueClass={basisLatest?.basis === undefined || basisLatest?.basis === null ? '' : basisLatest.basis >= 0 ? 'up' : 'down'}
          sub={
            <span className="kpi-tile-sub">
              {basisLatest?.backwardation === undefined || basisLatest?.backwardation === null ? (
                '-'
              ) : basisLatest.backwardation ? (
                <Badge kind="info">백워데이션 · 차익 매도 유의</Badge>
              ) : (
                '콘탱고'
              )}
              <StaleDate date={basisLatest?.date} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title={basisLatest?.date ? formatDate(basisLatest.date) : undefined}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="프로그램 차익 순매수"
          value={programArbLatest !== null ? eokLabel(programArbLatest) : '…'}
          valueClass={programArbLatest === null ? '' : programArbLatest >= 0 ? 'up' : 'down'}
          sub={
            <span className="kpi-tile-sub">
              코스피+코스닥
              <StaleDate date={programArbDate} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="다음 만기"
          value={expiry?.date ? `${mmdd(expiry.date)} · D-${expiry.d_day}` : '…'}
          sub={expiry?.quadruple && <Badge kind="warn">네 마녀의 날</Badge>}
          title={expiry?.date ? formatDate(expiry.date) : undefined}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
      </div>

      {/* 3. 시황 · 자금 */}
      <div className="section-title">시황 · 자금</div>
      <div className="kpi-grid">
        <KpiTile
          label="매수세 게이지"
          value={scoreLabel(sentiment?.score)}
          valueClass={scoreClass(sentiment?.score)}
          sub={<span className="kpi-tile-sub">-100 매도세 · +100 매수세 (근사)</span>}
          onClick={() => setModal({ type: 'sentiment', title: '시장 매수세/매도세 게이지' })}
        />
        <KpiTile
          label="등락 종목수"
          value={
            breadthTotals ? (
              <>
                <span className="up">{countFmt.format(breadthTotals.up)}↑</span>{' '}
                <span className="down">{countFmt.format(breadthTotals.down)}↓</span>
              </>
            ) : (
              '…'
            )
          }
          sub={<span className="kpi-tile-sub">코스피+코스닥 합계</span>}
          onClick={() => setModal({ type: 'breadth', title: '등락 종목수' })}
        />
        <KpiTile
          label="투자자예탁금"
          value={trillionLabel(fundLatest('investor_deposit'))}
          sub={
            <DiffArrow
              current={trillion(fundLatest('investor_deposit'))}
              prev={trillion(fundPrev('investor_deposit'))}
              formatter={(v) => `${joFmt.format(v)}조`}
            />
          }
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        <KpiTile
          label="대차잔고"
          value={trillionLabel(fundLatest('lending_balance'))}
          sub={
            <DiffArrow
              current={trillion(fundLatest('lending_balance'))}
              prev={trillion(fundPrev('lending_balance'))}
              formatter={(v) => `${joFmt.format(v)}조`}
            />
          }
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        <KpiTile
          label="신용융자"
          value={creditLoanLatest !== null ? `${joFmt.format(creditLoanLatest)}조` : '-'}
          sub={<DiffArrow current={creditLoanLatest} prev={creditLoanPrev} formatter={(v) => `${joFmt.format(v)}조`} />}
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        <KpiTile
          label="ETF 순유입 합계"
          value={etfComponent ? eokLabel(etfComponent.net_inflow_sum) : '…'}
          valueClass={etfComponent && etfComponent.net_inflow_sum < 0 ? 'down' : 'up'}
          sub={<StaleDate date={etfComponent?.date} baseDate={baseDate} />}
          title={etfComponent?.date ? formatDate(etfComponent.date) : undefined}
          onClick={() => setModal({ type: 'sentiment', title: '시장 매수세/매도세 게이지' })}
        />
        {/* 환율/WTI — 매크로 탭 통합(탭 제거, 타일+모달로 편입). 환율은 상승이 "좋은
            것"이 아니라 주가 등락 색 관례와 혼동될 수 있어 DiffArrow를 neutral로 켜서
            화살표 색을 중립(회색)으로 둔다 — 값·화살표 방향·포맷 자체는 다른 타일과
            동일한 관례(예탁금 타일의 DiffArrow)를 그대로 쓴다. WTI는 색상 구분 유지.
            브렌트는 타일에서 생략하고 모달(MacroModal)에서만 보여준다. */}
        <KpiTile
          label="환율(USD/KRW)"
          value={fxLabel(macroLatest('usdkrw'))}
          sub={
            <>
              <DiffArrow
                current={macroLatest('usdkrw')}
                prev={macroPrev('usdkrw')}
                formatter={(v) => `${fxFmt.format(v)}원`}
                neutral
              />
              <StaleDate date={macroDate('usdkrw')} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={macroDate('usdkrw') ? formatDate(macroDate('usdkrw')) : undefined}
          onClick={() => setModal({ type: 'macro', title: '환율 · 유가' })}
        />
        <KpiTile
          label="WTI"
          value={oilLabel(macroLatest('wti'))}
          sub={
            <>
              <DiffArrow current={macroLatest('wti')} prev={macroPrev('wti')} formatter={(v) => `$${oilFmt.format(v)}`} />
              <StaleDate date={macroDate('wti')} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={macroDate('wti') ? formatDate(macroDate('wti')) : undefined}
          onClick={() => setModal({ type: 'macro', title: '환율 · 유가' })}
        />
      </div>

      {/* 4. 컴팩트 트리맵 */}
      <div className="section-title">업종 · 테마 강약</div>
      <div className="toggle-row">
        {GROUP_TYPE_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${groupType === opt.key ? 'active' : ''}`}
            onClick={() => setGroupType(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        <span className="toggle-hint">일별 스냅샷 · 색 = 등락률</span>
      </div>
      {groupLoading && <div className="state">불러오는 중…</div>}
      {groupError && <div className="state error">{groupError}</div>}
      {!groupLoading && !groupError && <GroupTreemap items={groupItems} sizeBy="value" height={200} />}

      {/* 5. TOP5 요약 3열 — "…기준" 라벨은 대표 기준일(baseDate)과 같으면 생략, 다르면
          MM-DD만 붙인다(staleHintLabel, 대시보드 상단 표시와 동일 규칙). 정확한 날짜는
          카드 title(hover)로 확인 가능. */}
      <div className="section-title">TOP5 요약</div>
      <div className="top5-grid">
        <Top5Card
          title="수급 상위"
          hint={
            <>
              외국인 순매수
              <StaleDate date={flowRankTop?.date} baseDate={baseDate} prefix=" · " />
            </>
          }
          hoverDate={flowRankTop?.date ? formatDate(flowRankTop.date) : undefined}
          rows={flowRankTop?.rows}
          onMore={() => setModal({ type: 'flowRank', title: '수급 상위 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={row.code}
              clickable={!STATIC_DATA}
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className="top5-row-value up">{eokLabel(row.net_value)}</span>
            </Top5RowTile>
          )}
        />
        <Top5Card
          title="거래대금 상위"
          hint={valueRankTop?.date ? staleHintLabel(valueRankTop.date, baseDate) : undefined}
          hoverDate={valueRankTop?.date ? formatDate(valueRankTop.date) : undefined}
          rows={valueRankTop?.rows}
          onMore={() => setModal({ type: 'valueRank', title: '거래대금 상위 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={`${row.market}-${row.code}`}
              clickable={!STATIC_DATA}
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{eokLabel(row.value)}</span>
            </Top5RowTile>
          )}
        />
        <Top5Card
          title="ETF 경유 상위"
          hint={flowPathTop?.date ? staleHintLabel(flowPathTop.date, baseDate, '유입') || '유입' : undefined}
          hoverDate={flowPathTop?.date ? formatDate(flowPathTop.date) : undefined}
          rows={flowPathTop?.rows}
          onMore={() => setModal({ type: 'flowPath', title: 'ETF 경유 수급 상위 — 전체' })}
          renderRow={(row, i) => (
            <Top5RowTile key={row.code} clickable={!STATIC_DATA} onClick={() => openStockModal(row.code, row.name)}>
              <span className="top5-row-name">
                {i + 1}. {row.name || row.code}
              </span>
              <span className={`top5-row-value ${row.via_etf_net >= 0 ? 'up' : 'down'}`}>
                {eokLabel(row.via_etf_net)}
              </span>
            </Top5RowTile>
          )}
        />
        {/* 실시간 관심 종목 TOP20(조회수 기준, live-only) — flowLive와 동일하게 정적
            배포에서는 attentionTop이 항상 null이라 카드가 "표시할 데이터가 없습니다"로
            자연히 빈 상태를 보여준다. 다른 3개 카드와 달리 행 자체가 클릭 가능해
            바로 종목 상세 모달로 이어진다(StockSearch onSelect와 동일한 모달 타입). */}
        <Top5Card
          title="실시간 관심 TOP5"
          hint="조회수 기준 · 60초 갱신"
          rows={attentionTop?.rows}
          onMore={() => setModal({ type: 'attention', title: '실시간 관심 종목 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={row.code}
              clickable
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank ?? '-'}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          )}
        />
      </div>

      <Modal open={Boolean(modal)} onClose={closeModal} title={modal?.title}>
        {modal?.type === 'candle' && <CandleModal market={modal.market} />}
        {modal?.type === 'sentiment' && <SentimentModal />}
        {modal?.type === 'breadth' && <BreadthModal />}
        {modal?.type === 'fund' && <FundModal />}
        {modal?.type === 'macro' && <MacroModal />}
        {modal?.type === 'flowSummary' && <FlowSummaryModal />}
        {modal?.type === 'foreignPosition' && <ForeignPositionModal />}
        {modal?.type === 'derivativeEtf' && <DerivativeEtfModal />}
        {/* 랭킹 3종 전체 보기 모달 — onRowClick을 STATIC_DATA일 때 undefined로 넘겨
            행 클릭 자체를 비활성화한다(TOP5 카드와 동일한 정적 모드 판단, 위
            Top5RowTile 주석 참고). */}
        {modal?.type === 'flowRank' && (
          <FlowRankFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'valueRank' && (
          <ValueRankFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'flowPath' && (
          <FlowPathFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'attention' && (
          <AttentionFullModal
            onSelectStock={(row) => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
          />
        )}
        {modal?.type === 'stock' && <StockDetailModal code={modal.code} initial={modal.stock} />}
      </Modal>
    </div>
  )
}
