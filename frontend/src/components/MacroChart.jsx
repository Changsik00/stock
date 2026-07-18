import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

// 매크로 시계열(환율/유가 등) 라인차트 1개 카드 — 원래 MacroPage.jsx 안에서만 쓰이던
// 컴포넌트였으나, 매크로 탭이 대시보드 타일+모달로 통합되면서(PLAN.md §6 3.7-1 계열
// 후속 지시) DashboardPage의 매크로 모달과 공용으로 쓰기 위해 별도 파일로 뺐다.

const numFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 })

function dateLabel(iso) {
  const [, m, d] = iso.split('-')
  return `${m}/${d}`
}

function makeTooltip(unit) {
  return function MacroTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null
    const row = payload[0].payload
    return (
      <div className="tooltip">
        <div className="tooltip-date">{label}</div>
        <div className="tooltip-row">
          <span>값</span>
          <strong>
            {numFmt.format(row.value)} {unit}
          </strong>
        </div>
      </div>
    )
  }
}

export default function MacroChart({ label, unit, points }) {
  const hasData = points.length > 0
  const data = points.map((p) => ({ ...p, label: dateLabel(p.date) }))
  const Tip = makeTooltip(unit)

  return (
    <div className="chart-card">
      <div className="chart-title">{label}</div>
      {!hasData ? (
        <div className="state">데이터 수집 대기 (ECOS 키 필요)</div>
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
            />
            <Tooltip content={Tip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
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
