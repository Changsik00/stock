import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

// "등락 종목수" 1D 상승비율 추이 — PLAN.md §5.13. "오늘 오르는 종목이 많은지 적은지를
// 시간순으로 보고 싶다"는 사용자 요청으로, 순간 스냅샷(BreadthBadge)만으로는 놓치는
// 시간 흐름을 라인차트로 보여준다. IntradayFlowChart와 데이터 소스 성격은 같지만
// (collectors/intraday_snapshot.py의 "장중 자체 샘플링 누적" 패턴 재사용) 투자자별
// 다중 시리즈가 아니라 단일 시리즈(등락비율 %)이고 기준선이 0이 아니라 50이라 별도
// 경량 컴포넌트로 뺐다 — IntradayFlowChart를 프랍화해서 억지로 겸용하면 investor
// 다중 시리즈 로직(색상 매핑, 시장별 병합 등)까지 딸려와 오히려 더 복잡해진다.
//
// §5 "중립 계기판" 원칙 — 50% 위/아래를 "매수/매도 신호"처럼 색으로 판단하지 않는다.
// 단일 라인은 up/down(빨강/파랑) 대신 --series-price(중립 액센트)를 쓴다(MacroChart.jsx와
// 동일한 관례) — 값 자체(상승비율)와 등락 방향 색상 관행을 혼동하지 않기 위해서다.
//
// props.series는 [{time: "HH:MM", value}] 형태(value는 0~100 상승비율 %, 백엔드
// get_breadth_series()가 이미 계산해서 넘긴다 — 프런트는 추가 변환을 하지 않는다).

const numFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1 })

function pctLabel(v) {
  if (v === null || v === undefined) return '-'
  return `${numFmt.format(v)}%`
}

function chartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const v = payload[0].value
  return (
    <div className="tooltip">
      <div className="tooltip-date">{label}</div>
      <div className="tooltip-row">
        <span>상승비율</span>
        <strong>{pctLabel(v)}</strong>
      </div>
    </div>
  )
}

export default function BreadthRatioChart({ series }) {
  const data = series || []
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
              domain={[0, 100]}
              width={48}
              tickFormatter={(v) => `${v}%`}
              label={{ value: '상승비율(%)', angle: -90, position: 'insideLeft', fill: 'var(--text-muted)', fontSize: 11 }}
            />
            <ReferenceLine y={50} stroke="var(--axis)" strokeDasharray="3 3" />
            <Tooltip content={chartTooltip} cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }} />
            <Line
              type="monotone"
              dataKey="value"
              stroke="var(--series-price)"
              strokeWidth={2}
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
