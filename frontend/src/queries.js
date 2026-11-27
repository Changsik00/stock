import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import {
  STATIC_DATA,
  fetchAttention,
  fetchAutoTradeState,
  fetchBasis,
  fetchBasisLive,
  fetchBreadth,
  fetchBreadthLive,
  fetchDerivativeFlow,
  fetchEtfWeightChanges,
  fetchExpiryCumulative,
  fetchFlowAnomaly,
  fetchFlowConsistency,
  fetchFlowLive,
  fetchFlowPath,
  fetchFlowRank,
  fetchFreshness,
  fetchFuturesFlowLive,
  fetchFxLive,
  fetchGroupTopStocks,
  fetchGroups,
  fetchGroupsLive,
  fetchIndexTilesLive,
  fetchMacroSeries,
  fetchMarketIntraday,
  fetchMarketSeries,
  fetchNqComovement,
  fetchPremarketOutlook,
  fetchRegime,
  fetchScalpCandidates,
  fetchScalpHitrate,
  fetchSectorRotation,
  fetchSentiment,
  fetchShortSellingMarket,
  fetchValueRank,
  fetchValueRankAuto,
  fetchValueRankLive,
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

// refetchInterval: 시장 탭은 마운트 1회(미지정), 대시보드는 60초 폴링(같은 캐시를 공유).
export function useBreadth(refetchInterval) {
  return useQuery({ queryKey: qk.breadth(), queryFn: loadBreadth, refetchInterval })
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

// ---------------------------------------------------------------------------
// 대시보드 탭 데이터 훅(Phase 5.132 2단계). 호출 함수·인자·폴링 주기는 기존 DashboardPage useEffect와 동일하다.
// 주의: queryFn은 항상 화살표로 감싸 호출한다(react-query가 넘기는 context가 api 함수 첫 인자로 새지 않게).
// ---------------------------------------------------------------------------

// 갱신 실패 시 값을 null로 비우던 기존 `.catch(() => setX(null))` 동작을 그대로 재현한다.
// (react-query는 갱신 실패에도 직전 data를 유지하므로, "비우는" 쪽 쿼리만 이 헬퍼로 읽는다.)
export const resetOnError = (q) => (q.isError ? null : (q.data ?? null))

// 갱신 실패 시 직전 값을 유지하던 쪽(`.catch(() => {})`)은 q.data를 그대로 쓴다.

export const dq = {
  indexTilesLive: () => ['dash', 'index-tiles-live'],
  flowLive: () => ['dash', 'flow-live'],
  flowConsistency: () => ['dash', 'flow-consistency'],
  nqComovement: () => ['dash', 'nq-comovement'],
  attention: () => ['dash', 'attention'],
  scalpCandidates: (limit) => ['dash', 'scalp-candidates', limit],
  scalpHitrate: () => ['dash', 'scalp-hitrate'],
  groupsLive: (type) => ['dash', 'groups-live', type],
  basisLive: () => ['dash', 'basis-live'],
  futuresFlowLive: () => ['dash', 'futures-flow-live'],
  fxLive: () => ['dash', 'fx-live'],
  valueRankLive: () => ['dash', 'value-rank-live'],
  valueRankEod: (market, days) => ['dash', 'value-rank-eod', market, days],
  shortSelling: (days) => ['dash', 'short-selling', days],
  sectorRotation: () => ['dash', 'sector-rotation'],
  basis: (days) => ['dash', 'basis', days],
  expiryCumulative: (n) => ['dash', 'expiry-cumulative', n],
  derivativeFlow: (days) => ['dash', 'derivative-flow', days],
  etfWeightChanges: (limit) => ['dash', 'etf-weight-changes', limit],
  autoTradeState: () => ['dash', 'auto-trade-state'],
  freshness: () => ['dash', 'freshness'],
}

const live60 = { refetchInterval: POLL_60S, enabled: !STATIC_DATA }

// 지수 3종 시리즈 — 시장 탭과 같은 키(qk.marketSeries)라 캐시를 공유한다. 시장별 병렬, 하나가 늦어도 나머지는 먼저 뜬다.
export function useIndexSeries(markets, days) {
  return useQueries({
    queries: markets.map((m) => ({
      queryKey: qk.marketSeries(m.key, days),
      queryFn: () => fetchMarketSeries(m.key, days),
    })),
  })
}

export const useIndexTilesLive = () =>
  useQuery({ queryKey: dq.indexTilesLive(), queryFn: () => fetchIndexTilesLive(), ...live60 })
export const useFlowLive = () => useQuery({ queryKey: dq.flowLive(), queryFn: () => fetchFlowLive(), ...live60 })
export const useFlowConsistency = () =>
  useQuery({ queryKey: dq.flowConsistency(), queryFn: () => fetchFlowConsistency(), ...live60 })
export const useNqComovement = () =>
  useQuery({ queryKey: dq.nqComovement(), queryFn: () => fetchNqComovement(), ...live60 })
export const useAttention = () => useQuery({ queryKey: dq.attention(), queryFn: () => fetchAttention(), ...live60 })
export const useScalpCandidates = (limit = 20) =>
  useQuery({ queryKey: dq.scalpCandidates(limit), queryFn: () => fetchScalpCandidates(limit), ...live60 })
export const useGroupsLive = (type) =>
  useQuery({
    queryKey: dq.groupsLive(type),
    queryFn: () => fetchGroupsLive(type),
    placeholderData: keepPreviousData,
    ...live60,
  })
export const useBasisLive = () => useQuery({ queryKey: dq.basisLive(), queryFn: () => fetchBasisLive(), ...live60 })
export const useFuturesFlowLive = () =>
  useQuery({ queryKey: dq.futuresFlowLive(), queryFn: () => fetchFuturesFlowLive(), ...live60 })
export const useFxLive = () => useQuery({ queryKey: dq.fxLive(), queryFn: () => fetchFxLive(), ...live60 })

// 거래대금 상위 라이브 — 7분 티어(백엔드 캐시 420초와 맞춤)
export const useValueRankLive = () =>
  useQuery({
    queryKey: dq.valueRankLive(),
    queryFn: () => fetchValueRankLive(),
    refetchInterval: POLL_VALUE_RANK,
    enabled: !STATIC_DATA,
  })

// 마운트 1회성(폴링 없음) — 하루 1회 배치·확정치 소스
export const useScalpHitrate = () =>
  useQuery({ queryKey: dq.scalpHitrate(), queryFn: () => fetchScalpHitrate(), enabled: !STATIC_DATA })
export const useValueRankEod = (market, days) =>
  useQuery({ queryKey: dq.valueRankEod(market, days), queryFn: () => fetchValueRank(market, days) })
export const useShortSellingBoth = (days) =>
  useQuery({
    queryKey: dq.shortSelling(days),
    queryFn: async () => {
      const [kospi, kosdaq] = await Promise.all([fetchShortSellingMarket('kospi', days), fetchShortSellingMarket('kosdaq', days)])
      return { kospi: kospi.series || [], kosdaq: kosdaq.series || [] }
    },
  })
export const useSectorRotationBoth = () =>
  useQuery({
    queryKey: dq.sectorRotation(),
    queryFn: async () => {
      const [kospi, kosdaq] = await Promise.all([fetchSectorRotation('kospi'), fetchSectorRotation('kosdaq')])
      return { kospi, kosdaq }
    },
  })
export const useBasisSeries = (days) => useQuery({ queryKey: dq.basis(days), queryFn: () => fetchBasis(days) })
export const useExpiryCumulative = (n) =>
  useQuery({ queryKey: dq.expiryCumulative(n), queryFn: () => fetchExpiryCumulative(n), enabled: !STATIC_DATA })
export const useDerivativeFlow = (days) =>
  useQuery({ queryKey: dq.derivativeFlow(days), queryFn: () => fetchDerivativeFlow(days) })
export const useEtfWeightChangesTop = (limit) =>
  useQuery({ queryKey: dq.etfWeightChanges(limit), queryFn: () => fetchEtfWeightChanges({ limit }) })

// 대시보드 상단 보유 타일(30초) — 실거래 보유·손익 표시라 재방문 시 캐시를 먼저 보여주되 15초 지나면 다시 조회한다(staleTime 15초).
// dev mock 적용은 호출부(select)에서. 조회 실패 시 타일을 숨기던 동작은 resetOnError로.
export const useAutoTradeState = () =>
  useQuery({ queryKey: dq.autoTradeState(), queryFn: () => fetchAutoTradeState(), refetchInterval: 30_000, staleTime: 15_000 })
// 데이터 신선도 배너(5분)
export const useFreshness = () =>
  useQuery({ queryKey: dq.freshness(), queryFn: () => fetchFreshness(), refetchInterval: 5 * 60_000, enabled: !STATIC_DATA })
