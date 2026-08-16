import { useEffect, useState } from 'react'
import { STATIC_DATA, fetchFlowConcentrationIntradayAccumulated, fetchFlowLive } from '../../api'
import BreadthRatioChart from '../BreadthRatioChart'
import {
  BREADTH_LIVE_POLL_MS,
  BREADTH_MODE_OPTIONS,
  INTRADAY_DAYS_OPTIONS,
  computeConcentration,
  scoreFmt,
} from './shared'

export default function ConcentrationModal({ regime }) {
  // PLAN.md §5.18 — "외인, 기관이 적극 매수해야 코스피/코스닥이 오른다"는 관찰에서,
  // 그 돈이 어느 시장으로 쏠리는지를 "현재"(순간 관찰)와 "1D 추이"(BreadthModal과
  // 완전히 동일한 패턴, BreadthRatioChart 재사용) 두 탭으로 보여준다. "현재" 탭은
  // BreadthModal의 live 탭과 동일하게 flow/live(이미 다른 곳에서도 쓰는 기존
  // 엔드포인트, 새 호출 아님)를 자체 폴링해 computeConcentration으로 즉석 계산한다
  // — DashboardPage 본문의 KPI 타일이 쓰는 헬퍼와 동일해 숫자가 항상 일치한다.
  const [chartMode, setChartMode] = useState('live')
  const [flowLive, setFlowLive] = useState(null)
  const [liveError, setLiveError] = useState(null)

  const [intraday, setIntraday] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)
  const [intradayDays, setIntradayDays] = useState(1)

  useEffect(() => {
    if (STATIC_DATA || chartMode !== 'live') return undefined
    let cancelled = false
    async function load() {
      try {
        const body = await fetchFlowLive()
        if (!cancelled) {
          setFlowLive(body)
          setLiveError(null)
        }
      } catch (e) {
        if (!cancelled) setLiveError(e.message)
      }
    }
    load()
    // 모달이 열려 있는 동안 계속 갱신 — BreadthModal의 동일한 폴링 관례.
    const intervalId = setInterval(load, BREADTH_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [chartMode])

  useEffect(() => {
    if (STATIC_DATA || chartMode !== '1D') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchFlowConcentrationIntradayAccumulated(intradayDays)
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

  // PLAN.md §5.19 — regime(이미 1분 티어에서 폴링 중, prop으로 전달됨)의
  // activity_baseline으로 배율 정규화한다. regime이 아직 없거나 baseline이
  // 없으면 computeConcentration이 null을 돌려주고, 아래 "적립 중" 분기로
  // 그대로 흘러간다(별도 빈 상태 분기를 새로 만들지 않는다).
  const concentrationBaseline = {
    kospi: regime?.kospi?.activity_baseline?.avg_daily_activity ?? null,
    kosdaq: regime?.kosdaq?.activity_baseline?.avg_daily_activity ?? null,
  }
  const concentration = computeConcentration(flowLive, concentrationBaseline)

  return (
    <div>
      {!STATIC_DATA && (
        <div className="toggle-row">
          {BREADTH_MODE_OPTIONS.map((opt) => (
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
            쏠림% = 코스피 평소 대비 배율 / (코스피+코스닥 평소 대비 배율) × 100 — 각 시장을 자기 자신의 최근
            20거래일 평소 활동량(활동량 = |외국인 순매수|+|기관계 순매수|)과 비교한 뒤 비교해 시장 규모 차이를 보정
          </span>
        </div>
      )}

      {chartMode === 'live' && (
        <>
          {liveError && <div className="state error">{liveError}</div>}
          {!liveError && !concentration && <div className="state">적립 중 — 잠시 후 다시 확인</div>}
          {concentration && (
            // §5 "중립 계기판" 원칙 — "쏠려서 위험하다/좋다" 같은 가치 판단 없이
            // 어느 쪽 활동이 더 많은지만 관찰 서술한다.
            <div className="toggle-hint" style={{ marginBottom: 8 }}>
              코스피 활동 비중 {scoreFmt.format(concentration.kospiShare)}% · 코스닥{' '}
              {scoreFmt.format(concentration.kosdaqShare)}% — {concentration.moreActive} 쪽 활동이 더 많다
              <br />
              코스피 평소 대비 {scoreFmt.format(concentration.kospiMultiple)}배 · 코스닥 평소 대비{' '}
              {scoreFmt.format(concentration.kosdaqMultiple)}배
            </div>
          )}
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
          {!intradayError && intraday && <BreadthRatioChart series={intraday.series} valueLabel="코스피 쏠림" />}
        </>
      )}
    </div>
  )
}
