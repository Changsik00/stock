import { useEffect, useState } from 'react'
import {
  fetchFlowConcentrationIntradayAccumulated,
  fetchIndexTilesLive,
  fetchMacroSeries,
  fetchNasdaqFuturesLive,
  fetchRegime,
} from '../../api'
import RiskAlertBanner from '../RiskAlertBanner'
import { rateClass, rateLabel } from './shared'

// "오늘의 매크로 브리핑" — 환율/유가/미국증시/수급쏠림·장세국면/리스크경보를
// 하나의 읽기 좋은 패널로 묶는다(사용자 요청: "매크로 상황을 따로 보기 좋게
// 보여주는 게 있냐" — HynixPositioningModal.jsx와 동일한 패턴 재사용, 신규
// 백엔드 없음). AttentionFullModal/HynixPositioningModal과 동일한 자기완결
// 패턴(마운트 시 자기 데이터를 자기가 fetch)이지만, 구획이 서로 독립된
// API를 쓰므로 Promise.allSettled로 병렬 호출해 하나가 실패해도 나머지
// 구획은 그대로 표시한다. **house rule(§5): 숫자만 관찰 서술하고
// "강함/약함"·매매 판단은 이 컴포넌트의 어떤 텍스트에도 쓰지 않는다** —
// 종합 판단은 사용자 몫.
const MACRO_SERIES_IDS = ['usdkrw', 'wti', 'brent', 'us_sp500', 'us_nasdaq', 'us_dow', 'us_sox']

const MACRO_ROWS = [
  { id: 'usdkrw', label: '원/달러 환율(USD/KRW)', unit: '원', fmt: (v) => v.toFixed(1) },
  { id: 'wti', label: 'WTI 유가', unit: '달러', fmt: (v) => v.toFixed(2) },
  { id: 'brent', label: '브렌트유', unit: '달러', fmt: (v) => v.toFixed(2) },
]

const US_INDEX_ROWS = [
  { id: 'us_sp500', label: 'S&P500', fmt: (v) => v.toFixed(2) },
  { id: 'us_nasdaq', label: '나스닥종합', fmt: (v) => v.toFixed(2) },
  { id: 'us_dow', label: '다우존스', fmt: (v) => v.toFixed(2) },
  { id: 'us_sox', label: 'SOX(필라델피아반도체)', fmt: (v) => v.toFixed(2) },
]

// macro_series 한 시리즈에서 최신값/전일값/등락률을 뽑는다(HynixPositioningModal.jsx의
// SOX 계산부와 동일한 방식 — 여러 시리즈에 반복 적용하려고 헬퍼로 뺐다).
function latestWithChange(seriesMap, id) {
  const series = seriesMap?.[id] || []
  const latest = series.length ? series[series.length - 1] : null
  const prev = series.length > 1 ? series[series.length - 2] : null
  const changeRate = latest?.value != null && prev?.value ? ((latest.value - prev.value) / prev.value) * 100 : null
  return { latest, prev, changeRate }
}

