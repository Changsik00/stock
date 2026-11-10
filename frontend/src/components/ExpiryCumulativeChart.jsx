import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { signedEokJoLabel } from '../format'

// 누적 수급 라인차트(Phase 5.100/5.101) — ExpiryPatternChart.jsx와 동일한 recharts 컨벤션.
// 왼쪽 축: 외국인 현물·외국인 선물 누적(백만원 입력, 억/조 표기), showInst이면 기관계 현물 추가.
// 오른쪽 축: 같은 구간 지수 등락률(%) 얇은 중립색 선. 방향색(--up/--down)은 가격 방향
// 전용이라 선 색에는 쓰지 않는다. 현물 값이 없는 구간(spot_foreign 없음)은 해당 선을 그리지 않는다.

const pctLabel = (v) => (typeof v === 'number' ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}%` : '-')

function CumTooltip({ active, payload, label, showInst }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{row.date} ({label})</div>
      {typeof row.spot_foreign === 'number' && (
        <div className="tooltip-row">
          <span>외국인 현물</span>
          <strong>{signedEokJoLabel(row.spot_foreign)}</strong>
        </div>
      )}
      <div className="tooltip-row">
        <span>외국인 선물</span>
        <strong>{signedEokJoLabel(row.foreign)}</strong>
      </div>
      {showInst && typeof row.spot_institution === 'number' && (
        <div className="tooltip-row">
          <span>기관계 현물</span>
          <strong>{signedEokJoLabel(row.spot_institution)}</strong>
        </div>
      )}
      {showInst && (
        <div className="tooltip-row">
          <span>기관계 선물</span>
          <strong>{signedEokJoLabel(row.institution)}</strong>
        </div>
      )}
      <div className="tooltip-row">
        <span>지수 등락률</span>
        <strong>{pctLabel(row.index_pct)}</strong>
      </div>
    </div>
  )
}

export default function ExpiryCumulativeChart({ curve, showInst = false }) {
  if (!curve?.length) {
    return (
      <div className="chart-card">
        <div className="state">표시할 데이터가 없습니다.</div>
      </div>
    )
  }
  const data = curve.map((p) => ({ ...p, label: p.date.slice(5) }))
  const hasSpot = data.some((p) => typeof p.spot_foreign === 'number')
  const hasIdx = data.some((p) => typeof p.index_pct === 'number')
  const tick = { fill: 'var(--text-muted)', fontSize: 12 }
  const line = { dot: false, isAnimationActive: false, activeDot: { r: 4 }, connectNulls: true }
  return (
    <div className="chart-card">
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" stroke="var(--axis)" tick={tick} tickLine={false} minTickGap={24} />
          <YAxis
            yAxisId="flow"
            stroke="var(--axis)"
            tick={tick}
            tickLine={false}
            axisLine={false}
            domain={['auto', 'auto']}
            width={60}
            tickFormatter={signedEokJoLabel}
          />
          {hasIdx && (
            <YAxis
              yAxisId="idx"
              orientation="right"
              stroke="var(--axis)"
              tick={tick}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
              width={48}
              tickFormatter={pctLabel}
            />
          )}
          <ReferenceLine yAxisId="flow" y={0} stroke="var(--axis)" strokeDasharray="3 3" />
          <Tooltip content={(props) => <CumTooltip {...props} showInst={showInst} />} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
          <Legend
            verticalAlign="top"
            height={28}
            formatter={(value) => <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{value}</span>}
          />
          {hasSpot && <Line yAxisId="flow" type="monotone" dataKey="spot_foreign" name="외인 현물" stroke="var(--series-price)" strokeWidth={2} {...line} />}
          <Line yAxisId="flow" type="monotone" dataKey="foreign" name="외인 선물" stroke="var(--series-value)" strokeWidth={2} strokeDasharray="4 3" {...line} />
          {showInst && hasSpot && <Line yAxisId="flow" type="monotone" dataKey="spot_institution" name="기관 현물" stroke="var(--text-secondary)" strokeWidth={1.5} {...line} />}
          {showInst && <Line yAxisId="flow" type="monotone" dataKey="institution" name="기관 선물" stroke="var(--text-secondary)" strokeWidth={1.5} strokeDasharray="4 3" {...line} />}
          {hasIdx && <Line yAxisId="idx" type="monotone" dataKey="index_pct" name="지수 등락률(우)" stroke="var(--text-muted)" strokeWidth={1} {...line} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
