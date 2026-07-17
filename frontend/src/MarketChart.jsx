import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const dateFmt = (d) => `${d.slice(4, 6)}/${d.slice(6, 8)}`
const numFmt = new Intl.NumberFormat('ko-KR')

function priceTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  const up = row.changeRate >= 0
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>종가</span>
        <strong>{numFmt.format(row.close)}</strong>
      </div>
      <div className="tooltip-row">
        <span>등락률</span>
        <strong className={up ? 'up' : 'down'}>
          {up ? '+' : ''}
          {row.changeRate.toFixed(2)}%
        </strong>
      </div>
    </div>
  )
}

function valueTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>거래대금</span>
        <strong>{numFmt.format(Math.round(row.value / 1e8))}억원</strong>
      </div>
      <div className="tooltip-row">
        <span>거래량</span>
        <strong>{numFmt.format(row.volume)}</strong>
      </div>
    </div>
  )
}

export default function MarketChart({ series }) {
  const data = series.map((d) => ({ ...d, label: dateFmt(d.date) }))

  return (
    <div className="chart-stack">
      <div className="chart-card">
        <div className="chart-title">종가 추이</div>
        <ResponsiveContainer width="100%" height={220}>
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
            />
            <Tooltip content={priceTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            <Line
              type="monotone"
              dataKey="close"
              stroke="var(--series-price)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-card">
        <div className="chart-title">거래대금</div>
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={data} margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
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
              width={56}
              tickFormatter={(v) => numFmt.format(Math.round(v / 1e8))}
            />
            <Tooltip content={valueTooltip} cursor={{ fill: 'var(--chip-bg)' }} />
            <Bar dataKey="value" fill="var(--series-value)" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
