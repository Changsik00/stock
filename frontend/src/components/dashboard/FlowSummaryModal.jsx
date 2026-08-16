import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchFlowIntradayAccumulated,
  fetchForeignPositionIntradayAccumulated,
  fetchMarketSeries,
} from '../../api'
import FlowChart from '../FlowChart'
import IntradayFlowChart from '../IntradayFlowChart'
import PeriodPicker from '../PeriodPicker'
import {
  CHART_MODE_OPTIONS,
  INTRADAY_DAYS_OPTIONS,
  mergeFlows,
  mergeIntradayByTime,
} from './shared'

// 투자자별 수급 요약 타일 + 모달 — 시장 탭과 동일하게 3M 기본.
const DEFAULT_FLOW_DAYS = 90

// FlowSummaryModal(투자자별 수급 요약) 3M/1D 공통 시장 필터(PLAN.md §5.10,
// 2026-07-22) — "코스피로 다 몰려 있어서 코스닥이 주목 받는 날을 못 본다"는
// 사용자 요구로 코스피/코스닥을 분리해 볼 수 있게 한 토글. VALUE_RANK_MARKET_OPTIONS와
// 같은 3분기 패턴이지만 라벨만 "합계"로 다르다(거래대금 상위는 "전체"가 더
// 자연스럽고, 여기는 두 시장을 더한 값이라 "합계"가 더 정확한 표현). 기본값은
// 지금까지의 동작과 동일한 'all'(합계).
// 'futures'(선물)는 PLAN.md §5.45에서 추가 — "현물/선물 구분이 안 보인다"는
// 사용자 지적으로 4번째 옵션을 붙였다. '합계'는 기존 그대로 코스피+코스닥
// 현물 합계만 의미하며(의미 변경 없음), 선물은 별도 명시적 선택지로만 노출한다.
const FLOW_MARKET_FILTER_OPTIONS = [
  { key: 'all', label: '합계' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
  { key: 'futures', label: '선물' },
]

export default function FlowSummaryModal() {
  const [chartMode, setChartMode] = useState(STATIC_DATA ? '3M' : '1D')
  // 코스피/코스닥 분리 토글(PLAN.md §5.10, 2026-07-22) — 기본은 지금까지의
  // 동작과 동일한 'all'(합계), 3M/1D 두 탭 모두 이 필터를 공유한다.
  const [marketFilter, setMarketFilter] = useState('all')
  const [days, setDays] = useState(DEFAULT_FLOW_DAYS)
  // 3M: 코스피/코스닥/선물 원본 flows를 각각 보관해 두고(이미 fetch하던 그대로),
  // marketFilter가 바뀔 때 재요청 없이 즉시 합계/개별로 다시 계산한다. 선물은
  // PLAN.md §5.45 — futures가 추가돼도 '합계'(코스피+코스닥 현물)의 의미는
  // 바꾸지 않고 별도 필터 값으로만 노출한다.
  const [flowsByMarket, setFlowsByMarket] = useState({ kospi: {}, kosdaq: {}, futures: {} })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [intraday, setIntraday] = useState(null)
  // 선물 1D(장중 누적) 전용 — PLAN.md §5.45. get_flow_series(위 intraday가 쓰는
  // /flow/intraday-accumulated)는 series_key가 애초에 코스피/코스닥 6종뿐이라
  // 선물 항목 자체가 없다(intraday_snapshot.py 모듈 docstring의 "8종 고정값" —
  // 투자자 6개 + futures_외국인 1개 + breadth_ratio 1개 — futures_외국인은 이
  // 엔드포인트가 아니라 ForeignPositionModal이 쓰는 /foreign-position/
  // intraday-accumulated 전용). 선물 필터에서 외국인만이라도 보여주려면 그
  // 엔드포인트의 futures 필드를 재사용해야 해서 별도로 fetch한다 — 개인/기관계는
  // 선물 장중 누적을 수집한 적이 없어 어떤 소스로도 채울 수 없다(정직하게 빈 배열).
  const [futuresIntraday, setFuturesIntraday] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)
  const [intradayDays, setIntradayDays] = useState(1)

  useEffect(() => {
    if (chartMode !== '3M') return undefined
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([fetchMarketSeries('kospi', days), fetchMarketSeries('kosdaq', days), fetchMarketSeries('futures', days)])
      .then(([kospiBody, kosdaqBody, futuresBody]) => {
        if (!cancelled)
          setFlowsByMarket({
            kospi: kospiBody.flows || {},
            kosdaq: kosdaqBody.flows || {},
            futures: futuresBody.flows || {},
          })
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

  // 1D(오늘 장중 누적) — PLAN.md §5.4-3/4. STATIC_DATA(GH Pages 정적 배포)에는
  // 로컬 전용 라이브 폴링이 없어 이 탭 자체를 숨기므로(아래 렌더 분기) 여기서도
  // 요청하지 않는다. 모달이 열려 있는 동안 재폴링은 하지 않는다(스펙: "모달이
  // 열릴 때마다 최신 적립분을 한 번 더 fetch"로 충분, setInterval 불필요).
  useEffect(() => {
    if (STATIC_DATA || chartMode !== '1D') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    // 선물 외국인 장중 누적은 별도 엔드포인트(foreign-position/intraday-accumulated)
    // 소스라 병렬로 같이 받아 둔다(marketFilter를 futures로 바꿀 때 재요청 없이
    // 바로 보이도록, 위 3M 탭이 flowsByMarket을 미리 다 받아두는 것과 같은 패턴).
    Promise.all([fetchFlowIntradayAccumulated(intradayDays), fetchForeignPositionIntradayAccumulated(intradayDays)])
      .then(([body, foreignBody]) => {
        if (!cancelled) {
          setIntraday(body)
          setFuturesIntraday(foreignBody)
        }
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

  // marketFilter에 따라 3M flows를 합계/코스피/코스닥으로 분기(PLAN.md §5.10) —
  // 'all'은 지금까지와 동일한 mergeFlows, 나머지는 fetch해 둔 원본을 그대로 쓴다.
  const flows =
    marketFilter === 'kospi'
      ? flowsByMarket.kospi
      : marketFilter === 'kosdaq'
        ? flowsByMarket.kosdaq
        : marketFilter === 'futures'
          ? flowsByMarket.futures
          : mergeFlows(flowsByMarket.kospi, flowsByMarket.kosdaq)
  const hasFlows = Object.keys(flows || {}).length > 0

  // net_value는 백만원 단위(market_flow와 동일) — FlowChart.jsx eok() 관례와
  // 통일해 억원으로 변환한 뒤 IntradayFlowChart에 넘긴다. 1D 응답이 이제
  // series.kospi/series.kosdaq로 나뉘어 오므로(§5.10) marketFilter에 따라 한쪽만
  // 쓰거나 mergeIntradayByTime으로 시간 키 기준 합산한다. 선물은 개인/기관계
  // 장중 누적을 수집한 적이 없어(위 futuresIntraday 주석 참고) 빈 배열로 두고
  // 외국인만 futuresIntraday.futures에서 채운다(정직하게 부분 데이터).
  const intradaySeries = {}
  for (const name of ['개인', '외국인', '기관계']) {
    if (marketFilter === 'futures') {
      intradaySeries[name] =
        name === '외국인'
          ? (futuresIntraday?.futures || []).map((p) => ({ time: p.time, value: p.value / 100 }))
          : []
      continue
    }
    const kospiPoints = (intraday?.series?.kospi?.[name] || []).map((p) => ({ time: p.time, value: p.value / 100 }))
    const kosdaqPoints = (intraday?.series?.kosdaq?.[name] || []).map((p) => ({
      time: p.time,
      value: p.value / 100,
    }))
    intradaySeries[name] =
      marketFilter === 'kospi'
        ? kospiPoints
        : marketFilter === 'kosdaq'
          ? kosdaqPoints
          : mergeIntradayByTime(kospiPoints, kosdaqPoints)
  }

  const marketFilterLabel =
    marketFilter === 'kospi'
      ? '코스피'
      : marketFilter === 'kosdaq'
        ? '코스닥'
        : marketFilter === 'futures'
          ? '선물(K200)'
          : '코스피+코스닥 합계'

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        {marketFilterLabel}
        {chartMode === '1D' && marketFilter === 'futures' && ' — 1D는 외국인만 제공(개인/기관계 장중 누적 미수집)'}
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
            {chartMode === '1D' ? '오늘 장중 누적(참고용) · 개인/외국인/기관계 60초 틱' : '일별 히스토리'}
          </span>
        </div>
      )}
      <div className="toggle-row">
        {FLOW_MARKET_FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${marketFilter === opt.key ? 'active' : ''}`}
            onClick={() => setMarketFilter(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {chartMode === '3M' && (
        <>
          <PeriodPicker value={days} onChange={setDays} />
          {loading && <div className="state">불러오는 중…</div>}
          {error && <div className="state error">{error}</div>}
          {!loading && !error && hasFlows && <FlowChart flows={flows} />}
          {!loading && !error && !hasFlows && <div className="state">표시할 데이터가 없습니다.</div>}
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
