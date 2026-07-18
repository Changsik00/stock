import { useEffect, useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { fetchStockSeries } from '../api'
import { DEFAULT_INVESTORS, INVESTOR_COLOR_VAR } from '../constants'
import { formatEok } from '../format'
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
        return (
          <div className="stock-detail-flow-tile" key={name}>
            <span className="stock-detail-flow-tile-label">
              <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
            </span>
            <span className={`stock-detail-flow-tile-value ${eokClass(latest?.net_value)}`}>
              {latest ? eok(latest.net_value) : '-'}
            </span>
            <span className={`stock-detail-flow-tile-sub ${eokClass(latest?.cum_net_value)}`}>
              기간 누적 {latest ? eok(latest.cum_net_value) : '-'}
            </span>
          </div>
        )
      })}
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

  const name = series?.name || initial?.name
  const market = series?.market || initial?.market
  const isEtf = series?.is_etf ?? initial?.is_etf
  const prices = series?.prices || []
  const latestPrice = prices.length > 0 ? prices[prices.length - 1] : null
  const flowsError = series?.meta?.flows_error
  const hasFlows = !flowsError && series?.flows && Object.keys(series.flows).length > 0

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
      </div>

      <PeriodPicker value={days} onChange={setDays} />

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}

      {!loading && !error && prices.length > 0 && <CandleChart data={prices} height={280} />}
      {!loading && !error && prices.length === 0 && (
        <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
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