export default function MacroBriefingModal() {
  const [results, setResults] = useState(null) // Promise.allSettled 결과 배열 | null(로딩 중)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      fetchMacroSeries(MACRO_SERIES_IDS, 5),
      fetchNasdaqFuturesLive(),
      fetchRegime(),
      fetchFlowConcentrationIntradayAccumulated(1),
      fetchIndexTilesLive(),
    ]).then((r) => {
      if (!cancelled) setResults(r)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (!results) return <div className="state">불러오는 중…</div>

  const [macroResult, nasdaqResult, regimeResult, concentrationResult, tilesResult] = results

  const macro = macroResult.status === 'fulfilled' ? macroResult.value : null
  const nasdaq = nasdaqResult.status === 'fulfilled' ? nasdaqResult.value : null
  const regime = regimeResult.status === 'fulfilled' ? regimeResult.value : null
  const concentration = concentrationResult.status === 'fulfilled' ? concentrationResult.value : null
  const tiles = tilesResult.status === 'fulfilled' ? tilesResult.value : null

  const seriesMap = macro?.series || {}
  const nasdaqLatest = nasdaq?.bars?.length ? nasdaq.bars[nasdaq.bars.length - 1] : null
  const nasdaqTime = nasdaqLatest?.time
    ? new Date(nasdaqLatest.time).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
    : null

  const concentrationLatest = concentration?.series?.length
    ? concentration.series[concentration.series.length - 1].value
    : null

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 12 }}>
        아래 숫자는 모두 관찰치입니다. 매수/매도 신호가 아니며, 종합 판단은 직접 하시기 바랍니다.
      </div>

      {/* ① 환율 · 유가 */}
      <div className="section-title">① 환율 · 유가</div>
      <div style={{ marginBottom: 16 }}>
        {macroResult.status === 'rejected' && (
          <div className="state error">{macroResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {macroResult.status === 'fulfilled' &&
          MACRO_ROWS.map((row) => {
            const { latest, changeRate } = latestWithChange(seriesMap, row.id)
            return (
              <div key={row.id} style={{ marginTop: 4 }}>
                {row.label} {latest?.value != null ? `${row.fmt(latest.value)}${row.unit}` : '-'}
                {changeRate != null && <span className={rateClass(changeRate)}> ({rateLabel(changeRate)})</span>}
              </div>
            )
          })}
      </div>

      {/* ② 미국 증시 */}
      <div className="section-title">② 미국 증시</div>
      <div style={{ marginBottom: 16 }}>
        {macroResult.status === 'rejected' && (
          <div className="state error">{macroResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {macroResult.status === 'fulfilled' &&
          US_INDEX_ROWS.map((row) => {
            const { latest, changeRate } = latestWithChange(seriesMap, row.id)
            return (
              <div key={row.id} style={{ marginTop: 4 }}>
                {row.label}(전일 마감) {latest?.value != null ? row.fmt(latest.value) : '-'}
                {changeRate != null && <span className={rateClass(changeRate)}> ({rateLabel(changeRate)})</span>}
              </div>
            )
          })}
        {nasdaqResult.status === 'rejected' && (
          <div className="state error" style={{ marginTop: 4 }}>
            {nasdaqResult.reason?.message || '나스닥선물을 불러오지 못했습니다.'}
          </div>
        )}
        {nasdaqLatest && (
          <div style={{ marginTop: 4 }}>
            나스닥선물(NQ=F) 최신 {nasdaqLatest.close.toFixed(2)}
            {nasdaq.latest_change_pct != null && (
              <span className={rateClass(nasdaq.latest_change_pct)}> ({rateLabel(nasdaq.latest_change_pct)})</span>
            )}
            {nasdaqTime && <span className="toggle-hint"> ({nasdaqTime} 기준)</span>}
          </div>
        )}
      </div>

      {/* ③ 코스피/코스닥 수급 쏠림 · 장세 국면 */}
      <div className="section-title">③ 코스피/코스닥 수급 쏠림 · 장세 국면</div>
      <div style={{ marginBottom: 16 }}>
        {regimeResult.status === 'rejected' && (
          <div className="state error">{regimeResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {regimeResult.status === 'fulfilled' && !regime?.regime && <div className="state">표시할 데이터가 없습니다.</div>}
        {regime?.regime && (
          <div>
            <div>{regime.regime}</div>
            {regime.reason && <div className="toggle-hint">{regime.reason}</div>}
          </div>
        )}
        {concentrationResult.status === 'rejected' && (
          <div className="state error" style={{ marginTop: 4 }}>
            {concentrationResult.reason?.message || '수급 쏠림을 불러오지 못했습니다.'}
          </div>
        )}
        <div className="toggle-hint">
          현재 쏠림 {concentrationLatest != null ? `${concentrationLatest.toFixed(1)}%` : '-'} (코스피 기준)
        </div>
        <div className="toggle-hint">연속일수·가속도 등 자세한 내용은 대시보드의 "지금 유입 우세" 카드를 참고하세요.</div>
      </div>

      {/* ④ 리스크 경보 */}
      <div className="section-title">④ 리스크 경보</div>
      <div>
        {tilesResult.status === 'rejected' && (
          <div className="state error">{tilesResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {tiles && <RiskAlertBanner risk={tiles.risk} />}
        {tiles && !(tiles.risk?.alerts?.length > 0) && <div className="toggle-hint">현재 관찰된 경보가 없습니다.</div>}
      </div>
    </div>
  )
}
