import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchBreadth,
  fetchBreadthIntradayAccumulated,
  fetchBreadthLive,
} from '../../api'
import BreadthBadge from '../BreadthBadge'
import BreadthRatioChart from '../BreadthRatioChart'
import { BREADTH_LIVE_POLL_MS, BREADTH_MODE_OPTIONS, INTRADAY_DAYS_OPTIONS } from './shared'

export default function BreadthModal() {
  // PLAN.md §5.13 — "오늘 오르는 종목이 많은지 적은지를 시간순으로 보고 싶다"는
  // 요청으로 기존 "현재"(순간 스냅샷) 탭에 "1D 추이"(상승비율 라인차트) 탭을
  // 추가했다. FlowSummaryModal의 chartMode/toggle-row 패턴을 재사용하되, 이 모달은
  // 3M 히스토리 차트가 없어 CHART_MODE_OPTIONS(1D/3M) 대신 BREADTH_MODE_OPTIONS
  // (현재/1D 추이)를 쓴다.
  const [chartMode, setChartMode] = useState('live')
  const [breadth, setBreadth] = useState(null)
  const [error, setError] = useState(null)

  const [intraday, setIntraday] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)
  const [intradayDays, setIntradayDays] = useState(1)

  useEffect(() => {
    if (chartMode !== 'live') return undefined
    let cancelled = false
    const toCamel = (row) =>
      row
        ? {
            adv: row.adv,
            dec: row.dec,
            flat: row.flat,
            limitUp: row.limit_up ?? row.limitUp,
            limitDown: row.limit_down ?? row.limitDown,
          }
        : null

    async function load() {
      try {
        const body = await fetchBreadthLive()
        if (cancelled) return
        setBreadth({
          kospi: toCamel(body.kospi),
          kosdaq: toCamel(body.kosdaq),
          live: body.live !== false,
          date: body.kospi?.date || body.kosdaq?.date || null,
        })
        return
      } catch {
        // 장중 소스 실패 — 일별 확정치로 폴백 (MarketPage와 동일 패턴).
      }
      try {
        const [kospiBody, kosdaqBody] = await Promise.all([
          fetchBreadth('kospi', 30).catch(() => null),
          fetchBreadth('kosdaq', 30).catch(() => null),
        ])
        if (cancelled) return
        const last = (body) => {
          const series = body?.series
          return series && series.length > 0 ? series[series.length - 1] : null
        }
        const kospiRow = last(kospiBody)
        const kosdaqRow = last(kosdaqBody)
        if (!kospiRow && !kosdaqRow) {
          setError('등락 종목수 데이터를 불러오지 못했습니다.')
          return
        }
        setBreadth({
          kospi: toCamel(kospiRow),
          kosdaq: toCamel(kosdaqRow),
          live: false,
          date: kospiRow?.date || kosdaqRow?.date || null,
        })
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }

    load()
    // 모달이 열려 있는 동안에도 백엔드 60초 캐시와 맞춰 계속 갱신한다 — 이전에는
    // 최초 1회만 fetch하고 끝나서 모달을 오래 띄워둬도 값이 갱신되지 않았다
    // (DashboardPage 본문의 동일 패턴 useEffect와 같은 수정, PLAN.md 서버 측 능동
    // 60초 갱신 작업 참고). "1D 추이" 탭을 보는 동안에는 이 폴링이 필요 없으므로
    // chartMode가 'live'일 때만 돈다.
    const intervalId = setInterval(load, BREADTH_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [chartMode])

  // 1D(오늘 장중 누적 등락비율) — PLAN.md §5.13, FlowSummaryModal의 1D 탭과 동일한
  // 패턴(STATIC_DATA에는 로컬 전용 라이브 폴링이 없어 탭 자체를 숨기고 요청하지
  // 않는다, 모달이 열려 있는 동안 재폴링 없음).
  useEffect(() => {
    if (STATIC_DATA || chartMode !== '1D') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchBreadthIntradayAccumulated(intradayDays)
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
            {chartMode === '1D' ? '오늘 장중 누적(참고용) · 상승 대 하락 비율 60초 틱, 보합 제외' : '실시간 스냅샷'}
          </span>
        </div>
      )}

      {chartMode === 'live' && (
        <>
          {breadth && (
            <div className="toggle-hint" style={{ marginBottom: 8 }}>
              등락 종목수 — {breadth.live ? '장중 잠정치 (60초 캐시)' : '일별 확정치'}
            </div>
          )}
          {error && <div className="state error">{error}</div>}
          {breadth && (
            <BreadthBadge kospi={breadth.kospi} kosdaq={breadth.kosdaq} date={breadth.live ? null : breadth.date} />
          )}
          {!breadth && !error && <div className="state">불러오는 중…</div>}
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
          {!intradayError && intraday && <BreadthRatioChart series={intraday.series} />}
        </>
      )}
    </div>
  )
}
