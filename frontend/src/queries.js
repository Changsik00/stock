import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  STATIC_DATA,
  fetchBreadth,
  fetchBreadthLive,
  fetchFlowAnomaly,
  fetchFlowPath,
  fetchFlowRank,
  fetchGroupTopStocks,
  fetchGroups,
  fetchMacroSeries,
  fetchMarketIntraday,
  fetchMarketSeries,
  fetchPremarketOutlook,
  fetchRegime,
  fetchSentiment,
  fetchValueRankAuto,
  fetchVolRegime,
} from './api'

// 시장 탭 데이터 훅 모음(Phase 5.132). 호출 함수는 api.js 그대로이고, 캐시·재조회 주기만 여기서 정한다.
// 기본값(main.jsx QueryClient): staleTime 60초 / gcTime 30분 / 창 포커스 재조회 끔 / 재시도 1회.
// 필터(시장·기간·투자자 등)가 바뀌는 쿼리는 keepPreviousData로 직전 값을 유지해 표가 비지 않게 한다.

export const POLL_60S = 60_000 // 시장 개요 카드 — 대시보드와 같은 주기
export const POLL_VALUE_RANK = 420_000 // 거래대금 상위 — 라이브 엔드포인트 서버 캐시(7분)와 맞춤

// 쿼리 키 팩토리 — 키가 곧 캐시 단위라 한곳에서만 만든다.
export const qk = {
  premarket: () => ['market', 'premarket'],
  volRegime: () => ['market', 'vol-regime'],
  regime: () => ['market', 'regime'],
  flowAnomaly: () => ['market', 'flow-anomaly'],
  marketSeries: (market, days) => ['market', 'series', market, days],
  intraday: (market, interval) => ['market', 'intraday', market, interval],
  macroFund: (ids, days) => ['market', 'macro-fund', ids, days],
  flowRank: (investor, side, days) => ['market', 'flow-rank', investor, side, days],
  flowPath: (days, limit, direction) => ['market', 'flow-path', days, limit, direction],
  sentiment: () => ['market', 'sentiment'],
  breadth: () => ['market', 'breadth'],
  groups: (type) => ['market', 'groups', type],
  groupTopStocks: (type, name, limit) => ['market', 'group-top-stocks', type, name, limit],
  valueRank: (market, days) => ['market', 'value-rank', market, days],
}

// 에러 메시지 문자열(기존 화면이 문자열 state를 썼다). 데이터가 있으면(백그라운드 갱신 실패) 에러를 숨긴다.
export const errorText = (q) =>
  q.isError && (q.data === undefined || q.isPlaceholderData) ? q.error?.message || String(q.error) : null

// 개요 카드 4종 — 정적 배포에서는 호출하지 않는다. 갱신 실패 시 react-query가 직전 data를 유지한다.
export function usePremarketOutlook() {
  return useQuery({ queryKey: qk.premarket(), queryFn: () => fetchPremarketOutlook(), refetchInterval: POLL_60S, enabled: !STATIC_DATA })
}
export function useVolRegime() {
  return useQuery({ queryKey: qk.volRegime(), queryFn: () => fetchVolRegime(), refetchInterval: POLL_60S, enabled: !STATIC_DATA })
}
export function useRegime() {
  return useQuery({ queryKey: qk.regime(), queryFn: () => fetchRegime(), refetchInterval: POLL_60S, enabled: !STATIC_DATA })
}
export function useFlowAnomaly() {
  return useQuery({ queryKey: qk.flowAnomaly(), queryFn: () => fetchFlowAnomaly(), refetchInterval: POLL_60S, enabled: !STATIC_DATA })
}

export function useMarketSeries(market, days) {
  return useQuery({
    queryKey: qk.marketSeries(market, days),
    queryFn: () => fetchMarketSeries(market, days),
    placeholderData: keepPreviousData,
  })
}

// 분봉 — 일봉 모드·선물 탭·정적 배포에서는 요청하지 않는다(enabled).
export function useMarketIntraday(market, interval) {
  return useQuery({
    queryKey: qk.intraday(market, interval),
    queryFn: () => fetchMarketIntraday(market, interval),
    enabled: !STATIC_DATA && interval !== 'daily' && market !== 'futures',
    placeholderData: keepPreviousData,
  })
}

export function useMacroFundSeries(ids, days) {
  return useQuery({
    queryKey: qk.macroFund(ids, days),
    queryFn: () => fetchMacroSeries(ids, days),
    placeholderData: keepPreviousData,
  })
}

export function useFlowRank(investor, side, days) {
  return useQuery({
    queryKey: qk.flowRank(investor, side, days),
    queryFn: () => fetchFlowRank(investor, side, days),
    placeholderData: keepPreviousData,
  })
}

export function useFlowPath(days, limit, direction) {
  return useQuery({
    queryKey: qk.flowPath(days, limit, direction),
    queryFn: () => fetchFlowPath(days, limit, direction),
    placeholderData: keepPreviousData,
  })
}

export function useSentiment() {
  return useQuery({ queryKey: qk.sentiment(), queryFn: () => fetchSentiment() })
}

// 등락 종목수 — 백엔드 응답은 snake_case라 BreadthBadge가 기대하는 camelCase로 변환한다.
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

// 장중엔 live(60초 서버 캐시) 우선, 실패 시 일별 최신 확정치로 폴백한다(기존 로직 그대로, 한 queryFn).
async function loadBreadth() {
  try {
    const body = await fetchBreadthLive()
    return {
      kospi: toCamel(body.kospi),
      kosdaq: toCamel(body.kosdaq),
      live: body.live !== false,
      // 정적 모드 폴백 행에는 date가 있다(일별 스냅샷) — live 응답에는 없음.
      date: body.kospi?.date || body.kosdaq?.date || null,
    }
  } catch {
    // live 실패(장 마감 후 소스 장애 등) — 일별 최신 확정치로 폴백.
  }
  const [kospiBody, kosdaqBody] = await Promise.all([
    fetchBreadth('kospi', 30).catch(() => null),
    fetchBreadth('kosdaq', 30).catch(() => null),
  ])
  const last = (body) => {
    const series = body?.series
    return series && series.length > 0 ? series[series.length - 1] : null
  }
  const kospiRow = last(kospiBody)
  const kosdaqRow = last(kosdaqBody)
  if (!kospiRow && !kosdaqRow) throw new Error('등락 종목수 데이터를 불러오지 못했습니다.')
  return {
    kospi: toCamel(kospiRow),
    kosdaq: toCamel(kosdaqRow),
    live: false,
    date: kospiRow?.date || kosdaqRow?.date || null,
  }
}

export function useBreadth() {
  return useQuery({ queryKey: qk.breadth(), queryFn: loadBreadth })
}

export function useGroups(type) {
  return useQuery({ queryKey: qk.groups(type), queryFn: () => fetchGroups(type), placeholderData: keepPreviousData })
}

export function useGroupTopStocks(type, name, limit = 10) {
  return useQuery({ queryKey: qk.groupTopStocks(type, name, limit), queryFn: () => fetchGroupTopStocks(type, name, limit) })
}

export function useValueRankQuery(market, days) {
  return useQuery({
    queryKey: qk.valueRank(market, days),
    queryFn: () => fetchValueRankAuto(market, days),
    refetchInterval: POLL_VALUE_RANK,
    placeholderData: keepPreviousData,
  })
}
