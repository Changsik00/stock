import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchBasis,
  fetchForeignPositionIntradayAccumulated,
  fetchMarketSeries,
} from '../../api'
import ForeignPositionChart from '../ForeignPositionChart'
import IntradayFlowChart from '../IntradayFlowChart'
import PeriodPicker from '../PeriodPicker'
import { CHART_MODE_OPTIONS, INTRADAY_DAYS_OPTIONS, chartDateLabel, mergeFlows } from './shared'

// 외인 양손(현물·선물·베이시스) 섹션 — PLAN.md §4.5-5. 상세 모달(이 컴포넌트)은
// 시장 탭과 동일하게 90일 기본으로 시작한다(타일 최신값 계산용 짧은 창은
// DashboardPage.jsx의 FOREIGN_POSITION_TILE_DAYS 참고 — 원래 이 상수와 같은
// 주석 블록에 있었으나 파일 분리로 나뉘었다).
const DEFAULT_FOREIGN_POSITION_DAYS = 90

// 외인 양손 상세 — 외인 현물 vs 선물 순매수 시계열 + 베이시스 오버레이(PLAN.md §4.5-5
// 시그널 배지 클릭 시 열리는 모달). 코스피+코스닥(현물) + 선물 + 베이시스 3개 소스를
// 날짜 기준으로 병합한다 — CreditLoanChart(MarketFundChart.jsx)와 동일한 "여러 시리즈를
// Map으로 합친 뒤 라인 여러 개를 겹쳐 그리는" 패턴.
export default function ForeignPositionModal() {
  const [chartMode, setChartMode] = useState(STATIC_DATA ? '3M' : '1D')
  const [days, setDays] = useState(DEFAULT_FOREIGN_POSITION_DAYS)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [intraday, setIntraday] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)
  const [intradayDays, setIntradayDays] = useState(1)

  useEffect(() => {
    if (chartMode !== '3M') return undefined
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchMarketSeries('kospi', days),
      fetchMarketSeries('kosdaq', days),
      fetchMarketSeries('futures', days),
      fetchBasis(days),
    ])
      .then(([kospiBody, kosdaqBody, futuresBody, basisBody]) => {
        if (cancelled) return
        const spotRows = mergeFlows(kospiBody.flows, kosdaqBody.flows)['외국인'] || []
        const futuresRows = futuresBody.flows?.['외국인'] || []
        const basisRows = basisBody.series || []

        const byDate = new Map()
        const get = (date) => {
          if (!byDate.has(date)) byDate.set(date, { date, label: chartDateLabel(date) })
          return byDate.get(date)
        }
        for (const r of spotRows) get(r.date).spot = (r.net_value ?? 0) / 100
        for (const r of futuresRows) get(r.date).futures = (r.net_value ?? 0) / 100
        for (const r of basisRows) get(r.date).basis = r.basis

        setRows([...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1)))
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
  }, [chartMode, days])

  // 1D(장중 누적) — PLAN.md §5.4-3/4, FlowSummaryModal과 동일한 패턴.
  useEffect(() => {
    if (STATIC_DATA || chartMode !== '1D') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchForeignPositionIntradayAccumulated(intradayDays)
      .then((body) => {
        if (!cancelled) setIntraday(body)
      })
      .catch((e) => {
        if (!cancelled) setIntradayError(e.message)
      })
      .finally(() => {
        if (!cancelled) setIntradayLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [chartMode, intradayDays])

  // net_value는 백만원 단위 — ForeignPositionChart.jsx가 이미 하던 /100 변환과
  // 동일하게 억원으로 바꿔서 IntradayFlowChart에 넘긴다. 키를 "외국인"(현물)/
  // "외인선물"로 두는 이유: IntradayFlowChart는 시리즈 이름을 INVESTOR_COLOR_VAR
  // 조회 키로도 쓰므로, "외국인"으로 두면 3M 차트(ForeignPositionChart.jsx의
  // SPOT_COLOR = INVESTOR_COLOR_VAR['외국인'])와 동일한 색이 자동으로 나오고,
  // "외인선물"은 알 수 없는 키라 기본 색(var(--investor-6))으로 떨어져 3M
  // 차트의 FUTURES_COLOR와 같은 색이 된다 — 두 임의 라벨을 썼다면 둘 다 기본색으로
  // 겹쳐 구분이 안 됐을 것.
  const intradaySeries = {
    외국인: (intraday?.spot || []).map((p) => ({ time: p.time, value: p.value / 100 })),
    외인선물: (intraday?.futures || []).map((p) => ({ time: p.time, value: p.value / 100 })),
  }

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        외인 현물(코스피+코스닥) · 선물(K200) 순매수 + 베이시스 — 참고 지표(중립 계기판, 함정 탐지기 아님)
      </div>
      {!STATIC_DATA && (
        <div className="toggle-row">
          {CHART_MODE_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              className={`toggle-chip ${chartMode === opt.key ? 'active' : ''}`}
              onClick={() => setChartMode(opt.key)}
            >
              {opt.label}
            </button>
          ))}
          <span className="toggle-hint">
            {chartMode === '1D' ? '오늘 장중 누적(참고용) · 현물 60초, 선물 7분 틱' : '일별 히스토리 + 베이시스'}
          </span>
        </div>
      )}

      {chartMode === '3M' && (
        <>
          <PeriodPicker value={days} onChange={setDays} />
          {loading && <div className="state">불러오는 중…</div>}
          {error && <div className="state error">{error}</div>}
          {!loading && !error && <ForeignPositionChart data={rows} />}
        </>
      )}

      {chartMode === '1D' && (
        <>
          <div className="toggle-row">
            {INTRADAY_DAYS_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                className={`toggle-chip ${intradayDays === opt.key ? 'active' : ''}`}
                onClick={() => setIntradayDays(opt.key)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {intradayLoading && !intraday && <div className="state">불러오는 중…</div>}
          {intradayError && <div className="state error">{intradayError}</div>}
          {!intradayError && intraday && <IntradayFlowChart series={intradaySeries} />}
        </>
      )}
    </div>
  )
}
