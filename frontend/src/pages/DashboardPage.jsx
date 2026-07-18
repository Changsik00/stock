import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchBreadth,
  fetchBreadthLive,
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
import FlowChart from '../components/FlowChart'
import FlowPathTable from '../components/FlowPathTable'
import FlowRankTable from '../components/FlowRankTable'
import GroupTreemap from '../components/GroupTreemap'
import MarketFundChart from '../components/MarketFundChart'
import Modal from '../components/Modal'
import PeriodPicker from '../components/PeriodPicker'
import SentimentGauge from '../components/SentimentGauge'
import StockDetailModal from '../components/StockDetailModal'
import StockSearch from '../components/StockSearch'
import ValueRankTable from '../components/ValueRankTable'
import { DEFAULT_INVESTORS, INVESTOR_COLOR_VAR, MARKETS, MARKET_FUND_IDS } from '../constants'
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

// KPI 타일 초기 캔들 모달 기본 기간 — MarketPage와 동일하게 90일(3M)에서 시작한다.
const DEFAULT_CANDLE_DAYS = 90
// 자금(예탁금/대차잔고/신용융자) 모달 차트 기본 기간 — 추세를 보려면 90일보다 넉넉해야
// 자연스럽다.
const DEFAULT_FUND_DAYS = 180
// 투자자별 수급 요약 타일 + 모달 — 시장 탭과 동일하게 3M 기본.
const DEFAULT_FLOW_DAYS = 90
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

// 전일比 화살표 — prev가 없으면(첫 값) 표시하지 않는다.
function DiffArrow({ current, prev, formatter }) {
  if (current === null || current === undefined || prev === null || prev === undefined) return null
  const diff = current - prev
  if (diff === 0) return <span className="kpi-tile-sub">보합</span>
  const up = diff > 0
  return (
    <span className={`kpi-tile-sub ${up ? 'up' : 'down'}`}>
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
    return () => {
      cancelled = true
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

function FlowRankFullModal() {
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
    />
  )
}

function ValueRankFullModal() {
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
      <ValueRankTable rows={rows} loading={loading} error={error} date={date} />
    </div>
  )
}

function FlowPathFullModal() {
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
    />
  )
}

