import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { INVESTOR_COLOR_VAR } from '../constants'

// 외인 현물 vs 선물 순매수 시계열 + 베이시스 오버레이 (PLAN.md §4.5-5 "외인 양손"
// 시그널 상세 모달). CreditLoanChart(MarketFundChart.jsx)와 같은 "여러 시리즈를
// 날짜로 병합한 뒤 라인 여러 개를 겹쳐 그리는" 패턴을 따르되, 순매수(억원)와
// 베이시스(포인트)는 스케일이 완전히 달라 오른쪽 보조축(yAxisId="basis")을 추가로
// 쓴다 — 베이시스는 점선으로 구분해 "순매수 라인이 아니다"를 시각적으로도 표시한다.
//
// 현물 라인 색은 투자자 팔레트의 외국인 색(--investor-2)을 그대로 재사용한다 —
// 대시보드 "투자자별 수급 요약" 타일의 외국인 dot과 같은 색이라 "이게 그 외국인
// 얘기구나"를 별도 설명 없이 알아볼 수 있다. 선물 라인은 투자자 팔레트와 겹치지
// 않는 색(--investor-6)을 배정했다.

const eokFmt = new Intl.NumberFormat('ko-KR')
const basisFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

const SPOT_COLOR = INVESTOR_COLOR_VAR['외국인']
const FUTURES_COLOR = 'var(--investor-6)'
const BASIS_COLOR = 'var(--text-muted)'

function eokLabel(v) {
  if (v === null || v === undefined) return '-'
  return `${eokFmt.format(v)}억원`
}

function chartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>외인 현물</span>
        <strong className={row.spot >= 0 ? 'up' : 'down'}>{eokLabel(row.spot)}</strong>
      </div>
      <div className="tooltip-row">
        <span>외인 선물</span>
        <strong className={row.futures >= 0 ? 'up' : 'down'}>{eokLabel(row.futures)}</strong>
      </div>
      <div className="tooltip-row">
        <span>베이시스</span>
        <strong>{row.basis === null || row.basis === undefined ? '-' : `${basisFmt.format(row.basis)}pt`}</strong>
      </div>
    </div>
  )
}

// data: [{date, label, spot, futures, basis}] — 호출부(DashboardPage.jsx
// ForeignPositionModal)가 코스피+코스닥 합산 외인 순매수(현물) + k200_futures 외인
// 순매수(선물) + basis 시계열을 날짜 기준으로 병합해서 넘긴다.
export default function ForeignPositionChart({ data }) {
  const hasData = data.length > 0

  return (
    <div className="chart-card">
      <div className="chart-title">외인 현물 vs 선물 순매수 · 베이시스</div>
      {!hasData ? (
        <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
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
              yAxisId="net"
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
              width={64}
              tickFormatter={(v) => eokFmt.format(v)}
              label={{ value: '순매수(억원)', angle: -90, position: 'insideLeft', fill: 'var(--text-muted)', fontSize: 11 }}
            />
            <YAxis
              yAxisId="basis"
              orientation="right"
              stroke="var(--axis)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
              width={56}
              tickFormatter={(v) => basisFmt.format(v)}
              label={{ value: '베이시스(pt)', angle: 90, position: 'insideRight', fill: 'var(--text-muted)', fontSize: 11 }}
            />
            <ReferenceLine yAxisId="net" y={0} stroke="var(--axis)" strokeDasharray="3 3" />
            <Tooltip content={chartTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value) => value} />
            <Line
              yAxisId="net"
              type="monotone"
              name="외인 현물"
              dataKey="spot"
              stroke={SPOT_COLOR}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 3 }}
              connectNulls
            />
            <Line
              yAxisId="net"
              type="monotone"
              name="외인 선물"
              dataKey="futures"
              stroke={FUTURES_COLOR}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 3 }}
              connectNulls
            />
            <Line
              yAxisId="basis"
              type="monotone"
              name="베이시스"
              dataKey="basis"
              stroke={BASIS_COLOR}
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 3 }}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
