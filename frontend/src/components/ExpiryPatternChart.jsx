import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

// K200 선물 만기 수렴 패턴(PLAN.md §5.42) 라인차트 — MacroChart.jsx와 동일한
// recharts 컨벤션(스타일 변수, 툴팁 카드, 반응형 컨테이너)을 그대로 따르되,
// 두 시리즈(평균/중앙값)를 함께 그린다 — §5.40(스켈핑 적중률)이 표본이 작을 때
// 평균만 보여주면 극단치에 끌린 결과를 감출 위험이 있어 평균+중앙값을 함께
// 반환한 것과 동일한 이유. X축은 D-day(가장 먼 과거 -> 만기 당일 0), Y축은
// 베이시스(선물종가-현물종가, pt).

const numFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 })

function dDayLabel(dDay) {
  return dDay === 0 ? 'D-0(만기)' : `D${dDay}`
}

function ExpiryPatternTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>평균 베이시스</span>
        <strong>{numFmt.format(row.mean_basis)}pt</strong>
      </div>
      <div className="tooltip-row">
        <span>중앙값 베이시스</span>
        <strong>{numFmt.format(row.median_basis)}pt</strong>
      </div>
      <div className="tooltip-row">
        <span>표본(사이클)</span>
        <strong>{row.n}회</strong>
      </div>
    </div>
  )
}

export default function ExpiryPatternChart({ points }) {
  const hasData = points.length > 0
  const data = points.map((p) => ({ ...p, label: dDayLabel(p.d_day) }))

  if (!hasData) {
    return (
      <div className="chart-card">
        <div className="state">표시할 데이터가 없습니다.</div>
      </div>
    )
  }

  return (
    <div className="chart-card">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--axis)"
            tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
            tickLine={false}
            minTickGap={16}
          />
          <YAxis
            stroke="var(--axis)"
            tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
            tickLine={false}
            axisLine={false}
            domain={['auto', 'auto']}
            width={56}
          />
          <ReferenceLine y={0} stroke="var(--axis)" strokeDasharray="3 3" />
          <Tooltip content={ExpiryPatternTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
          <Legend
            verticalAlign="top"
            height={28}
            formatter={(value) => <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{value}</span>}
          />
          <Line
            type="monotone"
            dataKey="mean_basis"
            name="평균"
            stroke="var(--series-price)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            activeDot={{ r: 4 }}
          />
          <Line
            type="monotone"
            dataKey="median_basis"
            name="중앙값"
            stroke="var(--series-value)"
            strokeWidth={2}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
