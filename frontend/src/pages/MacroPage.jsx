import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fetchMacroSeries } from '../api'
import PeriodPicker from '../components/PeriodPicker'
import { MACRO_SERIES } from '../constants'

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

function MacroChart({ id, label, unit, points }) {
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

// 환율(USD/KRW) · WTI · 브렌트 라인차트 3개 세로 배치 (PLAN.md §5.1/§6 1-3).
export default function MacroPage() {
  const [days, setDays] = useState(365)
  const [seriesMap, setSeriesMap] = useState({})
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(
      MACRO_SERIES.map((s) => s.id),
      days
    )
      .then((body) => {
        if (!cancelled) setSeriesMap(body.series || {})
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}

      {!loading && !error && (
        <div className="chart-stack">
          {MACRO_SERIES.map((s) => (
            <MacroChart key={s.id} id={s.id} label={s.label} unit={s.unit} points={seriesMap[s.id] || []} />
          ))}
        </div>
      )}
    </div>
  )
}
