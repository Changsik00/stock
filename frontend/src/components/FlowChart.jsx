import { useMemo, useState } from 'react'
import { Bar, BarChart, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { ALL_INVESTORS, DEFAULT_INVESTORS, EXTRA_INVESTORS, INVESTOR_COLOR_VAR } from '../constants'
import { formatDate } from '../format'

const numFmt = new Intl.NumberFormat('ko-KR')

function dateLabel(iso) {
  // "YYYY-MM-DD" 또는 "YYYYMMDD" 둘 다 대응.
  const digits = iso.replaceAll('-', '')
  return `${digits.slice(4, 6)}/${digits.slice(6, 8)}`
}

// net_value는 market_flow에 pykrx 원본 단위(원)로 적재되어 있다 — 기존 MarketChart의
// 거래대금 표시 관례(값/1e8 = 억원)를 그대로 따른다.
function eok(v) {
  if (v === null || v === undefined) return '-'
  return `${numFmt.format(Math.round(v / 1e8))}억원`
}

function barTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  const up = row.net_value >= 0
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>일별 순매수</span>
        <strong className={up ? 'up' : 'down'}>{eok(row.net_value)}</strong>
      </div>
    </div>
  )
}

function lineTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>누적 순매수</span>
        <strong>{eok(row.cumulative)}</strong>
      </div>
    </div>
  )
}

function InvestorRow({ name, rows }) {
  const latest = rows.length ? rows[rows.length - 1] : null
  return (
    <div className="flow-row">
      <div className="flow-row-header">
        <span className="flow-row-name">
          <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} />
          {name}
        </span>
        {latest && (
          <span className="flow-row-latest">
            누적 {eok(latest.cumulative)} · {formatDate(latest.date)} 기준
          </span>
        )}
      </div>
      <div className="flow-charts">
        <div>
          <div className="flow-chart-label">일별 순매수 (매수 우위=빨강 · 매도 우위=파랑)</div>
          <ResponsiveContainer width="100%" height={90}>
            <BarChart data={rows} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
              <XAxis dataKey="label" hide />
              <ReferenceLine y={0} stroke="var(--axis)" />
              <Tooltip content={barTooltip} cursor={{ fill: 'var(--chip-bg)' }} />
              <Bar dataKey="net_value" radius={[2, 2, 2, 2]} isAnimationActive={false}>
                {rows.map((r, i) => (
                  <Cell key={i} fill={r.net_value >= 0 ? 'var(--up)' : 'var(--down)'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="flow-chart-label">누적 순매수</div>
          <ResponsiveContainer width="100%" height={90}>
            <LineChart data={rows} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
              <XAxis dataKey="label" hide />
              <ReferenceLine y={0} stroke="var(--axis)" strokeDasharray="3 3" />
              <Tooltip content={lineTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
              <Line
                type="monotone"
                dataKey="cumulative"
                stroke={INVESTOR_COLOR_VAR[name]}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                activeDot={{ r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}

// 투자자별 일별 순매수 막대(부호=빨강/파랑) + 누적 순매수 라인 — 시장/종목 공용
// (PLAN.md §5.1 FlowChart.jsx). 기본 3분류(개인/외국인/기관계)는 항상 표시하고,
// 토글로 세부 분류(금융투자/보험/투신/사모/연기금)를 추가로 켤 수 있다.
export default function FlowChart({ flows }) {
  const [selectedExtra, setSelectedExtra] = useState([])

  const availableExtra = EXTRA_INVESTORS.filter((name) => flows?.[name]?.length)

  const toggle = (name) => {
    setSelectedExtra((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  const activeInvestors = ALL_INVESTORS.filter(
    (name) => DEFAULT_INVESTORS.includes(name) || selectedExtra.includes(name)
  ).filter((name) => flows?.[name]?.length)

  const seriesByInvestor = useMemo(() => {
    const out = {}
    for (const name of activeInvestors) {
      const sorted = [...(flows[name] || [])].sort((a, b) => (a.date < b.date ? -1 : 1))
      let cum = 0
      out[name] = sorted.map((d) => {
        cum += d.net_value || 0
        return { ...d, label: dateLabel(d.date), cumulative: cum }
      })
    }
    return out
  }, [flows, activeInvestors.join(',')])

  return (
    <div>
      {availableExtra.length > 0 && (
        <div className="toggle-row">
          {availableExtra.map((name) => (
            <button
              key={name}
              type="button"
              className={`toggle-chip ${selectedExtra.includes(name) ? 'active' : ''}`}
              onClick={() => toggle(name)}
            >
              <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} />
              {name}
            </button>
          ))}
          <span className="toggle-hint">세부 분류 표시</span>
        </div>
      )}

      <div className="flow-grid">
        {activeInvestors.map((name) => (
          <InvestorRow key={name} name={name} rows={seriesByInvestor[name] || []} />
        ))}
      </div>
    </div>
  )
}
