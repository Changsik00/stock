import { useEffect, useState } from 'react'
import {
  fetchFlowConcentrationIntradayAccumulated,
  fetchForeignPositionIntradayAccumulated,
  fetchHynixRelativeStrength,
  fetchIndexTilesLive,
  fetchMacroSeries,
  fetchNasdaqFuturesLive,
  fetchPositioningHitrate,
  fetchRegime,
} from '../../api'
import RiskAlertBanner from '../RiskAlertBanner'
import { rateClass, rateLabel, signedEokLabel } from './shared'

// "오늘의 포지셔닝 재료" 브리핑(하이닉스 중심) — Top-down 프레임(PLAN.md §5.50-6/
// 5.50-7, 2026-08-03 사용자 확장: "①코스피/코스닥 수급이 어디로 쏠려있는가
// ②미장 선행지표가 어떤가 ③오전에 외인·기관이 선물·현물을 매수/매도하는가
// ④개별 종목이 어떻게 반응하는가 ⑤기타"). 5개 구획 모두 기존 엔드포인트
// 재조합 + 신규는 ④ 하이닉스 상대강도(fetchHynixRelativeStrength) 하나뿐 —
// 신규 TR 없음. AttentionFullModal/ExpiryPatternModal과 동일한 자기완결 패턴
// (마운트 시 자기 데이터를 자기가 fetch)이지만, 5개 구획이 서로 독립된 API를
// 쓰므로 Promise.allSettled로 병렬 호출해 하나가 실패해도 나머지 구획은 그대로
// 표시한다. **house rule(§5): 숫자만 관찰 서술하고 "강함/약함"·매매 판단은
// 이 컴포넌트의 어떤 텍스트에도 쓰지 않는다** — 종합 판단은 사용자 몫.
export default function HynixPositioningModal() {
  const [results, setResults] = useState(null) // Promise.allSettled 결과 배열 | null(로딩 중)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      fetchRegime(),
      fetchFlowConcentrationIntradayAccumulated(1),
      fetchMacroSeries(['us_sox'], 5),
      fetchForeignPositionIntradayAccumulated(1),
      fetchHynixRelativeStrength(),
      fetchIndexTilesLive(),
      fetchNasdaqFuturesLive(),
      fetchPositioningHitrate(),
    ]).then((r) => {
      if (!cancelled) setResults(r)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (!results) return <div className="state">불러오는 중…</div>

  const [
    regimeResult,
    concentrationResult,
    macroResult,
    foreignResult,
    hynixResult,
    tilesResult,
    nasdaqResult,
    hitrateResult,
  ] = results

  const regime = regimeResult.status === 'fulfilled' ? regimeResult.value : null
  const concentration = concentrationResult.status === 'fulfilled' ? concentrationResult.value : null
  const macro = macroResult.status === 'fulfilled' ? macroResult.value : null
  const foreign = foreignResult.status === 'fulfilled' ? foreignResult.value : null
  const hynix = hynixResult.status === 'fulfilled' ? hynixResult.value : null
  const tiles = tilesResult.status === 'fulfilled' ? tilesResult.value : null
  const nasdaq = nasdaqResult.status === 'fulfilled' ? nasdaqResult.value : null
  const hitrate = hitrateResult.status === 'fulfilled' ? hitrateResult.value : null
  const nasdaqLatest = nasdaq?.bars?.length ? nasdaq.bars[nasdaq.bars.length - 1] : null
  const nasdaqTime = nasdaqLatest?.time ? new Date(nasdaqLatest.time).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : null

  const concentrationLatest = concentration?.series?.length
    ? concentration.series[concentration.series.length - 1].value
    : null

  const soxSeries = macro?.series?.us_sox || []
  const soxLatest = soxSeries.length ? soxSeries[soxSeries.length - 1] : null
  const soxPrev = soxSeries.length > 1 ? soxSeries[soxSeries.length - 2] : null
  const soxChangeRate =
    soxLatest?.value != null && soxPrev?.value
      ? ((soxLatest.value - soxPrev.value) / soxPrev.value) * 100
      : null

  const spotLatest = foreign?.spot?.length ? foreign.spot[foreign.spot.length - 1].value : null
  const futuresLatest = foreign?.futures?.length ? foreign.futures[foreign.futures.length - 1].value : null

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 12 }}>
        아래 숫자는 모두 관찰치입니다. 매수/매도 신호가 아니며, 종합 판단은 직접 하시기 바랍니다.
      </div>

      {/* ① 코스피/코스닥 수급 쏠림 */}
      <div className="section-title">① 코스피/코스닥 수급 쏠림</div>
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
        <div className="toggle-hint">
          현재 쏠림 {concentrationLatest != null ? `${concentrationLatest.toFixed(1)}%` : '-'} (코스피 기준)
        </div>
      </div>

      {/* ② 미장 선행지표 */}
      <div className="section-title">② 미장 선행지표</div>
      <div style={{ marginBottom: 16 }}>
        {macroResult.status === 'rejected' && (
          <div className="state error">{macroResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {macroResult.status === 'fulfilled' && !soxLatest && <div className="state">표시할 데이터가 없습니다.</div>}
        {soxLatest && (
          <div>
            필라델피아반도체지수(SOX, 전일 마감) {soxLatest.value.toFixed(2)}
            {soxChangeRate != null && <span className={rateClass(soxChangeRate)}> ({rateLabel(soxChangeRate)})</span>}
          </div>
        )}
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

      {/* ③ 오전 외인·기관 현물·선물 */}
      <div className="section-title">③ 오전 외인·기관 현물·선물</div>
      <div style={{ marginBottom: 16 }}>
        {foreignResult.status === 'rejected' && (
          <div className="state error">{foreignResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {foreignResult.status === 'fulfilled' && spotLatest == null && futuresLatest == null && (
          <div className="state">표시할 데이터가 없습니다.</div>
        )}
        {spotLatest != null && (
          <div className={spotLatest >= 0 ? 'up' : 'down'}>외인 현물 오전~현재 누적 순매수 {signedEokLabel(spotLatest)}</div>
        )}
        {futuresLatest != null && (
          <div className={futuresLatest >= 0 ? 'up' : 'down'}>외인 선물 오전~현재 누적 순매수 {signedEokLabel(futuresLatest)}</div>
        )}
      </div>

      {/* ④ 하이닉스 개별 반응 */}
      <div className="section-title">④ 하이닉스 개별 반응</div>
      <div style={{ marginBottom: 16 }}>
        {hynixResult.status === 'rejected' && (
          <div className="state error">{hynixResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {hynix?.market_closed && <div className="state">장 마감 — 관찰치 없음.</div>}
        {hynix && !hynix.market_closed && hynix.relative_strength_pct == null && (
          <div className="state">표시할 데이터가 없습니다.</div>
        )}
        {hynix && !hynix.market_closed && hynix.relative_strength_pct != null && (
          <div className={rateClass(hynix.relative_strength_pct)}>
            하이닉스 등락률({rateLabel(hynix.hynix_change_rate)}) − 코스피 등락률({rateLabel(hynix.kospi_change_rate)}) ={' '}
            {rateLabel(hynix.relative_strength_pct)}p
          </div>
        )}
      </div>

      {/* ⑤ 기타 — 리스크 경보 */}
      <div className="section-title">⑤ 기타 — 리스크 경보</div>
      <div>
        {tilesResult.status === 'rejected' && (
          <div className="state error">{tilesResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {tiles && <RiskAlertBanner risk={tiles.risk} />}
        {tiles && !(tiles.risk?.alerts?.length > 0) && <div className="toggle-hint">현재 관찰된 경보가 없습니다.</div>}
      </div>

      {/* ⑥ 사후 검증(PLAN.md §5.52) — ①~⑤가 실제로 다음날 수익률과 관계가 있는지
          매일 쌓은 스냅샷(positioning_snapshot)을 그룹별로 집계한 표본수/평균/
          상승확률만 보여준다. n<min_samples(20)인 그룹은 아예 표에 넣지 않는다
          (표본 부족 은닉 금지 — 대신 total_days_collected로 "며칠째 수집 중인지"는
          항상 보여준다). house rule: "유리하다"류 판단 문구 절대 금지, 숫자만. */}
      <div className="section-title">⑥ 사후 검증</div>
      <div>
        {hitrateResult.status === 'rejected' && (
          <div className="state error">{hitrateResult.reason?.message || '불러오지 못했습니다.'}</div>
        )}
        {hitrate && (
          <div className="toggle-hint" style={{ marginBottom: 8 }}>
            수집 {hitrate.total_days_collected}일째(최소 {hitrate.min_samples}일 필요) — 아래 표는 표본
            {hitrate.min_samples}일 이상인 그룹만 표시합니다.
          </div>
        )}
        {hitrate && <PositioningHitrateTable hitrate={hitrate} />}
      </div>
    </div>
  )
}

// ⑥ 사후 검증 표(PLAN.md §5.52) — GET /api/markets/positioning-hitrate 응답의
// by_regime/by_relative_strength_sign/by_foreign_spot_sign/by_foreign_futures_sign/
// by_nasdaq_futures_sign 5개 그룹핑을 한 표로 펼친다. 백엔드가 이미 n<min_samples인
// 그룹은 avg_next_day_change_rate/positive_rate_pct를 null로 가려 보내므로, 이
// 컴포넌트는 그 null 여부로 "표시 가능한 행"만 걸러낼 뿐 자체적으로 판단하지 않는다.
const POSITIONING_HITRATE_GROUP_LABELS = {
  by_regime: '코스피/코스닥 우세',
  by_relative_strength_sign: '하이닉스 상대강도 부호',
  by_foreign_spot_sign: '외인 현물 누적 부호',
  by_foreign_futures_sign: '외인 선물 누적 부호',
  by_nasdaq_futures_sign: '나스닥선물 등락 부호',
}

const POSITIONING_HITRATE_SIGN_LABELS = { positive: '양수', negative: '음수' }

function PositioningHitrateTable({ hitrate }) {
  const rows = []
  for (const groupKey of Object.keys(POSITIONING_HITRATE_GROUP_LABELS)) {
    const groups = hitrate[groupKey] || {}
    for (const [label, stats] of Object.entries(groups)) {
      if (stats.avg_next_day_change_rate == null) continue // 표본 부족 — 표에 넣지 않음
      rows.push({
        category: POSITIONING_HITRATE_GROUP_LABELS[groupKey],
        label: POSITIONING_HITRATE_SIGN_LABELS[label] || label,
        ...stats,
      })
    }
  }

  if (rows.length === 0) {
    return <div className="state">표본 {hitrate.min_samples}일 이상 쌓인 그룹이 아직 없습니다.</div>
  }

  return (
    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border)' }}>
          <th style={{ textAlign: 'left', padding: '4px 6px' }}>구분</th>
          <th style={{ textAlign: 'left', padding: '4px 6px' }}>그룹</th>
          <th style={{ textAlign: 'right', padding: '4px 6px' }}>표본(n)</th>
          <th style={{ textAlign: 'right', padding: '4px 6px' }}>평균 다음날 수익률</th>
          <th style={{ textAlign: 'right', padding: '4px 6px' }}>상승확률</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.category}-${r.label}-${i}`} style={{ borderBottom: '1px solid var(--border)' }}>
            <td style={{ padding: '4px 6px', color: 'var(--text-muted)' }}>{r.category}</td>
            <td style={{ padding: '4px 6px' }}>{r.label}</td>
            <td style={{ padding: '4px 6px', textAlign: 'right' }}>{r.n}</td>
            <td className={rateClass(r.avg_next_day_change_rate)} style={{ padding: '4px 6px', textAlign: 'right' }}>
              {rateLabel(r.avg_next_day_change_rate)}
            </td>
            <td style={{ padding: '4px 6px', textAlign: 'right' }}>{r.positive_rate_pct.toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
