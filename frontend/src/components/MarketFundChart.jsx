import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CREDIT_LOAN_SERIES } from '../constants'

const trillionFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

function dateLabel(iso) {
  const [, m, d] = iso.split('-')
  return `${m}/${d}`
}

// macro_series는 백만원 단위로 적재돼 있다 (PLAN.md §3.5) — 1조원 = 1,000,000백만원이므로
// 조원 환산은 /1e6. 화면에는 항상 "N.N조" 형태로만 노출해 큰 숫자를 읽기 쉽게 한다.
function trillion(value) {
  if (value === null || value === undefined) return null
  return value / 1e6
}

function trillionLabel(value) {
  if (value === null || value === undefined) return '-'
  return `${trillionFmt.format(value)}조`
}

function singleTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>값</span>
        <strong>{trillionLabel(row.value)}</strong>
      </div>
    </div>
  )
}

function creditTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      {CREDIT_LOAN_SERIES.map((s) => (
        <div className="tooltip-row" key={s.id}>
          <span>{s.label}</span>
          <strong>{trillionLabel(row[s.id])}</strong>
        </div>
      ))}
    </div>
  )
}

// 단일 라인차트 — 투자자예탁금 / 대차잔고 공용 (조원 환산).
function SingleFundChart({ label, points }) {
  const hasData = points.length > 0
  const data = points.map((p) => ({ label: dateLabel(p.date), value: trillion(p.value) }))

  return (
    <div className="chart-card">
      <div className="chart-title">{label}</div>
      {!hasData ? (
        <div className="state">데이터 수집 대기</div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="label"
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
              width={56}
              tickFormatter={(v) => trillionFmt.format(v)}
            />
            <Tooltip content={singleTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            <Line
              type="monotone"
              dataKey="value"
              stroke="var(--series-price)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// 코스피/코스닥 신용융자 잔고 — 날짜 기준으로 두 시리즈를 한 데이터셋으로 병합해
// 라인 두 개를 겹쳐 그린다.
function CreditLoanChart({ seriesMap }) {
  const byDate = new Map()
  for (const s of CREDIT_LOAN_SERIES) {
    for (const p of seriesMap[s.id] || []) {
      const row = byDate.get(p.date) || { date: p.date, label: dateLabel(p.date) }
      row[s.id] = trillion(p.value)
      byDate.set(p.date, row)
    }
  }
  const data = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1))
  const hasData = data.length > 0

  return (
    <div className="chart-card">
      <div className="chart-title">신용융자 잔고 (코스피·코스닥)</div>
      {!hasData ? (
        <div className="state">데이터 수집 대기</div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="label"
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
              width={56}
              tickFormatter={(v) => trillionFmt.format(v)}
            />
            <Tooltip content={creditTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => value} />
            {CREDIT_LOAN_SERIES.map((s) => (
              <Line
                key={s.id}
                type="monotone"
                name={s.label}
                dataKey={s.id}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                activeDot={{ r: 4 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// 투자자예탁금 · 신용융자(코스피/코스닥) · 대차잔고 — 시장 자금·대차 보조 차트
// (PLAN.md §3.5, §6 1.5-4). 등락 종목수(breadth) UI는 1.5-3 선행 대기 중이라 여기 없음.
export default function MarketFundChart({ seriesMap }) {
  return (
    <div className="chart-stack">
      <SingleFundChart label="투자자예탁금" points={seriesMap.investor_deposit || []} />
      <CreditLoanChart seriesMap={seriesMap} />
      <SingleFundChart label="대차잔고" points={seriesMap.lending_balance || []} />
    </div>
  )
}