// ---------------------------------------------------------------------------
// TOP5 요약 행 — 표(FlowRankTable 등)를 그대로 축소하지 않고, "종목명·핵심 숫자·배지"
// 만 남긴 가벼운 목록을 별도로 그린다(사용자 요구: "100개짜리 리스트도 뒤로").
// ---------------------------------------------------------------------------
function Top5Card({ title, hint, rows, onMore, renderRow, emptyText = '표시할 데이터가 없습니다.' }) {
  return (
    <div className="top5-card">
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

  const [fundSeries, setFundSeries] = useState({})

  const [groupType, setGroupType] = useState('upjong')
  const [groupItems, setGroupItems] = useState([])
  const [groupLoading, setGroupLoading] = useState(false)
  const [groupError, setGroupError] = useState(null)

  const [flowRankTop, setFlowRankTop] = useState(null)
  const [valueRankTop, setValueRankTop] = useState(null)
  const [flowPathTop, setFlowPathTop] = useState(null)

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
    return () => {
      cancelled = true
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

  return (
    <div className="dashboard-page">
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
        <StockSearch
          onSelect={(stock) =>
            setModal({ type: 'stock', title: `${stock.name} · 종목 상세`, code: stock.code, stock })
          }
        />
      )}

      {/* 1. 지수 3종 */}
      <div className="section-title" style={{ marginTop: 16 }}>
        지수
      </div>
      <div className="kpi-grid">
        {MARKETS.map((m) => {
          const data = marketData[m.key]
          const latest = data?.prices?.length ? data.prices[data.prices.length - 1] : null
          return (
            <KpiTile
              key={m.key}
              label={m.label}
              value={latest ? numFmt.format(latest.close) : marketLoading ? '…' : '-'}
              valueClass={latest ? rateClass(latest.changeRate) : ''}
              sub={
                latest && (
                  <span className={`kpi-tile-sub ${rateClass(latest.changeRate)}`}>
                    {rateLabel(latest.changeRate)} · {formatDate(latest.date)}
                  </span>
                )
              }
              onClick={() => setModal({ type: 'candle', market: m.key, title: `${m.label} · 캔들차트` })}
            />
          )
        })}
      </div>

      {/* 2. 시황 · 자금 */}
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
          sub={etfComponent?.date && <span className="kpi-tile-sub">{formatDate(etfComponent.date)} 기준</span>}
          onClick={() => setModal({ type: 'sentiment', title: '시장 매수세/매도세 게이지' })}
        />
      </div>

      {/* 3. 컴팩트 트리맵 */}
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

      {/* 4. TOP5 요약 3열 */}
      <div className="section-title">TOP5 요약</div>
      <div className="top5-grid">
        <Top5Card
          title="수급 상위"
          hint="외국인 순매수"
          rows={flowRankTop?.rows}
          onMore={() => setModal({ type: 'flowRank', title: '수급 상위 — 전체' })}
          renderRow={(row) => (
            <div key={row.code} className="top5-row">
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className="top5-row-value up">{eokLabel(row.net_value)}</span>
            </div>
          )}
        />
        <Top5Card
          title="거래대금 상위"
          hint={valueRankTop?.date ? `${formatDate(valueRankTop.date)} 기준` : undefined}
          rows={valueRankTop?.rows}
          onMore={() => setModal({ type: 'valueRank', title: '거래대금 상위 — 전체' })}
          renderRow={(row) => (
            <div key={`${row.market}-${row.code}`} className="top5-row">
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{eokLabel(row.value)}</span>
            </div>
          )}
        />
        <Top5Card
          title="ETF 경유 상위"
          hint={flowPathTop?.date ? `${formatDate(flowPathTop.date)} 기준 · 유입` : undefined}
          rows={flowPathTop?.rows}
          onMore={() => setModal({ type: 'flowPath', title: 'ETF 경유 수급 상위 — 전체' })}
          renderRow={(row, i) => (
            <div key={row.code} className="top5-row">
              <span className="top5-row-name">
                {i + 1}. {row.name || row.code}
              </span>
              <span className={`top5-row-value ${row.via_etf_net >= 0 ? 'up' : 'down'}`}>
                {eokLabel(row.via_etf_net)}
              </span>
            </div>
          )}
        />
      </div>

      {/* 5. 투자자별 수급 요약 */}
      <div className="section-title">투자자별 수급 요약</div>
      <div className="kpi-grid">
        {DEFAULT_INVESTORS.map((name) => {
          const row = flowInvestorSummary?.[name]
          return (
            <KpiTile
              key={name}
              label={
                <>
                  <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
                </>
              }
              value={row ? eokLabel(row.net_value) : marketLoading ? '…' : '-'}
              valueClass={row ? (row.net_value >= 0 ? 'up' : 'down') : ''}
              sub={row?.date && <span className="kpi-tile-sub">{formatDate(row.date)} 기준</span>}
              onClick={() => setModal({ type: 'flowSummary', title: '투자자별 수급 (코스피+코스닥)' })}
            />
          )
        })}
      </div>

      <Modal open={Boolean(modal)} onClose={closeModal} title={modal?.title}>
        {modal?.type === 'candle' && <CandleModal market={modal.market} />}
        {modal?.type === 'sentiment' && <SentimentModal />}
        {modal?.type === 'breadth' && <BreadthModal />}
        {modal?.type === 'fund' && <FundModal />}
        {modal?.type === 'flowSummary' && <FlowSummaryModal />}
        {modal?.type === 'flowRank' && <FlowRankFullModal />}
        {modal?.type === 'valueRank' && <ValueRankFullModal />}
        {modal?.type === 'flowPath' && <FlowPathFullModal />}
        {modal?.type === 'stock' && <StockDetailModal code={modal.code} initial={modal.stock} />}
      </Modal>
    </div>
  )
}
