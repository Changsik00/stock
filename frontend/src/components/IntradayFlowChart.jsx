import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { INVESTOR_COLOR_VAR } from '../constants'

// "오늘 장중 누적" 1D 차트 — PLAN.md §5.4-4. FlowSummaryModal(투자자별 수급 요약)과
// ForeignPositionModal(외인 양손) 두 상세 모달의 1D 탭이 공유하는 재사용 컴포넌트다.
// ka10051에는 분단위 이력이 없어 서버가 직접 만들어 낼 수 없는 "장중 추이"를,
// 이미 60초/7분마다 도는 라이브 폴링 결과를 그날 메모리에 스냅샷으로 적립해
// 자체 생성한 것이 데이터 소스다(collectors/intraday_snapshot.py 참고) — 그래서
// 시리즈마다 점 간격이 다를 수 있다(개인/외국인/기관계는 ~60초, 외인선물은 ~7분).
//
// props.series는 {표시명: [{time: "HH:MM", value}]} 형태이고, value는 호출부가
// 이미 억원 단위로 변환해서 넘긴다(FlowChart.jsx/ForeignPositionChart.jsx의 eok()
// 관례와 통일 — 이 컴포넌트 내부에서는 추가 단위 변환을 하지 않는다).
const numFmt = new Intl.NumberFormat('ko-KR')

function eokLabel(v) {
  if (v === null || v === undefined) return '-'
  return `${numFmt.format(Math.round(v))}억원`
}

function chartTooltip({ active, payload, label, seriesNames }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      {seriesNames.map((name) => {
        const v = row[name]
        if (v === undefined || v === null) return null
        return (
          <div className="tooltip-row" key={name}>
            <span>
              <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] || 'var(--investor-6)' }} /> {name}
            </span>
            <strong className={v >= 0 ? 'up' : 'down'}>{eokLabel(v)}</strong>
          </div>
        )
      })}
    </div>
  )
}

export default function IntradayFlowChart({ series }) {
  const seriesNames = Object.keys(series || {})

  // 모든 시리즈의 time 값을 하나의 정렬된 x축으로 병합한다(ForeignPositionModal/
  // MarketFundChart.jsx의 "byDate Map 병합" 패턴과 동일, 키만 date 대신 time).
  const byTime = new Map()
  for (const name of seriesNames) {
    for (const p of series[name] || []) {
      const row = byTime.get(p.time) || { time: p.time }
      row[name] = p.value
      byTime.set(p.time, row)
    }
  }
  const data = [...byTime.values()].sort((a, b) => (a.time < b.time ? -1 : 1))
  const totalPoints = data.length

  return (
    <div className="chart-card">
      {totalPoints === 0 ? (
        <div className="state">적립 중 — 잠시 후 다시 확인</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="time"
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
              width={64}
              tickFormatter={(v) => numFmt.format(v)}
              label={{ value: '순매수(억원)', angle: -90, position: 'insideLeft', fill: 'var(--text-muted)', fontSize: 11 }}
            />
            <ReferenceLine y={0} stroke="var(--axis)" strokeDasharray="3 3" />
            <Tooltip content={(props) => chartTooltip({ ...props, seriesNames })} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            {seriesNames.map((name) => (
              <Line
                key={name}
                type="monotone"
                name={name}
                dataKey={name}
                stroke={INVESTOR_COLOR_VAR[name] || 'var(--investor-6)'}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                activeDot={{ r: 3 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
