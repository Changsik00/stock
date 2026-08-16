import { useEffect, useState } from 'react'
import { fetchMacroSeries } from '../../api'
import MacroChart from '../MacroChart'
import PeriodPicker from '../PeriodPicker'
import { MACRO_SERIES, US_INDEX_SERIES } from '../../constants'

// 매크로(환율/유가) 모달 차트 기본 기간 — 옛 MacroPage.jsx와 동일하게 1Y로 시작한다
// (환율/유가는 자금 지표보다 변동 주기가 길어 1년 창이 자연스럽다는 기존 판단 유지).
const DEFAULT_MACRO_DAYS = 365

// 환율(USD/KRW) · WTI · 브렌트 라인차트 3개 + 전일 미국장 4대 지수(S&P500/나스닥/다우/SOX,
// PLAN.md §5.8) + 기간 선택 — 옛 MacroPage.jsx를 그대로 모달로 옮긴 것이다(차트 렌더
// 로직은 components/MacroChart.jsx로 뽑아 공용화). 타일은 환율/WTI/미국 4대 지수만 두지만
// (브렌트는 타일 생략 지시) 모달에서는 기존 3개 라인 + 미국 4대 지수까지 전부 보여준다
// (환율/유가와 스케일이 달라 섹션 제목으로만 구분하고 차트 자체는 나누지 않는다).
export default function MacroModal() {
  const [days, setDays] = useState(DEFAULT_MACRO_DAYS)
  const [seriesMap, setSeriesMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const allIds = [...MACRO_SERIES.map((s) => s.id), ...US_INDEX_SERIES.map((s) => s.id)]

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(allIds, days)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (
        <>
          <div className="chart-stack">
            {MACRO_SERIES.map((s) => (
              <MacroChart key={s.id} label={s.label} unit={s.unit} points={seriesMap[s.id] || []} />
            ))}
          </div>
          <div className="section-title" style={{ marginTop: 16 }}>
            전일 미국장 4대 지수
          </div>
          <div className="chart-stack">
            {US_INDEX_SERIES.map((s) => (
              <MacroChart key={s.id} label={s.label} unit={s.unit} points={seriesMap[s.id] || []} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
