import { useState } from 'react'
import { STATIC_DATA } from '../api'
import {
  errorText,
  resetOnError,
  useAttention,
  useBasisLive,
  useBasisSeries,
  useBreadth,
  useDerivativeFlow,
  useEtfWeightChangesTop,
  useExpiryCumulative,
  useFlowConsistency,
  useFlowLive,
  useFlowPath,
  useFlowRank,
  useFuturesFlowLive,
  useFxLive,
  useGroups,
  useGroupsLive,
  useIndexSeries,
  useIndexTilesLive,
  useMacroFundSeries,
  useNqComovement,
  useRegime,
  useScalpCandidates,
  useScalpHitrate,
  useSectorRotationBoth,
  useSentiment,
  useShortSellingBoth,
  useValueRankEod,
  useValueRankLive,
} from '../queries'
import Badge from '../components/Badge'
import { SkeletonBlock } from '../components/Skeleton'
import HoldingTile from '../components/HoldingTile'
import GroupTreemap from '../components/GroupTreemap'
import Modal from '../components/Modal'
import RiskAlertBanner from '../components/RiskAlertBanner'
import FreshnessBanner from '../components/FreshnessBanner'
import InvestorFlowSection from '../components/dashboard/InvestorFlowSection'
import NqComovementCard from '../components/dashboard/NqComovementCard'
import MetricTile, { heroEok } from '../components/dashboard/MetricTile'
import StockDetailModal from '../components/StockDetailModal'
import StockHeatmap from '../components/StockHeatmap'
import StockSearch from '../components/StockSearch'
import {
  DEFAULT_INVESTORS,
  INVESTOR_COLOR_VAR,
  MACRO_SERIES,
  MARKETS,
  MARKET_FUND_IDS,
  US_INDEX_SERIES,
  VIX_SERIES,
} from '../constants'
import { formatDate, signedEokJoLabel } from '../format'
import {
  isNewerDate,
  pickClosedBasis,
  pickClosedForeignSpot,
  pickClosedFuturesFlow,
  pickFxDisplay,
  shouldUseClosedLiveIndexRow,
  shouldUseValueRankLive,
  shouldUseGroupLive,
} from '../liveFallback'
import {
  BREADTH_LIVE_POLL_MS,
  DEFAULT_CANDLE_DAYS,
  DiffArrow,
  FLOW_RANK_LOOKBACK_DAYS,
  KpiTile,
  SectorRotationCard,
  StaleDate,
  Top5Card,
  Top5RowTile,
  VALUE_RANK_MARKET_OPTIONS,
  atRiskBadgeLabel,
  basisLabel,
  computeConcentration,
  countFmt,
  eokLabel,
  filterRowsByMarket,
  flowBadgeLabel,
  fxFmt,
  fxLabel,
  joFmt,
  latestFlowRow,
  latestOf,
  mergeFlows,
  mmdd,
  numFmt,
  oilFmt,
  oilLabel,
  pctSuffix,
  rateClass,
  rateLabel,
  scalpHitrateFootnote,
  scoreClass,
  scoreFmt,
  scoreLabel,
  staleHintLabel,
  trillion,
  trillionLabel,
  usIndexFmt,
  usIndexLabel,
  volumeSurgeLabel,
} from '../components/dashboard/shared'
import CandleModal from '../components/dashboard/CandleModal'
import SentimentModal from '../components/dashboard/SentimentModal'
import BreadthModal from '../components/dashboard/BreadthModal'
import ConcentrationModal from '../components/dashboard/ConcentrationModal'
import ShortSellingModal from '../components/dashboard/ShortSellingModal'
import FundModal from '../components/dashboard/FundModal'
import MacroModal from '../components/dashboard/MacroModal'
import FlowSummaryModal from '../components/dashboard/FlowSummaryModal'
import ForeignPositionModal from '../components/dashboard/ForeignPositionModal'
import DerivativeEtfModal from '../components/dashboard/DerivativeEtfModal'
import ExpiryPatternModal from '../components/dashboard/ExpiryPatternModal'
import HynixPositioningModal from '../components/dashboard/HynixPositioningModal'
import MacroBriefingModal from '../components/dashboard/MacroBriefingModal'
import FlowRankFullModal from '../components/dashboard/FlowRankFullModal'
import ValueRankFullModal from '../components/dashboard/ValueRankFullModal'
import FlowPathFullModal from '../components/dashboard/FlowPathFullModal'
import EtfWeightChangesFullModal from '../components/dashboard/EtfWeightChangesFullModal'
import AttentionFullModal from '../components/dashboard/AttentionFullModal'
import ScalpCandidatesFullModal from '../components/dashboard/ScalpCandidatesFullModal'
import GroupTopStocksModal from '../components/dashboard/GroupTopStocksModal'

// 대시보드 탭 (PLAN.md §6 3.7-1, 사용자 원문: "장황하게 정보가 노출됨. 디테일은 뒤로
// 숨기고 핵심 숫자만. 차트는 모달/탭으로. 100개짜리 리스트도 뒤로.") — 기본 탭.
// 시장 탭(MarketPage)의 상세 컨트롤(투자자·기간·시장 토글이 있는 표/차트 전부)을
// 그대로 옮기지 않고, 여기서는 "핵심 숫자만" 골라 타일로 보여준 뒤 클릭 시에만 그
// 상세(차트/전체 리스트)를 모달로 연다. 데이터는 전부 기존 api.js 함수를 그대로
// 재사용한다 — 이 작업에서 백엔드/api.js는 건드리지 않는다(병렬 작업 중인 백엔드
// 검색/종목 API와 충돌 방지, PLAN.md §6 3.7-1 작업 지시).

// 환율/WTI 타일의 최신값·전일비 계산용 — 최근 2거래일만 있으면 되므로 짧게 잡는다
// (fundSeries가 fundLatest/fundPrev용으로 10일을 쓰는 관례와 동일).
const MACRO_TILE_DAYS = 10

// 외인 양손(현물·선물·베이시스) 섹션 — PLAN.md §4.5-5. 타일 최신값 계산용으로는
// 짧은 창(10일)이면 충분하고(마지막 값 + 전일 비교), 상세 모달(ForeignPositionModal)은
// 시장 탭과 동일하게 90일 기본으로 시작한다.
const FOREIGN_POSITION_TILE_DAYS = 10

// 프로그램매매 차익 순매수(macro_series prog_arb_*, PLAN.md §4.5-4) — 코스피+코스닥
// 합산해서 "프로그램 차익 순매수" 타일 하나로 보여준다(신용융자 타일의 creditLoanSum과
// 동일한 관례).
const PROGRAM_ARB_IDS = ['prog_arb_kospi', 'prog_arb_kosdaq']

// 프로그램매매 비차익 순매수(macro_series prog_nonarb_*, PLAN.md §5.29-1) — 이미
// program_flow.py(ka90010)가 매일 수집해 DB에 쌓아두고 있었는데 프런트에만 노출이
// 안 돼 있던 것을 뒤늦게 붙인다. 외부 강의(파생상품 전문가, 최창규 이사) 검토 결과
// "비차익거래(대량 바스켓 매매)가 차익거래보다 펀드 리밸런싱·ETF 설정/환매 등 큰손
// 움직임을 더 잘 보여주는 지표"라는 조언에 따라 차익 타일과 나란히 노출한다.
const PROGRAM_NONARB_IDS = ['prog_nonarb_kospi', 'prog_nonarb_kosdaq']
// 차익 2개 + 비차익 2개를 한 번의 macro/series 호출로 받는다(쿼리 키에 쓰이므로 모듈 상수로 고정)
const PROGRAM_IDS = [...PROGRAM_ARB_IDS, ...PROGRAM_NONARB_IDS]
// 환율·WTI·미국 4대 지수·VIX 타일용 시리즈 id
const MACRO_TILE_IDS = [...MACRO_SERIES.map((s) => s.id), ...US_INDEX_SERIES.map((s) => s.id), VIX_SERIES.id]

// 만기 임박 시그널 배지 기준(D-n 이내, PLAN.md §4.5-5 "만기 D-3 이내").
const EXPIRY_SOON_D_DAY = 3

// 수급 상위 카드 토글(PLAN.md 5.117) — API investor/side 값과 같다.
const FLOW_RANK_INVESTOR_OPTIONS = [
  { key: 'foreign', label: '외국인' },
  { key: 'institution', label: '기관' },
]
const FLOW_RANK_SIDE_OPTIONS = [
  { key: 'buy', label: '순매수' },
  { key: 'sell', label: '순매도' },
]

const GROUP_TYPE_OPTIONS = [
  { key: 'upjong', label: '업종' },
  { key: 'theme', label: '테마' },
]

// 업종 자금 흐름(§5.33) 카드는 코스피/코스닥이 서로 다른 ka10051 코드 체계라
// "전체" 합산 옵션이 없다(VALUE_RANK_MARKET_OPTIONS와 달리 2개 토글만).
const SECTOR_ROTATION_MARKET_OPTIONS = [
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]

// 7분 티어 — 이제 거래대금 상위(value-rank) 하나만 남았다. 백엔드 5~10분 캐시
// (routers/flow_rank.py의 LIVE_TTL_SECONDS=420초)와 맞춘다(PLAN.md §4.7 3단 갱신
// 주기, 2026-07-20 장중 실측으로 편입). value-rank만 7분을 유지하는 이유(§5.5-2
// 진단②): 코스피+코스닥 전 종목(~4,300종목) 페이지네이션이 필요해 사이클당
// ~44요청·13초+가 걸리는 진짜 비싼 호출이라, 유가(yfinance) 429 차단 전례와 같은
// 리스크 카테고리로 보수적으로 유지한다. 업종/테마·베이시스·외인선물수급은 목록·
// 단일 조회 1회뿐이라 비용이 없어 위 1분 티어로 옮겼다. 수급 상위(flow-rank)는
// 실측 결과 소스가 2영업일 이상 지연돼 있어 애초에 라이브로 편입하지 않았다 —
// EOD(FLOW_RANK_LOOKBACK_DAYS 기준) 그대로 유지.
// (주기 상수는 queries.js POLL_VALUE_RANK = 420_000, useValueRankLive에서 쓴다.)

// VIX 수준 단어(Phase 5.112, 참고용): <15 평온 / 15–25 보통 / 25–30 높음 / ≥30 공포
function vixLevelWord(v) {
  if (v < 15) return '평온'
  if (v < 25) return '보통'
  if (v < 30) return '높음'
  return '공포'
}

export default function DashboardPage({ onNavigate }) {
  // 화면 토글·필터 state(데이터가 아니라 선택값) — 데이터 조회는 아래 useQuery 훅이 맡는다(Phase 5.132 2단계).
  // 쿼리 키에 이 값이 들어가므로 바꾸면 해당 쿼리만 새로 조회하고, 한 번 본 값은 캐시에서 즉시 나온다.
  const [groupType, setGroupType] = useState('upjong')
  // 업종 자금 흐름 관찰(PLAN.md §5.33) — 코스피/코스닥 선택(하루 1회 배치 산출물, 폴링 없음)
  const [sectorRotationMarket, setSectorRotationMarket] = useState('kospi')
  // 수급 상위 카드 선택(PLAN.md 5.117) — 기본 외국인·순매수
  const [flowRankInvestor, setFlowRankInvestor] = useState('foreign')
  const [flowRankSide, setFlowRankSide] = useState('buy')
  // 종목 랭킹 요약 3개 카드(거래대금 상위/실시간 관심 TOP5/스켈핑 후보)의 시장 필터(PLAN.md §5.15-3)
  // — 'all'|'kospi'|'kosdaq', 기본 전체. 수급 상위/ETF 경유 상위는 시장 구분이 뚜렷하지 않은 소스라 필터 대상에서 뺐다.
  const [rankingMarketFilter, setRankingMarketFilter] = useState('all')
  const [modal, setModal] = useState(null) // { type, title, ...params } | null

  // ── 지수 3종: 타일 표시용 + 캔들 모달 기본 데이터를 겸한다(시장 탭과 같은 캐시 키). 시장별 병렬이라
  // 하나가 늦어도 나머지 타일은 먼저 뜨고, 실패한 시장은 빈 시리즈로 둔다(기존 .catch(() => null) 동작).
  const indexQueries = useIndexSeries(MARKETS, DEFAULT_CANDLE_DAYS)
  const marketData = {}
  MARKETS.forEach((m, i) => {
    const q = indexQueries[i]
    if (q.data) marketData[m.key] = q.data
    else if (q.isError) marketData[m.key] = { prices: [], flows: {} }
  })
  const marketLoading = indexQueries.some((q) => q.isLoading)

  // 지수 3종 타일 1D 실시간(index-tiles/live, 60초) — 실패 시 null이면 렌더가 marketData(3M EOD) 마지막 봉으로 폴백한다.
  const indexTilesQ = useIndexTilesLive()
  const indexTilesLive = resetOnError(indexTilesQ)

  const sentimentQ = useSentiment()
  const sentiment = sentimentQ.data ?? null
  // 등락 종목수 — 60초 폴링(live 우선, 실패 시 일별 확정치 폴백은 queryFn 안에서)
  const breadthQ = useBreadth(BREADTH_LIVE_POLL_MS)
  const breadth = breadthQ.data ?? null

  // 장중 잠정 수급(PLAN.md §6 3.7-3) — null이면 "라이브 없음"이라 타일은 기존 확정치로 폴백한다.
  const flowLive = resetOnError(useFlowLive())
  // 장중 외인 매도 일관성(Phase 5.113)·코스피 등락 분해(5.116) — 실패 시 마지막 값 유지
  const flowConsistencyQ = useFlowConsistency()
  const flowConsistency = flowConsistencyQ.data ?? null
  const nqComovementQ = useNqComovement()
  const nqComovement = nqComovementQ.data ?? null


  const fundQ = useMacroFundSeries(MARKET_FUND_IDS, 10)
  const fundSeries = fundQ.data?.series || {}
  // 시장 전체 공매도(PLAN.md §5.32) — 거래대금 비중 최신값 + 전일비용 며칠치
  const shortQ = useShortSellingBoth(10)
  const shortSellingSeries = shortQ.data || {}
  // 환율·WTI·미국 지수·VIX 타일 계산용(모달은 자기 기간을 따로 조회)
  const macroQ = useMacroFundSeries(MACRO_TILE_IDS, MACRO_TILE_DAYS)
  const macroSeries = macroQ.data?.series || {}

  // 외인 양손 · 현선물(PLAN.md §4.5-5) — 베이시스/만기, 만기 사이클 누적(5.100), 파생ETF 방향성, 프로그램 차익/비차익
  const basisQ = useBasisSeries(FOREIGN_POSITION_TILE_DAYS)
  const basisData = basisQ.data ?? null
  const expiryQ = useExpiryCumulative(6)
  const expiryCum = expiryQ.data ?? null
  const derivativeQ = useDerivativeFlow(FOREIGN_POSITION_TILE_DAYS)
  const derivativeFlow = derivativeQ.data ?? null
  const programQ = useMacroFundSeries(PROGRAM_IDS, FOREIGN_POSITION_TILE_DAYS)
  const programFlow = programQ.data?.series || {}
  // 타일 격자가 로딩 중에도 로드 후 높이를 차지하도록(CSS .kpi-grid-sk-*) 마운트 시 한 번만 조회되는 소스들의 로딩 여부
  const foreignGridLoading = marketLoading || basisQ.isLoading || derivativeQ.isLoading || programQ.isLoading || expiryQ.isLoading

  // 베이시스·외인 선물수급·환율 1분 라이브 오버레이(§4.7, §5.5-2/3) — 실패 시 null로 두면 EOD 값으로 폴백한다.
  const basisLive = resetOnError(useBasisLive())
  const futuresFlowLive = resetOnError(useFuturesFlowLive())
  const fxLive = resetOnError(useFxLive())

  // 업종/테마: EOD(트리맵 박스 크기) + 1분 라이브 등락률(색). 유형을 바꾸면 이전 유형 데이터를 잠시 들고 있으므로
  // 그 동안은 로딩으로 취급해 다른 유형의 박스가 새 유형 제목 아래 뜨지 않게 한다(기존 동작과 동일).
  const groupsQ = useGroups(groupType)
  const groupItems = groupsQ.data || []
  const groupLoading = groupsQ.isLoading || groupsQ.isPlaceholderData
  const groupError = errorText(groupsQ)
  const groupLive = resetOnError(useGroupsLive(groupType))

  const sectorQ = useSectorRotationBoth()
  const sectorRotation = sectorQ.data || {}

  // 수급 상위(EOD) — 토글 직후 이전 선택의 행이 새 라벨 아래 보이지 않도록 placeholder 동안은 비운다(기존: 토글 때 null 초기화).
  const flowRankQ = useFlowRank(flowRankInvestor, flowRankSide, FLOW_RANK_LOOKBACK_DAYS)
  const flowRankTop = flowRankQ.isPlaceholderData ? null : flowRankQ.data?.dates?.[0] || null
  const flowRankLoading = flowRankQ.isLoading || flowRankQ.isPlaceholderData
  const valueRankQ = useValueRankEod('all', FLOW_RANK_LOOKBACK_DAYS)
  const valueRankTop = valueRankQ.data ?? null
  // 거래대금 상위 7분 라이브(§4.7) — 응답 스키마가 valueRankTop과 같아 그대로 대체해 쓴다
  const valueRankLiveQ = useValueRankLive()
  const valueRankLive = resetOnError(valueRankLiveQ)
  const flowPathQ = useFlowPath(FLOW_RANK_LOOKBACK_DAYS, 5, 'in')
  const flowPathTop = flowPathQ.data ?? null
  // ETF 비중 변화 상위(§5.25, 하루 1회 배치)
  const etfWeightQ = useEtfWeightChangesTop(5)
  const etfWeightChangesTop = etfWeightQ.data ?? null
  // 실시간 관심 TOP20(live-only)·스켈핑 후보(§5.2)·적중률(§5.40, 폴링 없음) — 실패 시 null
  const attentionQ = useAttention()
  const attentionTop = resetOnError(attentionQ)
  const scalpQ = useScalpCandidates(20)
  const scalpCandidates = resetOnError(scalpQ)
  const scalpHitrate = useScalpHitrate().data ?? null
  const marketGridLoading = fundQ.isLoading || macroQ.isLoading || sentimentQ.isLoading || breadthQ.isLoading || shortQ.isLoading
  // 장 상태(정규장/NXT/마감)는 index-tiles/live·attention 응답으로 판정한다 — 둘 다 도착하기 전엔 배너를 정하지 않고 자리만 잡는다.
  const statusLoading = indexTilesQ.isLoading || attentionQ.isLoading
  // "지금 유입 우세" 판정(§5.15) — 시장 탭과 같은 캐시. 여기서는 수급 집중도 기준치(activity_baseline)에만 쓴다.
  const regime = resetOnError(useRegime())

  const closeModal = () => setModal(null)

  // 종목 상세 모달을 여는 공용 헬퍼 — 실시간 관심 TOP5(기존)와 수급/거래대금/ETF경유
  // 랭킹 3종(이번 작업으로 클릭 가능해짐)이 전부 이 함수 하나로 모인다(사용자 요구:
  // "모든 랭킹 행 클릭 → 종목 상세 모달"). 열려 있던 리스트 모달을 별도로 닫을
  // 필요는 없다 — setModal은 단일 상태라 새 타입으로 덮어쓰면 그 자체로 "닫고
  // 교체"가 된다(Modal.jsx가 modal?.type에 따라 자식을 조건부로만 렌더).
  const openStockModal = (code, name, extra = {}) =>
    setModal({
      type: 'stock',
      title: `${name || code} · 종목 상세`,
      code,
      stock: { code, name, ...extra },
    })

  // 지수 타일 + baseDate 계산이 공유하는 헬퍼 — marketData[key].prices의 마지막 값
  // (3M EOD, 캔들 모달 기본 데이터와 동일한 소스).
  const latestPriceOf = (key) => {
    const data = marketData[key]
    return data?.prices?.length ? data.prices[data.prices.length - 1] : null
  }

  // 지수 타일 표시값(PLAN.md 작업 지시, 2026-07-21) — indexTilesLive가 있고 장중이며
  // 해당 시장 값이 있으면 그 라이브 값(1D 기준, 60초 갱신)을 쓰고, 아니면(정적
  // 배포·라이브 실패·장 마감) latestPriceOf(3M EOD 마지막 봉)로 자동 폴백한다.
  // 반환 모양을 latestPriceOf와 맞춰(close/changeRate/date) 렌더 쪽 분기를 최소화
  // 하고, isLive만 추가로 얹어 "장중" 배지 여부를 결정한다. 모달(캔들차트)은 이
  // 헬퍼를 쓰지 않고 marketData를 그대로 쓴다(모달·차트는 기존 로직 유지 — 작업 지시).
  const indexTilesLiveOpen = indexTilesLive?.market_closed === false
  // 장 상태 배너(PLAN.md §5.9, 2026-07-22 사용자 지적) — 정규장 09:00 개장 전(또는
  // 15:30 마감 후)엔 지수·수급 등 "오늘" 계산이 정규장 기준이라 어제 확정치로 보이는
  // 게 정상인데, 화면이 그걸 알려주지 않아 "고장났나?" 헷갈림이 반복됐다. 새 API·새
  // 시간 로직 없이 이미 폴링 중인 두 신호만으로 3단계를 판정한다:
  //   1) 정규장 중: indexTilesLive.market_closed === false — 배너 없음(가장 흔한
  //      상태라 화면을 어지럽히지 않는다).
  //   2) NXT만 열림(프리마켓 08:00~09:00 / 애프터마켓 15:30~20:00): 지수 쪽은
  //      닫혔는데 attentionTop이 살아있음(rows가 오는, market_closed가 true가
  //      아닌) 경우.
  //   3) 완전 마감(야간·주말): 둘 다 닫힘.
  const nxtAlive = Boolean(attentionTop && attentionTop.market_closed !== true)
  const marketStatus = indexTilesLiveOpen ? 'regular' : nxtAlive ? 'nxt-only' : 'closed'
  // 마감 후~배치 전: 라이브 row date가 EOD 마지막 봉보다 최신이면 마감 후에도 라이브 값을
  // 쓴다(isLive는 false — 장중 배지 없음). 실제로 쓰인 row만 baseDate/수급 판정에 반영.
  const closedLiveIndexRowOf = (key) => {
    if (indexTilesLiveOpen || STATIC_DATA) return null
    const row = indexTilesLive?.[key]
    return row && shouldUseClosedLiveIndexRow(row, latestPriceOf(key)) ? row : null
  }
  const indexTileOf = (key) => {
    const openRow = indexTilesLiveOpen ? indexTilesLive?.[key] : null
    const row = openRow || closedLiveIndexRowOf(key)
    if (row) {
      return {
        close: row.close,
        changeRate: row.change_rate,
        date: row.date,
        time: row.time,
        isLive: Boolean(openRow),
        // §5.24: kospi/kosdaq만 갖는 필드(futures는 이 라우터가 아예 채우지
        // 않음, _index_tile_futures_live는 별도 경로) — 없으면 undefined로
        // 남아 volumeSurgeLabel이 null을 반환하고 아래 렌더가 줄을 생략한다.
        volumeSurge: row.volume_surge,
      }
    }
    const fallback = latestPriceOf(key)
    return fallback ? { ...fallback, isLive: false } : null
  }
  // 마감 후 라이브 지수가 실제로 쓰였는가(=오늘이 거래일이고 EOD가 뒤처진 상태의 증거)
  const closedLiveIndexUsed = ['kospi', 'kosdaq'].some((k) => closedLiveIndexRowOf(k))

  // 등락 종목수 압축 칩 — 코스피+코스닥 합계 (BreadthBadge.splitTotals와 동일 규칙:
  // 상승 = adv+limitUp, 하락 = dec+limitDown).
  const breadthTotals = (() => {
    if (!breadth) return null
    const rows = [breadth.kospi, breadth.kosdaq].filter(Boolean)
    if (rows.length === 0) return null
    return rows.reduce(
      (acc, r) => ({
        up: acc.up + (r.adv ?? 0) + (r.limitUp ?? 0),
        down: acc.down + (r.dec ?? 0) + (r.limitDown ?? 0),
      }),
      { up: 0, down: 0 }
    )
  })()

  // "코스피/코스닥 쏠림" KPI 타일(PLAN.md §5.18, 시총 편향 보정은 §5.19) — 이미
  // 1분 티어에서 폴링 중인 flowLive + regime을 computeConcentration으로 즉석
  // 계산한다(breadthTotals와 동일한 관례, 새 API 호출 없음). regime이 아직 없거나
  // activity_baseline이 없으면(로딩 중 등) computeConcentration이 null을 돌려준다.
  const concentrationBaseline = {
    kospi: regime?.kospi?.activity_baseline?.avg_daily_activity ?? null,
    kosdaq: regime?.kosdaq?.activity_baseline?.avg_daily_activity ?? null,
  }
  const concentrationLive = computeConcentration(flowLive, concentrationBaseline)

  // 시장 전체 공매도(코스피/코스닥, PLAN.md §5.32) 타일용 — 거래대금 비중(value_ratio, %)
  // 최신값/기준일. fundLatest/fundDate와 동일한 관례이되, 소스가
  // /api/markets/{market}/short-selling(전용 테이블)이라 값 필드명이 다르다. 두 시장을
  // 한 타일에 나란히 보여주는 형태라(등락종목수/코스피코스닥쏠림 타일과 동일한 관례)
  // 전일비 화살표는 붙이지 않는다(두 시장 diff를 한 타일에 억지로 합치지 않음).
  const shortSellingLatest = (market) => {
    const points = shortSellingSeries[market] || []
    return points.length > 0 ? points[points.length - 1].value_ratio : null
  }
  const shortSellingDate = (market) => {
    const points = shortSellingSeries[market] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }

  const fundLatest = (id) => {
    const points = fundSeries[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const fundPrev = (id) => {
    const points = fundSeries[id] || []
    return points.length > 1 ? points[points.length - 2].value : null
  }
  const macroLatest = (id) => {
    const points = macroSeries[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const macroPrev = (id) => {
    const points = macroSeries[id] || []
    return points.length > 1 ? points[points.length - 2].value : null
  }
  const macroDate = (id) => {
    const points = macroSeries[id] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }
  // §5.6-1: fundSeries(투자자예탁금/신용융자/대차잔고)도 macroSeries와 동일한
  // /api/macro/series 응답이라 각 포인트에 date가 이미 있다 — macroDate와 동일한
  // 관례로 최신 포인트의 date만 뽑는다(KPI 타일 StaleDate 배지용).
  const fundDate = (id) => {
    const points = fundSeries[id] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }
  // 신용융자 타일 — 코스피+코스닥 두 시리즈를 합산해 단일 숫자로 보여준다(MARKET_FUND_IDS
  // 관례상 별도 "합계" id가 없다). 둘 다 없으면 null(대시 표시), 하나라도 있으면 0으로
  // 보정해 더한다.
  function creditLoanSum(pick) {
    const kospi = pick('credit_loan_kospi')
    const kosdaq = pick('credit_loan_kosdaq')
    if (kospi === null && kosdaq === null) return null
    return trillion((kospi ?? 0) + (kosdaq ?? 0))
  }
  const creditLoanLatest = creditLoanSum(fundLatest)
  const creditLoanPrev = creditLoanSum(fundPrev)
  // 코스피/코스닥 두 시리즈가 서로 다른 날짜에 멈춰 있을 수 있어(KOFIA 소스별 지연이
  // 다를 수 있음) latestOf로 더 최근 쪽을 대표일로 쓴다 — macroDate('usdkrw') 등과
  // 동일한 "여러 후보 중 최신" 관례.
  const creditLoanDate = latestOf(fundDate('credit_loan_kospi'), fundDate('credit_loan_kosdaq'))

  const etfComponent = sentiment?.components?.etf

  // 장중 잠정 수급 — 코스피+코스닥 investors를 투자자별로 합산한다(기존 확정치
  // flowInvestorSummary와 같은 모양: {투자자명: net_value}). 장 마감(market_closed)
  // 이후이거나 두 시장 다 provisional이 아니면(=백엔드가 이미 DB 확정치로 폴백한
  // 경우) "장중 잠정" 타일을 켤 이유가 없으므로 flowLiveActive를 false로 둔다 —
  // PLAN.md §6 3.7-3 지시: "장중엔 live 값 + 배지, 실패·마감 후엔 기존 확정치 + 라벨".
  const flowLiveActive = Boolean(
    flowLive && flowLive.market_closed === false && (flowLive.kospi?.provisional || flowLive.kosdaq?.provisional)
  )
  const flowLiveSummary = (() => {
    if (!flowLiveActive) return null
    const out = {}
    for (const name of DEFAULT_INVESTORS) {
      const kospiVal = flowLive.kospi?.investors?.[name]?.net_value
      const kosdaqVal = flowLive.kosdaq?.investors?.[name]?.net_value
      if (kospiVal === undefined && kosdaqVal === undefined) continue
      out[name] = (kospiVal ?? 0) + (kosdaqVal ?? 0)
    }
    return out
  })()

  const flowInvestorSummary = (() => {
    const kospi = marketData.kospi
    const kosdaq = marketData.kosdaq
    if (!kospi || !kosdaq) return null
    const merged = mergeFlows(kospi.flows, kosdaq.flows)
    const out = {}
    for (const name of DEFAULT_INVESTORS) {
      const rows = merged[name] || []
      out[name] = rows.length > 0 ? rows[rows.length - 1] : null
    }
    return out
  })()

  // 마감 후~배치 전 수급: flowLive가 market_closed:true로 오늘 합산치를 주지만 휴장일에도
  // "오늘 날짜"가 오는 퀴크가 있어 flowLive.date만으로는 판정 불가 — 마감 후 라이브 지수가
  // 실제로 쓰인 날(closedLiveIndexUsed, 거래일 증거)에만, 그리고 flow 라이브 date가 EOD
  // 수급 date보다 최신일 때만 쓴다. 장중 배지는 없고 "확정" 라벨 + 값만 라이브.
  const flowClosedLiveSummary = (() => {
    if (STATIC_DATA || flowLiveActive || !closedLiveIndexUsed || flowLive?.market_closed !== true) return null
    const liveDate = flowLive.kospi?.date
    if (!liveDate || flowLive.kosdaq?.date !== liveDate) return null
    if (!isNewerDate(liveDate, latestOf(...DEFAULT_INVESTORS.map((n) => flowInvestorSummary?.[n]?.date)))) return null
    const out = {}
    for (const name of DEFAULT_INVESTORS) {
      const k = flowLive.kospi?.investors?.[name]?.net_value
      const q = flowLive.kosdaq?.investors?.[name]?.net_value
      if (k === undefined && q === undefined) continue
      out[name] = (k ?? 0) + (q ?? 0)
    }
    return { values: out, date: liveDate }
  })()

  // 투자자별 수급 타일(PLAN.md Phase 5.124): 합계(기존 값 그대로) + 코스피/코스닥 시장별 값.
  // 시장별 값은 합계와 같은 원천·같은 기준일에서 뽑아 합계 = 코스피 + 코스닥이 되게 한다.
  const flowLiveTime = (() => {
    const t = flowLive?.cached_at ? new Date(flowLive.cached_at) : null
    if (!t || Number.isNaN(t.getTime())) return null
    return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false }).format(t)
  })()
  // Phase 5.127 — 외국인·기관계 타일의 선물(코스피200) 순매수와 기관계의 금융투자 몫. 현물과 같은 날짜일 때만 붙인다
  // (다른 날 값끼리 같은/반대 방향을 비교하면 틀리므로).
  const futuresFor = (name, source, date) => {
    if (!STATIC_DATA && source === 'live') {
      return futuresFlowLive && futuresFlowLive.market_closed === false
        ? (futuresFlowLive.investors?.[name]?.net_value ?? null)
        : null
    }
    if (!STATIC_DATA && futuresFlowLive?.date && date && futuresFlowLive.date === date) {
      return futuresFlowLive.investors?.[name]?.net_value ?? null
    }
    const row = latestFlowRow(marketData.futures?.flows, name)
    return row && date && row.date === date ? row.net_value : null
  }
  const sumMarkets = (a, b) => (typeof a === 'number' || typeof b === 'number' ? (a ?? 0) + (b ?? 0) : null)
  const liveFin = () =>
    sumMarkets(flowLive?.kospi?.investors?.['금융투자']?.net_value, flowLive?.kosdaq?.investors?.['금융투자']?.net_value)
  const investorFlowTiles = DEFAULT_INVESTORS.map((name) => {
    const liveValue = flowLiveSummary?.[name]
    if (liveValue !== undefined) {
      return {
        name,
        source: 'live',
        total: liveValue,
        kospi: flowLive.kospi?.investors?.[name]?.net_value,
        kosdaq: flowLive.kosdaq?.investors?.[name]?.net_value,
        futures: futuresFor(name, 'live'),
        fin: liveFin(),
      }
    }
    const closedValue = flowClosedLiveSummary?.values?.[name]
    if (closedValue !== undefined) {
      return {
        name,
        source: 'closed',
        date: flowClosedLiveSummary.date,
        total: closedValue,
        kospi: flowLive.kospi?.investors?.[name]?.net_value,
        kosdaq: flowLive.kosdaq?.investors?.[name]?.net_value,
        futures: futuresFor(name, 'closed', flowClosedLiveSummary.date),
        fin: liveFin(),
      }
    }
    const row = flowInvestorSummary?.[name]
    const onDate = (flows, who = name) => flows?.[who]?.find((r) => r.date === row?.date)?.net_value
    return {
      name,
      source: 'eod',
      date: row?.date,
      total: row?.net_value,
      kospi: row ? onDate(marketData.kospi?.flows) : undefined,
      kosdaq: row ? onDate(marketData.kosdaq?.flows) : undefined,
      futures: row ? futuresFor(name, 'eod', row.date) : null,
      fin: row ? sumMarkets(onDate(marketData.kospi?.flows, '금융투자'), onDate(marketData.kosdaq?.flows, '금융투자')) : null,
    }
  })

  // 외인 양손 · 현선물 섹션(PLAN.md §4.5-5) — 현물은 위에서 이미 계산한
  // flowInvestorSummary/flowLiveSummary의 '외국인' 값을 그대로 재사용하고(중복 fetch
  // 없음), 선물은 marketData.futures(지수 3종 fetch가 이미 flows까지 받아온다)의
  // '외국인' 마지막 행을 쓴다.
  // 마감 후~배치 전에는 위 수급 섹션과 같이 flowClosedLiveSummary(오늘 값)를 쓴다("확정 · 날짜", 배지 없음).
  const foreignSpotLiveValue = flowLiveSummary?.['외국인']
  const foreignSpotIsLive = foreignSpotLiveValue !== undefined
  const foreignSpotClosed = foreignSpotIsLive ? null : pickClosedForeignSpot(flowClosedLiveSummary)
  const foreignSpotRow = foreignSpotClosed
    ? { net_value: foreignSpotClosed.value, date: foreignSpotClosed.date }
    : flowInvestorSummary?.['외국인']
  const foreignSpotValue = foreignSpotIsLive ? foreignSpotLiveValue : foreignSpotRow?.net_value
  const foreignFuturesRow = latestFlowRow(marketData.futures?.flows, '외국인')

  // 외인 선물(K200) 1분 라이브 오버레이(PLAN.md §4.7, §5.5-2로 7분→1분 이동) —
  // 장중이고(market_closed===false) 값이 있을 때만 "표시"를 라이브로 바꾼다
  // (시그널 판정 foreignFuturesSign은 EOD 기준 그대로, 아래 futuresFlowLiveNetValue는
  // KPI 타일 표시 전용).
  const futuresFlowLiveNetValue =
    !STATIC_DATA && futuresFlowLive && futuresFlowLive.market_closed === false
      ? (futuresFlowLive.investors?.['외국인']?.net_value ?? null)
      : null
  // 마감 후~배치 전: 거래일 증거(closedLiveIndexUsed)가 있고 라이브 date가 EOD 선물수급
  // date보다 최신일 때만 라이브 값을 표시한다(장중 배지 없음, "확정 · 날짜" 표기는 수급 타일 관례).
  // baseDate에는 섞지 않는다. 시그널(foreignFuturesSign)은 EOD 기준 그대로.
  const futuresClosedLive =
    STATIC_DATA || futuresFlowLiveNetValue !== null
      ? null
      : pickClosedFuturesFlow(futuresFlowLive, { closedLiveIndexUsed, eodDate: foreignFuturesRow?.date })
  const futuresShownValue = futuresFlowLiveNetValue ?? futuresClosedLive?.value ?? null

  const basisLatest = basisData?.latest
  // 베이시스 1분 라이브 오버레이(PLAN.md §4.7, §5.5-2로 7분→1분 이동) — KPI 타일
  // 표시 전용(시그널 판정 backwardationSignal은 EOD 기준 basisLatest 그대로).
  // 마감 후~배치 전에는 거래일 증거(closedLiveIndexUsed)+라이브 date가 EOD보다 최신일 때만 라이브
  // (장중 표기 없이 날짜 표기). baseDate에는 섞지 않는다.
  const basisLivePick = STATIC_DATA
    ? null
    : pickClosedBasis(basisLive, { closedLiveIndexUsed, eodDate: basisLatest?.date })
  const basisLiveActive = Boolean(basisLivePick?.isOpen)
  // 타일 표시용 베이시스(라이브 우선, 없으면 EOD)
  const basisShown = basisLivePick ?? basisLatest
  // 환율(USD/KRW) 1분 라이브 오버레이(PLAN.md §5.5-3) — basisLiveActive와 동일한
  // 관례. 값 표시만 라이브로 바꾸고 "전일 대비"라는 의미는 유지한 채 "현재"만 갱신한다.
  // 마감 후에도 라이브 date가 EOD 마지막보다 최신이면 라이브를 쓰고(장중 배지 없음), 전일비 기준은
  // 라이브 date가 더 최신일 때 EOD 마지막 값(직전 거래일 종가), 같으면 macroPrev다(장중·마감 후 공통).
  // 기존엔 항상 macroPrev(끝에서 두 번째)를 써서 EOD 마지막이 어제인 시간대엔 "그제 대비"였다.
  // baseDate에는 라이브 date를 섞지 않는다.
  const fxPick = STATIC_DATA
    ? { active: false }
    : pickFxDisplay(fxLive, {
        eodDate: macroDate('usdkrw'),
        eodLatest: macroLatest('usdkrw'),
        eodPrev: macroPrev('usdkrw'),
      })
  const fxLiveActive = fxPick.active
  const fxCurrent = fxLiveActive ? fxPick.value : macroLatest('usdkrw')
  const fxPrevValue = fxLiveActive ? fxPick.prev : macroPrev('usdkrw')
  const expiry = basisData?.expiry
  const derivativeLatest = derivativeFlow?.latest
  const derivativeUniverse = derivativeFlow?.universe

  // 프로그램 차익 순매수 — 코스피+코스닥 합산(신용융자 타일 creditLoanSum과 동일한 관례).
  const programLatest = (id) => {
    const points = programFlow[id] || []
    return points.length > 0 ? points[points.length - 1].value : null
  }
  const programDate = (id) => {
    const points = programFlow[id] || []
    return points.length > 0 ? points[points.length - 1].date : null
  }
  const programArbKospi = programLatest('prog_arb_kospi')
  const programArbKosdaq = programLatest('prog_arb_kosdaq')
  const programArbLatest =
    programArbKospi === null && programArbKosdaq === null ? null : (programArbKospi ?? 0) + (programArbKosdaq ?? 0)
  const programArbDate = latestOf(programDate('prog_arb_kospi'), programDate('prog_arb_kosdaq'))

  // 프로그램 비차익 순매수(PLAN.md §5.29-1) — 차익 타일과 동일한 관례(코스피+코스닥
  // 합산). programFlow 상태는 위 fetch에서 차익/비차익 4개 series를 이미 함께 받아온다.
  const programNonarbKospi = programLatest('prog_nonarb_kospi')
  const programNonarbKosdaq = programLatest('prog_nonarb_kosdaq')
  const programNonarbLatest =
    programNonarbKospi === null && programNonarbKosdaq === null
      ? null
      : (programNonarbKospi ?? 0) + (programNonarbKosdaq ?? 0)
  const programNonarbDate = latestOf(programDate('prog_nonarb_kospi'), programDate('prog_nonarb_kosdaq'))

  // 시그널 배지(PLAN.md §4.5-5, 중립 표현 — "함정" 단정 금지) — ① 외인 현물·선물 방향
  // 대치, ② 만기 D-3 이내. 값이 없거나(0 포함) 한쪽이 없으면 판단하지 않는다(오검
  // 방지 — Math.sign(0)===0이라 자연히 걸러진다).
  // 2026-07-23(§5.15 후속): 백워데이션 배지는 제거했다 — 3년치 실측 결과 다음날
  // 하락확률이 콘탱고와 거의 차이 없어(43.8% vs 43.0%, PLAN.md §5.15) 예측력이
  // 없다는 게 실증됐다. 베이시스 값 자체(콘탱고/역전 텍스트)는 위 KpiTile에 그대로
  // 남아 있다 — 이 배지 행에서만 뺀다.
  // 시그널(현·선 방향 배지)용 부호는 기존 기준 유지: 장중이면 라이브, 아니면 EOD. 마감 후 라이브
  // 표시값(foreignSpotClosed)은 타일 표시 전용이라 여기 섞지 않는다(선물 부호가 EOD 기준이라
  // 섞으면 오늘 현물 vs 어제 선물 비교가 된다).
  const foreignSpotSignValue = foreignSpotIsLive ? foreignSpotLiveValue : flowInvestorSummary?.['외국인']?.net_value
  const foreignSpotSign =
    foreignSpotSignValue === null || foreignSpotSignValue === undefined ? 0 : Math.sign(foreignSpotSignValue)
  const foreignFuturesSign =
    foreignFuturesRow?.net_value === null || foreignFuturesRow?.net_value === undefined
      ? 0
      : Math.sign(foreignFuturesRow.net_value)
  const directionMismatch = foreignSpotSign !== 0 && foreignFuturesSign !== 0 && foreignSpotSign !== foreignFuturesSign
  const expirySoonSignal = typeof expiry?.d_day === 'number' && expiry.d_day >= 0 && expiry.d_day <= EXPIRY_SOON_D_DAY

  const foreignSignals = [
    directionMismatch && { key: 'direction', kind: 'warn', label: '현·선 방향 상이' },
    expirySoonSignal && { key: 'expiry', kind: 'warn', label: '만기 임박' },
  ].filter(Boolean)

  // 대표 기준일(대시보드 상단 1회 표시) — 소스별로 수집 시차가 있어(지수/수급/ETF 등)
  // 항상 같은 날짜라는 보장이 없으므로, 각 타일 데이터 날짜들 중 최신값 하나만 뽑는다.
  // 사용자 피드백: "타일마다 날짜가 있는데 중복이다. 어차피 마지막 거래일일 텐데" —
  // 실제로는 다를 수 있어 예외 표시(StaleDate/staleHintLabel)로 보완한다.
  const investorDates = DEFAULT_INVESTORS.map((name) => flowInvestorSummary?.[name]?.date).filter(Boolean)
  // flowLive.date는 market_closed===false(장중)일 때만 대표 기준일 계산에 넣는다.
  // 휴장일(주말 등)에도 /api/markets/flow/live가 market_closed:true 상태로 오늘 날짜를
  // 되돌려주는 경우가 있어, 그 날짜를 그대로 latestOf에 섞으면 baseDate가 실제
  // 마지막 거래일보다 앞으로(오늘로) 부풀어 다른 모든 타일이 "뒤처진 것처럼"
  // StaleDate 배지가 붙는 왜곡이 생긴다 — 장이 닫힌 날은 확정치 날짜들만으로
  // 기준일을 정한다(수급 타일의 "장중 잠정" 배지 게이트인 flowLiveActive와 동일하게
  // market_closed로 판단).
  const flowLiveOpen = flowLive?.market_closed === false
  // attentionTop.queried_at은 의도적으로 baseDate 계산에 넣지 않는다 — flowLive.date와
  // 달리 "거래일 날짜"가 아니라 조회 시각을 담은 전체 ISO 타임스탬프(예:
  // "2026-07-18T17:40:47.354047+00:00")라 formatDate가 'YYYY-MM-DD'로 정규화하지
  // 못한다(8자리 숫자가 아님). 억지로 넣어도 latestOf의 length===10 필터에 걸러져
  // 아무 효과가 없고, 의미상으로도 "당일 거래 기준일"과 "지금 이 순간"은 다른
  // 개념이라 섞으면 오히려 baseDate 해석이 혼란스러워진다.
  const baseDate = latestOf(
    ...MARKETS.map((m) => latestPriceOf(m.key)?.date),
    // indexTilesLive.date도 flowLive와 동일한 이유로 장중(market_closed===false)일
    // 때만 섞는다 — 장 마감 후 market_closed:true로 오늘 날짜가 오면 baseDate가
    // 실제 마지막 거래일보다 앞으로 부풀어 다른 타일에 잘못된 StaleDate가 붙는다.
    ...MARKETS.map((m) => (indexTilesLiveOpen ? indexTilesLive?.[m.key]?.date : closedLiveIndexRowOf(m.key)?.date)),
    etfComponent?.date,
    ...investorDates,
    flowLiveOpen ? flowLive?.kospi?.date : null,
    flowLiveOpen ? flowLive?.kosdaq?.date : null,
    valueRankTop?.date,
    flowPathTop?.date,
    flowRankTop?.date,
    macroDate('usdkrw'),
    macroDate('wti'),
    foreignFuturesRow?.date,
    basisLatest?.date,
    derivativeLatest?.date,
    programArbDate,
    programNonarbDate
  )

  // 업종/테마 트리맵 — groupItems(EOD, value/market_sum 포함)에 groupLive(1분 라이브,
  // §5.5-2로 7분→1분 이동, change_rate만)를 이름 기준으로 병합한다(PLAN.md §4.7).
  // 박스 크기(value)는 EOD 그대로 유지하고 색(change_rate)만 장중에 갱신되는 셈 —
  // GroupTreemap.jsx는 이 병합 여부를 모르는 순수 컴포넌트라 그대로 재사용한다.
  // groupLive가 없거나(정적 배포/폴링 전/실패) 해당 그룹 타입과 다르면 groupItems를
  // 그대로 쓴다.
  // 마감 후엔 거래일 증거(closedLiveIndexUsed)+rows 비어있지 않음일 때만 라이브(장중 배지 없음).
  const groupLiveActive = !STATIC_DATA && shouldUseGroupLive(groupLive, groupType, closedLiveIndexUsed)
  const groupLiveOpen = groupLiveActive && groupLive.market_closed === false
  const groupTreemapItems = groupLiveActive
    ? groupItems.map((item) => {
        const live = groupLive.rows.find((r) => r.name === item.name)
        return live ? { ...item, change_rate: live.change_rate } : item
      })
    : groupItems

  // 거래대금 상위 TOP5 카드 — valueRankLive(7분 라이브)가 있고 장중이면 그걸 그대로
  // 쓰고(응답 스키마가 valueRankTop과 동일), 아니면 valueRankTop(EOD 1회성 fetch)으로
  // 폴백한다(PLAN.md §4.7).
  const valueRankLiveActive = Boolean(!STATIC_DATA && valueRankLive && valueRankLive.market_closed === false)
  // 마감 후에도 EOD 스냅샷이 라이브보다 낡았으면(배치 지연/실패) 라이브를 쓴다(Phase 5.119).
  const effectiveValueRank =
    !STATIC_DATA && shouldUseValueRankLive(valueRankLive, valueRankTop?.date) ? valueRankLive : valueRankTop
  // 거래대금 상위 데이터가 아직 하나도 없고(EOD·라이브 모두 조회 중) 기다리는 동안 — 히트맵·카드가 자리를 잡는다.
  const heatmapLoading = !effectiveValueRank && (valueRankQ.isLoading || valueRankLiveQ.isLoading)

  return (
    <div className="dashboard-page m-font">
      {/* Phase 5.121 — 보유 중일 때만 보이는 한 줄 타일(매매 탭으로 이동) */}
      {!STATIC_DATA && <HoldingTile onNavigate={onNavigate} />}
      <div className="dashboard-header-row">
        {/* 종목 검색 (PLAN.md §6 3.7-2) — 온디맨드 API(/api/stocks/search)라 정적
            스냅샷 대상이 아니다. 정적 배포(STATIC_DATA=1)에서는 검색을 서빙할 수 없으므로
            같은 자리에 비활성 입력만 남겨 레이아웃이 흔들리지 않게 한다. */}
        {STATIC_DATA ? (
          <input
            type="text"
            className="dashboard-search"
            placeholder="종목 검색 — 준비 중"
            disabled
            title="정적 배포에선 종목 검색 미지원"
            aria-label="종목 검색 (정적 배포 미지원)"
          />
        ) : (
          <StockSearch onSelect={(stock) => openStockModal(stock.code, stock.name, stock)} />
        )}
        {/* 대표 기준일 — 타일별 개별 날짜 대신 여기 한 번만 노출한다(위 baseDate 계산 참고). */}
        {baseDate && (
          <span
            className="dashboard-base-date"
            title={`대표 기준일 — 각 타일 데이터 날짜 중 최신값 (${baseDate})`}
          >
            기준일 {formatDate(baseDate)}
          </span>
        )}
      </div>

      {/* 시장 위험 경보 배너(PLAN.md §5.36, 2026-07-28) — 서킷브레이커/사이드카
          발동 기준 도달·거래량 급증을 알린다. index-tiles/live의 `risk` 필드가
          이미 판정을 끝내 놓은 값을 그대로 넘기기만 한다(새 fetch 없음, 아래
          indexTilesLive는 기존 1분 티어가 채운다). 장 마감이면 백엔드가 `risk`
          자체를 null로 주므로(더 이상 "지금 당장의 위험"이 아님) 이 배너는
          자동으로 숨는다. 아래 NXT/장마감 안내(.banner)보다 먼저 둬서 "지금
          진행 중인 위험"이 항상 가장 눈에 먼저 띄게 한다(실제로는 risk가 장중
          에만 채워지므로 두 배너가 동시에 뜰 일은 거의 없다). */}
      {!STATIC_DATA && <RiskAlertBanner risk={indexTilesLive?.risk} />}

      {/* 장 상태 배너(PLAN.md §5.9) — 정규장 중엔 렌더하지 않는다(가장 흔한 상태라
          배너로 화면을 어지럽히지 않는다). 정적 배포(STATIC_DATA)는 라이브 폴링이
          없어 판정 근거가 없으므로 아예 숨긴다(다른 라이브 전용 UI와 동일한 관례). */}
      {!STATIC_DATA && statusLoading && (
        <div className="banner sk-banner" aria-hidden="true">
          장 마감 — 모든 지표가 최근 확정치입니다.
        </div>
      )}
      {!STATIC_DATA && !statusLoading && marketStatus === 'nxt-only' && (
        <div className="banner">
          NXT 확장세션 — 실시간 관심 TOP5 등 개별 종목만 갱신 중이며, 지수·수급·업종·테마
          등은 정규장(09:00~15:30) 확정치입니다.
        </div>
      )}
      {!STATIC_DATA && !statusLoading && marketStatus === 'closed' && (
        <div className="banner">장 마감 — 모든 지표가 최근 확정치입니다.</div>
      )}

      {/* 데이터 신선도 경고(PLAN.md Phase 5.120 C2) — 문제가 있을 때만 주황 한 줄 */}
      <FreshnessBanner />

      {/* '지금 유입 우세'(PLAN.md §5.15)는 Phase 5.128에서 '시장' 탭 상단(MarketOverviewCards)으로 옮겼다. regime 데이터는
          수급 집중도 기준치(activity_baseline)에 계속 쓰므로 조회는 여기 남긴다. */}

      {/* 프리마켓 예상 시작점·오늘 변동성 레짐은 Phase 5.128에서 '시장' 탭 상단(MarketOverviewCards)으로 옮겼다. */}

      {/* 1. 지수 3종 */}
      <div className="section-title mt-title">지수</div>
      <div className="mt-row mt-row-index">
        {MARKETS.map((m) => {
          const latest = indexTileOf(m.key)
          const liveTime =
            latest?.isLive && latest.time && latest.time.length === 4
              ? `${latest.time.slice(0, 2)}:${latest.time.slice(2)}`
              : null
          const volumeSurgeText = volumeSurgeLabel(latest?.volumeSurge)
          return (
            <MetricTile
              key={m.key}
              label={m.label}
              loading={!latest && marketLoading}
              value={latest ? numFmt.format(latest.close) : null}
              dir={latest ? rateClass(latest.changeRate) : undefined}
              delta={latest ? (latest.changeRate == null ? '-' : `${Math.abs(latest.changeRate).toFixed(2)}%`) : undefined}
              sub={
                latest && (volumeSurgeText || latest.isLive || latest.date) ? (
                  <>
                    {latest.isLive ? (
                      <Badge kind="live">장중</Badge>
                    ) : (
                      <StaleDate date={latest.date} baseDate={baseDate} />
                    )}
                    {/* §5.24 시장 전체 거래량 급증 감지 — 데이터 부족이면 null이라 렌더하지 않는다
                        (placeholder/0 금지). 관찰 서술만 표시(§5 원칙). */}
                    {volumeSurgeText && <span className="mt-sub-note">{volumeSurgeText}</span>}
                  </>
                ) : null
              }
              title={
                latest?.isLive
                  ? `${formatDate(latest.date)}${liveTime ? ` ${liveTime}` : ''} · 60초 갱신`
                  : latest?.date
                    ? formatDate(latest.date)
                    : undefined
              }
              onClick={() => setModal({ type: 'candle', market: m.key, title: `${m.label} · 캔들차트` })}
            />
          )
        })}
      </div>

      {/* 2. 투자자별 수급 (현물) + 외인 장중 흐름 해석(PLAN.md Phase 5.124) — 값은 타일(합계 +
          코스피/코스닥), 해석은 접는 영역. 장중엔 라이브 잠정치(섹션 제목에 "장중 잠정 · 시각" 1회),
          그 외(장마감 후·라이브 실패·정적 배포)에는 기존 확정치 + 타일별 "확정 · 날짜"(flowLiveActive 참고).
          선물 값은 클릭해 여는 FlowSummaryModal의 "선물" 필터에서 확인한다(PLAN.md §5.45-3). */}
      <InvestorFlowSection
        tiles={investorFlowTiles}
        liveActive={flowLiveActive}
        liveTime={flowLiveTime}
        consistency={STATIC_DATA ? null : flowConsistency}
        consistencyEnabled={!STATIC_DATA}
        loading={marketLoading}
        baseDate={baseDate}
        onTileClick={() => setModal({ type: 'flowSummary', title: '투자자별 수급 요약' })}
      />

      {/* 2.3 코스피–나스닥 선물 동조도(PLAN.md Phase 5.116) — 참고용, 정적 배포에서는 숨김 */}
      {!STATIC_DATA && <NqComovementCard data={nqComovement} loading={nqComovement === null && nqComovementQ.isLoading} />}

      {/* 2.5 외인 양손 · 현선물 (PLAN.md §4.5-5) — 중립적 상태 계기판, "함정" 단정 표현
          금지(§4.5 배경). 시그널 배지는 있을 때만 렌더되고, 클릭하면 전부 같은 상세
          모달(ForeignPositionModal — 현물/선물 시계열 + 베이시스 오버레이)을 연다. */}
      <div className="section-title">외인 양손 · 현선물</div>
      {foreignSignals.length > 0 && (
        <div className="signal-row">
          {foreignSignals.map((s) => (
            <button
              key={s.key}
              type="button"
              className={`badge badge-${s.kind}`}
              onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
              title="외인 현물·선물 순매수와 베이시스 시계열 — 참고 지표(중립 계기판)"
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className={`kpi-grid${foreignGridLoading ? ' kpi-grid-sk-fx' : ''}`}>
        <KpiTile
          label="외인 현물"
          value={
            foreignSpotValue !== undefined && foreignSpotValue !== null
              ? eokLabel(foreignSpotValue)
              : marketLoading
                ? '…'
                : '-'
          }
          valueClass={
            foreignSpotValue !== undefined && foreignSpotValue !== null ? (foreignSpotValue >= 0 ? 'up' : 'down') : ''
          }
          sub={
            foreignSpotIsLive ? (
              <span className="kpi-tile-sub">
                <Badge kind="live" />
              </span>
            ) : (
              foreignSpotRow?.date && (
                <span className="kpi-tile-sub">
                  확정
                  <StaleDate date={foreignSpotRow.date} baseDate={baseDate} prefix=" · " />
                </span>
              )
            )
          }
          title={foreignSpotClosed ? `코스피+코스닥 합계 · ${formatDate(foreignSpotClosed.date)}` : '코스피+코스닥 합계'}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="외인 선물 (K200)"
          value={
            futuresShownValue !== null
              ? eokLabel(futuresShownValue)
              : foreignFuturesRow
                ? eokLabel(foreignFuturesRow.net_value)
                : marketLoading
                  ? '…'
                  : '-'
          }
          valueClass={
            futuresShownValue !== null
              ? futuresShownValue >= 0
                ? 'up'
                : 'down'
              : foreignFuturesRow
                ? foreignFuturesRow.net_value >= 0
                  ? 'up'
                  : 'down'
                : ''
          }
          sub={
            futuresFlowLiveNetValue !== null ? (
              <span className="kpi-tile-sub">1분 갱신 · 장중</span>
            ) : futuresClosedLive ? (
              <span className="kpi-tile-sub">
                확정
                <StaleDate date={futuresClosedLive.date} baseDate={baseDate} prefix=" · " />
              </span>
            ) : (
              foreignFuturesRow?.date && (
                <span className="kpi-tile-sub">
                  확정
                  <StaleDate date={foreignFuturesRow.date} baseDate={baseDate} prefix=" · " />
                </span>
              )
            )
          }
          title={
            futuresClosedLive
              ? formatDate(futuresClosedLive.date)
              : foreignFuturesRow?.date
                ? formatDate(foreignFuturesRow.date)
                : undefined
          }
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="개인 방향성(파생ETF)"
          value={derivativeLatest ? eokLabel(derivativeLatest.net_bet) : '…'}
          valueClass={derivativeLatest ? (derivativeLatest.net_bet >= 0 ? 'up' : 'down') : ''}
          sub={
            <span className="kpi-tile-sub">
              레버리지 {derivativeUniverse?.leverage ?? 0} · 인버스 {derivativeUniverse?.inverse ?? 0}종목
              <StaleDate date={derivativeLatest?.date} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title={derivativeLatest?.date ? formatDate(derivativeLatest.date) : undefined}
          onClick={() => setModal({ type: 'derivativeEtf', title: '개인 방향성(파생ETF) · 순베팅' })}
        />
        <KpiTile
          label="베이시스"
          value={basisLabel(basisShown?.basis)}
          valueClass={basisShown?.basis === undefined || basisShown?.basis === null ? '' : basisShown.basis >= 0 ? 'up' : 'down'}
          sub={
            <span className="kpi-tile-sub">
              {basisShown?.backwardation === undefined || basisShown?.backwardation === null ? (
                '-'
              ) : basisShown.backwardation ? (
                // 2026-07-23 수정(§4.5-5 후속) — 어제(2026-07-22)는 "차익 매도 유의"
                // 문구를 순화해 배지 자체는 남겨뒀는데, 오늘 index_ohlcv/market_flow
                // 3년치 실측 결과 백워데이션 다음날 하락확률(43.8%)이 콘탱고(43.0%)와
                // 거의 차이가 없어(PLAN.md §5.15) 예측력이 없다는 게 실증됐다 —
                // 배지(강조 표시) 자체를 없애고 콘탱고와 동일하게 중립적인 짧은
                // 텍스트만 남긴다. 베이시스 수치(pt)는 그대로 위에 표시된다.
                '역전(선물<현물)'
              ) : (
                '콘탱고'
              )}
              {basisLiveActive ? ' · 1분 갱신' : <StaleDate date={basisShown?.date} baseDate={baseDate} prefix=" · " />}
            </span>
          }
          title={basisShown?.date ? formatDate(basisShown.date) : undefined}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="프로그램 차익 순매수"
          value={programArbLatest !== null ? eokLabel(programArbLatest) : '…'}
          valueClass={programArbLatest === null ? '' : programArbLatest >= 0 ? 'up' : 'down'}
          sub={
            <span className="kpi-tile-sub">
              코스피+코스닥
              <StaleDate date={programArbDate} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="프로그램 비차익 순매수"
          value={programNonarbLatest !== null ? eokLabel(programNonarbLatest) : '…'}
          valueClass={programNonarbLatest === null ? '' : programNonarbLatest >= 0 ? 'up' : 'down'}
          sub={
            <span className="kpi-tile-sub">
              코스피+코스닥
              <StaleDate date={programNonarbDate} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="다음 만기"
          value={expiry?.date ? `${mmdd(expiry.date)} · D-${expiry.d_day}` : '…'}
          sub={
            <>
              {expiry?.quadruple && <Badge kind="warn">네 마녀의 날</Badge>}
              {expiryCum?.current && (
                <span
                  className="kpi-tile-sub"
                  style={{ display: 'block' }}
                  title="직전 만기 익일부터 오늘까지 순매수 누적 — 현물은 코스피 시장 합계, 선물은 K200(옵션 아님)"
                >
                  {typeof expiryCum.current.spot_totals?.foreign === 'number' && (
                    <>
                      사이클 현물 외인{' '}
                      <span className={expiryCum.current.spot_totals.foreign >= 0 ? 'up' : 'down'}>
                        {expiryCum.current.spot_totals.foreign >= 0 ? '▲' : '▼'}{' '}
                        {signedEokJoLabel(expiryCum.current.spot_totals.foreign)}
                      </span>
                      {expiryCum.current.absorption && (
                        <> · 기관·개인 {Math.round(expiryCum.current.absorption.pct)}% 흡수</>
                      )}
                      <br />
                    </>
                  )}
                  선물 외인{' '}
                  <span className={expiryCum.current.totals.foreign >= 0 ? 'up' : 'down'}>
                    {expiryCum.current.totals.foreign >= 0 ? '▲' : '▼'}{' '}
                    {signedEokJoLabel(expiryCum.current.totals.foreign)}
                  </span>
                  {' · 기관 '}
                  <span className={expiryCum.current.totals.institution >= 0 ? 'up' : 'down'}>
                    {expiryCum.current.totals.institution >= 0 ? '▲' : '▼'}{' '}
                    {signedEokJoLabel(expiryCum.current.totals.institution)}
                  </span>
                </span>
              )}
            </>
          }
          title={expiry?.date ? formatDate(expiry.date) : undefined}
          onClick={() =>
            setModal(
              STATIC_DATA
                ? { type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' }
                : { type: 'expiryPattern', title: '만기 수렴 패턴 (D-day별 베이시스)' }
            )
          }
        />
        {/* "오늘의 포지셔닝 재료" 브리핑 진입점(PLAN.md §5.50-6/5.50-7) — 이 타일
            자체는 상시 실시간 값을 보여주는 카드가 아니라 모달을 여는 진입점일
            뿐이라(위 다른 타일들과 달리 마운트 시 값을 미리 fetch하지 않는다),
            클릭해서 HynixPositioningModal이 열릴 때 5개 API를 그때 가져온다.
            로컬 전용 기능이라 정적 배포(STATIC_DATA)에서는 기존 foreignPosition
            모달로 대신 연결한다(다음 만기 타일과 동일한 STATIC_DATA 분기 관례). */}
        <KpiTile
          label="오늘의 포지셔닝 재료"
          value="자세히 보기"
          sub={<span className="kpi-tile-sub">하이닉스 중심 · 관찰치 모음</span>}
          onClick={() =>
            setModal(
              STATIC_DATA
                ? { type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' }
                : { type: 'hynixPositioning', title: '오늘의 포지셔닝 재료 (하이닉스 중심)' }
            )
          }
        />
        {/* "오늘의 매크로 브리핑" 진입점 — 사용자 요청("매크로 상황을 따로 보기
            좋게 보여주는 게 있냐")에 따라 위 포지셔닝 재료 타일과 동일한 패턴으로
            추가. 클릭 시에만 MacroBriefingModal이 데이터를 가져온다(신규
            백엔드 없음, 전부 기존 엔드포인트 재조합). 로컬 전용 기능이라
            STATIC_DATA에서는 기존 macro 모달(차트만)로 대신 연결한다(다른
            브리핑 진입점 타일과 동일한 STATIC_DATA 분기 관례). */}
        <KpiTile
          label="오늘의 매크로 브리핑"
          value="자세히 보기"
          sub={<span className="kpi-tile-sub">환율·유가·미국증시·수급쏠림 모음</span>}
          onClick={() =>
            setModal(
              STATIC_DATA
                ? { type: 'macro', title: '환율 · 유가 · 전일 미국장' }
                : { type: 'macroBriefing', title: '오늘의 매크로 브리핑' }
            )
          }
        />
      </div>

      {/* 3. 시황 · 자금 */}
      <div className="section-title">시황 · 자금</div>
      <div className={`kpi-grid${marketGridLoading ? ' kpi-grid-sk-mkt' : ''}`}>
        <KpiTile
          label="매수세 게이지"
          value={scoreLabel(sentiment?.score)}
          valueClass={scoreClass(sentiment?.score)}
          sub={<span className="kpi-tile-sub">-100 매도세 · +100 매수세 (근사)</span>}
          onClick={() => setModal({ type: 'sentiment', title: '시장 매수세/매도세 게이지' })}
        />
        <KpiTile
          label="등락 종목수"
          value={
            breadthTotals ? (
              <>
                <span className="up">{countFmt.format(breadthTotals.up)}↑</span>{' '}
                <span className="down">{countFmt.format(breadthTotals.down)}↓</span>
              </>
            ) : (
              '…'
            )
          }
          sub={<span className="kpi-tile-sub">코스피+코스닥 합계</span>}
          onClick={() => setModal({ type: 'breadth', title: '등락 종목수' })}
        />
        {/* "코스피/코스닥 쏠림"(PLAN.md §5.18) — "외인/기관 돈이 어디로 쏠리는지"
            관찰 카드. 값은 코스피 쪽 활동 비중(%), 50%면 균등 분산. */}
        <KpiTile
          label="코스피/코스닥 쏠림"
          value={concentrationLive ? `코스피 ${scoreFmt.format(concentrationLive.kospiShare)}%` : '…'}
          sub={<span className="kpi-tile-sub">외인+기관계 활동량 비교 · 코스닥 나머지</span>}
          onClick={() => setModal({ type: 'concentration', title: '코스피/코스닥 쏠림' })}
        />
        {/* §5.6-1: 예탁금/대차잔고/신용융자는 KOFIA T+1(영업일) 공시라 구조적으로
            라이브 불가(§4.7-4, §7에 기록됨, 바꾸지 않음) — 그래서 실제 데이터 날짜가
            오늘과 다를 때가 대부분이다. ETF순유입/WTI 타일과 동일한 StaleDate("MM-DD"
            회색 배지) 패턴을 그대로 재사용해 "이 값이 언제 기준인지"를 명시한다. */}
        <KpiTile
          label="투자자예탁금"
          value={trillionLabel(fundLatest('investor_deposit'))}
          sub={
            <>
              <DiffArrow
                current={trillion(fundLatest('investor_deposit'))}
                prev={trillion(fundPrev('investor_deposit'))}
                formatter={(v) => `${joFmt.format(v)}조${pctSuffix(v, trillion(fundPrev('investor_deposit')))}`}
              />
              <StaleDate date={fundDate('investor_deposit')} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={fundDate('investor_deposit') ? formatDate(fundDate('investor_deposit')) : undefined}
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        <KpiTile
          label="대차잔고"
          value={trillionLabel(fundLatest('lending_balance'))}
          sub={
            <>
              <DiffArrow
                current={trillion(fundLatest('lending_balance'))}
                prev={trillion(fundPrev('lending_balance'))}
                formatter={(v) => `${joFmt.format(v)}조${pctSuffix(v, trillion(fundPrev('lending_balance')))}`}
              />
              <StaleDate date={fundDate('lending_balance')} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={fundDate('lending_balance') ? formatDate(fundDate('lending_balance')) : undefined}
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        {/* 공매도(KRX 정보데이터시스템, PLAN.md §5.32) — 위 "대차잔고"(KOFIA, 빌린
            주식 잔고, 시장 전체 합계)와는 소스와 의미가 다른 별개 지표라 같은 타일에
            합치지 않고 분리된 타일로 둔다("정직한 표시" 원칙, ETF 비중 변화 섹션과
            동일한 취지). 값은 공매도 거래대금 비중(%, 소스 원값 그대로 — 재계산하지
            않음)이며, 비중이 높다/낮다에 대한 해석은 붙이지 않는다. */}
        <KpiTile
          label="공매도 비중"
          value={
            shortSellingLatest('kospi') !== null || shortSellingLatest('kosdaq') !== null ? (
              <>
                코스피{' '}
                {shortSellingLatest('kospi') !== null ? `${scoreFmt.format(shortSellingLatest('kospi'))}%` : '-'} ·
                코스닥{' '}
                {shortSellingLatest('kosdaq') !== null ? `${scoreFmt.format(shortSellingLatest('kosdaq'))}%` : '-'}
              </>
            ) : (
              '…'
            )
          }
          sub={
            <span className="kpi-tile-sub">
              거래대금 비중 · 대차잔고와 별개 지표
              <StaleDate date={shortSellingDate('kospi')} baseDate={baseDate} prefix=" · " />
            </span>
          }
          title={shortSellingDate('kospi') ? formatDate(shortSellingDate('kospi')) : undefined}
          onClick={() => setModal({ type: 'shortSelling', title: '공매도 비중 · 코스피/코스닥' })}
        />
        <KpiTile
          label="신용융자"
          value={creditLoanLatest !== null ? `${joFmt.format(creditLoanLatest)}조` : '-'}
          sub={
            <>
              <DiffArrow
                current={creditLoanLatest}
                prev={creditLoanPrev}
                formatter={(v) => `${joFmt.format(v)}조${pctSuffix(v, creditLoanPrev)}`}
              />
              <StaleDate date={creditLoanDate} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={creditLoanDate ? `코스피+코스닥 합계 · ${formatDate(creditLoanDate)}` : '코스피+코스닥 합계'}
          onClick={() => setModal({ type: 'fund', title: '시장 자금 · 대차' })}
        />
        <KpiTile
          label="ETF 순유입 합계"
          value={etfComponent ? eokLabel(etfComponent.net_inflow_sum) : '…'}
          valueClass={etfComponent && etfComponent.net_inflow_sum < 0 ? 'down' : 'up'}
          sub={<StaleDate date={etfComponent?.date} baseDate={baseDate} />}
          title={etfComponent?.date ? formatDate(etfComponent.date) : undefined}
          onClick={() => setModal({ type: 'sentiment', title: '시장 매수세/매도세 게이지' })}
        />
        {/* 환율/WTI — 매크로 탭 통합(탭 제거, 타일+모달로 편입). 환율은 상승이 "좋은
            것"이 아니라 주가 등락 색 관례와 혼동될 수 있어 DiffArrow를 neutral로 켜서
            화살표 색을 중립(회색)으로 둔다 — 값·화살표 방향·포맷 자체는 다른 타일과
            동일한 관례(예탁금 타일의 DiffArrow)를 그대로 쓴다. WTI는 색상 구분 유지.
            브렌트는 타일에서 생략하고 모달(MacroModal)에서만 보여준다. */}
        <KpiTile
          label="환율(USD/KRW)"
          value={fxLabel(fxCurrent)}
          sub={
            <>
              <DiffArrow
                current={fxCurrent}
                prev={fxPrevValue}
                formatter={(v) => `${fxFmt.format(v)}원${pctSuffix(v, fxPrevValue)}`}
                neutral
              />
              {fxLiveActive && fxPick.isOpen ? (
                <span className="kpi-tile-sub"> · 1분 갱신 · 장중</span>
              ) : (
                <StaleDate date={fxLiveActive ? fxPick.date : macroDate('usdkrw')} baseDate={baseDate} prefix=" · " />
              )}
            </>
          }
          title={
            (fxLiveActive ? fxPick.date : macroDate('usdkrw'))
              ? formatDate(fxLiveActive ? fxPick.date : macroDate('usdkrw'))
              : undefined
          }
          onClick={() => setModal({ type: 'macro', title: '환율 · 유가 · 전일 미국장' })}
        />
        <KpiTile
          label="WTI"
          value={oilLabel(macroLatest('wti'))}
          sub={
            <>
              <DiffArrow
                current={macroLatest('wti')}
                prev={macroPrev('wti')}
                formatter={(v) => `$${oilFmt.format(v)}${pctSuffix(v, macroPrev('wti'))}`}
              />
              <StaleDate date={macroDate('wti')} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={macroDate('wti') ? formatDate(macroDate('wti')) : undefined}
          onClick={() => setModal({ type: 'macro', title: '환율 · 유가 · 전일 미국장' })}
        />
        {/* 전일 미국장 4대 지수(PLAN.md §5.8, 2026-07-22 사용자 제안) — 미국장 EOD라
            WTI와 동일하게 라이브 갱신 없이 하루 1회 배치 값 + StaleDate 배지만 보여준다.
            등락 색은 다른 지수 타일과 동일한 관례(neutral 없이 up=상승/down=하락). */}
        {US_INDEX_SERIES.map((s) => (
          <KpiTile
            key={s.id}
            label={s.label}
            value={usIndexLabel(macroLatest(s.id))}
            sub={
              <>
                <DiffArrow
                  current={macroLatest(s.id)}
                  prev={macroPrev(s.id)}
                  formatter={(v) => `${usIndexFmt.format(v)}${pctSuffix(v, macroPrev(s.id))}`}
                />
                <StaleDate date={macroDate(s.id)} baseDate={baseDate} prefix=" · " />
              </>
            }
            title={macroDate(s.id) ? formatDate(macroDate(s.id)) : undefined}
            onClick={() => setModal({ type: 'macro', title: '환율 · 유가 · 전일 미국장' })}
          />
        ))}
        {/* VIX(공포 지수, Phase 5.112) — 참고용. 상승이 가격 상승이 아니므로 neutral(방향색 없음),
            25 이상일 때만 수준 단어를 경고색(주황)으로 표시한다. 신호·자동매매와 무관. */}
        <KpiTile
          label={VIX_SERIES.label}
          value={macroLatest(VIX_SERIES.id) != null ? macroLatest(VIX_SERIES.id).toFixed(2) : '-'}
          sub={
            <>
              <DiffArrow
                current={macroLatest(VIX_SERIES.id)}
                prev={macroPrev(VIX_SERIES.id)}
                formatter={(v) => v.toFixed(2)}
                neutral
              />
              {macroLatest(VIX_SERIES.id) != null && (
                <span className={macroLatest(VIX_SERIES.id) >= 25 ? 'kpi-sub-warn' : undefined}>
                  {vixLevelWord(macroLatest(VIX_SERIES.id))}
                </span>
              )}
              <StaleDate date={macroDate(VIX_SERIES.id)} baseDate={baseDate} prefix=" · " />
            </>
          }
          title={`S&P500 옵션 기반 30일 기대 변동성(전일 미국장 종가). 참고용 — 방향 신호 아님. 2023-07~2026-11 점검: 다음 날 코스피 방향과 무관, 25 이상이면 장중 시가 대비 −1.5% 이하 터치 비율 약 31%(15 미만 8%).${
            macroDate(VIX_SERIES.id) ? ` (${formatDate(macroDate(VIX_SERIES.id))})` : ''
          }`}
          onClick={() => setModal({ type: 'macro', title: '환율 · 유가 · 전일 미국장' })}
        />
      </div>

      {/* 4. 컴팩트 트리맵 */}
      <div className="section-title">업종 · 테마 강약</div>
      <div className="toggle-row">
        {GROUP_TYPE_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${groupType === opt.key ? 'active' : ''}`}
            onClick={() => setGroupType(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        <span className="toggle-hint">
          {groupLiveActive ? (groupLiveOpen ? '박스 크기 = 일별 거래대금 · 색(등락률) 1분 갱신 · 장중' : '박스 크기 = 일별 거래대금 · 색(등락률) 오늘 종가') : '일별 스냅샷 · 색 = 등락률'}
        </span>
      </div>
      {groupLoading && <SkeletonBlock variant="dtreemap" label="업종·테마 불러오는 중" />}
      {groupError && <div className="state error">{groupError}</div>}
      {!groupLoading && !groupError && (
        <GroupTreemap
          items={groupTreemapItems}
          sizeBy="value"
          height={200}
          onBoxClick={(name) =>
            setModal({ type: 'groupTopStocks', title: `${name} · 대장 종목`, groupType, name })
          }
        />
      )}

      {/* 코스피/코스닥 상위 종목 히트맵(사용자 원 요청 "코스피, 코스닥 상위 종목" —
          테마와 무관한 시장 전체 상위 종목). 새 fetch/새 폴링을 만들지 않고 위
          "종목 랭킹 요약"의 "거래대금 상위" 카드가 이미 쓰는 effectiveValueRank
          (live 활성이면 valueRankLive, 아니면 valueRankTop — loadValueRankLive()가
          이미 최신으로 유지)를 그대로 재사용한다. filterRowsByMarket으로 시장별로
          나누면 이미 거래대금 내림차순 — ETF는 배지 없이 개별 종목과 셀 하나로
          섞이면 "상위 종목"이 개별 종목이 아닌 ETF로 채워질 수 있어(코스피 거래대금
          상위엔 KODEX/TIGER류가 흔함) is_etf 행을 걸러낸 뒤 slice(0, 20)으로 개별
          종목만 상위 20개를 남긴다(다른 표들은 배지로 구분하지만 히트맵 셀은 배지를
          넣을 자리가 없다). */}
      <div className="section-title" style={{ marginTop: 16 }}>코스피 / 코스닥 상위 종목</div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>거래대금 상위 20종목(개별 종목만, ETF 제외) · 색 = 등락률</div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: 16,
          marginBottom: 16,
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>코스피 상위 종목</div>
          {heatmapLoading ? (
            <SkeletonBlock variant="dheat" label="상위 종목 불러오는 중" />
          ) : (
            <StockHeatmap
              items={(filterRowsByMarket(effectiveValueRank?.rows, 'kospi') || []).filter((r) => !r.is_etf).slice(0, 20)}
              minCellWidth={80}
              onCellClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)}
            />
          )}
        </div>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>코스닥 상위 종목</div>
          {heatmapLoading ? (
            <SkeletonBlock variant="dheat" label="상위 종목 불러오는 중" />
          ) : (
            <StockHeatmap
              items={(filterRowsByMarket(effectiveValueRank?.rows, 'kosdaq') || []).filter((r) => !r.is_etf).slice(0, 20)}
              minCellWidth={80}
              onCellClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)}
            />
          )}
        </div>
      </div>

      {/* 업종 자금 흐름 관찰(PLAN.md §5.33, 2026-07-28 JTBC 뉴스 클립 검토) — 업종·테마
          트리맵 바로 아래 배치(같은 "업종 단위" 관찰이라 인접 배치가 자연스럽다는 작업
          지시). "로테이션 있다/없다"를 판정하지 않는다 — 유입/유출 상위 업종의 평소
          대비 배율과 순환·이탈 보조 통계만 그대로 보여준다(quant/sector_rotation.py,
          SectorRotationCard 모듈 docstring 참고). group_snapshot(위 트리맵, 네이버
          업종 79개/테마 266개)과는 완전히 다른 분류 체계(ka10051, 코스피/코스닥별로도
          다른 코드 공간)라 트리맵과 데이터를 병합하지 않고 독립 카드로 둔다. */}
      <div className="section-title" style={{ marginTop: 16 }}>업종 자금 흐름 (관찰)</div>
      <div className="toggle-row">
        {SECTOR_ROTATION_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${sectorRotationMarket === opt.key ? 'active' : ''}`}
            onClick={() => setSectorRotationMarket(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        <span className="toggle-hint">일 1회 배치 · 외국인+기관계 합산 · 업종 자신의 평소(최대 20거래일) 대비 배율</span>
      </div>
      <SectorRotationCard data={sectorRotation[sectorRotationMarket]} loading={sectorQ.isLoading} />

      {/* 5. TOP5 요약 3열 — "…기준" 라벨은 대표 기준일(baseDate)과 같으면 생략, 다르면
          MM-DD만 붙인다(staleHintLabel, 대시보드 상단 표시와 동일 규칙). 정확한 날짜는
          카드 title(hover)로 확인 가능. */}
      <div className="section-title">종목 랭킹 요약</div>
      {/* 시장 필터(PLAN.md §5.15-3) — 거래대금 상위/실시간 관심 TOP5/스켈핑 후보
          3개 카드에만 적용된다(각 row에 market 필드가 있는 소스). 수급 상위는
          market이 일부 구간만 채워져 있고 ETF 경유 상위는 애초에 market 필드가
          없어(routers/flow_rank.py 참고) 두 카드는 필터 대상에서 뺐다 — 항상
          그대로 표시된다. */}
      <div className="toggle-row">
        {VALUE_RANK_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${rankingMarketFilter === opt.key ? 'active' : ''}`}
            onClick={() => setRankingMarketFilter(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        <span className="toggle-hint">거래대금 상위 · 실시간 관심 TOP5 · 스켈핑 후보에 적용</span>
      </div>
      <div className="top5-grid">
        <Top5Card
          title="수급 상위"
          hint={
            <>
              <span className="fr-toggle">
                {FLOW_RANK_INVESTOR_OPTIONS.map((opt) => (
                  <button
                    key={opt.key}
                    type="button"
                    className={`toggle-chip ${flowRankInvestor === opt.key ? 'active' : ''}`}
                    aria-pressed={flowRankInvestor === opt.key}
                    onClick={() => setFlowRankInvestor(opt.key)}
                  >
                    {opt.label}
                  </button>
                ))}
                {FLOW_RANK_SIDE_OPTIONS.map((opt) => (
                  <button
                    key={opt.key}
                    type="button"
                    className={`toggle-chip ${flowRankSide === opt.key ? 'active' : ''}`}
                    aria-pressed={flowRankSide === opt.key}
                    onClick={() => setFlowRankSide(opt.key)}
                  >
                    {opt.label}
                  </button>
                ))}
              </span>
              <span title="소스가 최근 확정된 거래일까지만 제공 — 당일 장마감 전에는 전날 데이터가 최신입니다.">
                {flowRankInvestor === 'foreign' ? '외국인' : '기관'} {flowRankSide === 'sell' ? '순매도' : '순매수'}
                <StaleDate date={flowRankTop?.date} baseDate={baseDate} prefix=" · 확정 " />
              </span>
            </>
          }
          hoverDate={flowRankTop?.date ? formatDate(flowRankTop.date) : undefined}
          loading={flowRankLoading}
          rows={flowRankTop?.rows}
          onMore={() => setModal({ type: 'flowRank', title: '수급 상위 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={row.code}
              clickable={!STATIC_DATA}
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              {/* net_value는 항상 양수 크기 — 방향은 선택한 side로 표현(순매도는 ▼ + 파랑) */}
              <span className={`top5-row-value ${flowRankSide === 'sell' ? 'down' : 'up'}`}>
                {flowRankSide === 'sell' ? '▼ ' : '▲ '}
                {eokLabel(row.net_value)}
              </span>
            </Top5RowTile>
          )}
        />
        <Top5Card
          title="거래대금 상위"
          hint={
            valueRankLiveActive
              ? '7분 갱신 · 장중'
              : effectiveValueRank?.date
                ? staleHintLabel(effectiveValueRank.date, baseDate)
                : undefined
          }
          hoverDate={effectiveValueRank?.date ? formatDate(effectiveValueRank.date) : undefined}
          loading={heatmapLoading}
          rows={filterRowsByMarket(effectiveValueRank?.rows, rankingMarketFilter)}
          onMore={() => setModal({ type: 'valueRank', title: '거래대금 상위 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={`${row.market}-${row.code}`}
              clickable={!STATIC_DATA}
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{eokLabel(row.value)}</span>
            </Top5RowTile>
          )}
        />
        <Top5Card
          title="ETF 경유 상위"
          // §5.6-2: 형제 카드(수급 상위="확정 MM-DD", 거래대금 상위="7분 경신")와
          // 달리 헤더에 날짜 배지가 없던 문제 — flow-path 응답 최상위 date를 "확정
          // MM-DD"로 항상 노출한다(수급 상위와 동일한 문구, staleHintLabel처럼
          // baseDate 대비 뒤처졌을 때만 표시하는 조건은 걸지 않는다 — 이 카드는
          // 배치 스냅샷이라 "언제 확정된 값인지"가 항상 의미 있는 정보이기 때문).
          // 2026-07-21 추가: "확정 07-21"(오늘 날짜)이 뜨니 다른 라이브 카드처럼
          // 계속 갱신될 거라고 오해하기 쉬웠다(사용자 지적: "몇 시간째 같았다") —
          // 이 값의 재료(etf_holdings/etf_stats/flow_rank)가 전부 하루 1회
          // 배치 산출물이라 원천적으로 하루 중 더 자주 바뀔 수 없다는 걸
          // "일 1회"로 명시한다.
          hint={
            <span title="구성 ETF 보유내역·순유입 통계 자체가 하루 1회만 갱신되는 배치 산출물입니다 — 장중에는 더 자주 바뀌지 않습니다. 기여 ETF마다 기준 날짜가 다를 수 있으니 배지에 마우스를 올려 확인하세요.">
              유입 · 일 1회 배치
              {flowPathTop?.date && (
                <span className="stale-date"> · 확정 {mmdd(flowPathTop.date)}</span>
              )}
            </span>
          }
          hoverDate={flowPathTop?.date ? formatDate(flowPathTop.date) : undefined}
          loading={flowPathQ.isLoading}
          rows={flowPathTop?.rows}
          onMore={() => setModal({ type: 'flowPath', title: 'ETF 경유 수급 상위 — 전체' })}
          renderRow={(row, i) => (
            <Top5RowTile key={row.code} clickable={!STATIC_DATA} onClick={() => openStockModal(row.code, row.name)}>
              <span className="top5-row-name">
                {i + 1}. {row.name || row.code}
              </span>
              <span className={`top5-row-value ${row.via_etf_net >= 0 ? 'up' : 'down'}`}>
                {eokLabel(row.via_etf_net)}
              </span>
            </Top5RowTile>
          )}
        />
        {/* ETF 비중 변화 상위 (PLAN.md §5.25, 알테오젠 사례) — "어떤 종목이 최근 ETF
            비중 변화를 겪었는지" 시장 전체 스크리닝. "ETF 경유 상위"(수급 금액 추정)와는
            완전히 다른 지표(비중 %p 변화)라 카드도 분리했다 — 두 카드 모두 하루 1회
            배치(etf_holdings/flow_path) 산출물이라 나란히 두어도 자연스럽다. */}
        <Top5Card
          title="ETF 비중 변화 상위"
          hint={
            <span title="etf_holdings는 각 ETF의 top10 구성만 매일 스냅샷합니다 — '신규편입'/'편출'이 top10 안팎 이동일 수 있어 실제 매수/매도 시작을 뜻하지 않습니다.">
              감별 · 일 1회 배치
              {etfWeightChangesTop?.curr_date && (
                <span className="stale-date"> · 확정 {mmdd(etfWeightChangesTop.curr_date)}</span>
              )}
            </span>
          }
          hoverDate={etfWeightChangesTop?.curr_date ? formatDate(etfWeightChangesTop.curr_date) : undefined}
          loading={etfWeightQ.isLoading}
          rows={etfWeightChangesTop?.changes}
          onMore={() => setModal({ type: 'etfWeightChanges', title: 'ETF 비중 변화 상위 — 전체' })}
          renderRow={(row, i) => (
            <Top5RowTile
              key={`${i}-${row.etf_code}-${row.code}`}
              clickable={!STATIC_DATA}
              onClick={() => openStockModal(row.code, row.name)}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code} · {row.etf_name || row.etf_code}
                </span>
                {row.is_active && <Badge kind="info">액티브</Badge>}
              </span>
              <span
                className={`top5-row-value ${row.event === '비중확대' || row.event === '신규편입' ? 'up' : 'down'}`}
              >
                {row.event}
              </span>
            </Top5RowTile>
          )}
        />
        {/* 실시간 관심 종목 TOP20(조회수 기준, live-only) — flowLive와 동일하게 정적
            배포에서는 attentionTop이 항상 null이라 카드가 "표시할 데이터가 없습니다"로
            자연히 빈 상태를 보여준다. 다른 3개 카드와 달리 행 자체가 클릭 가능해
            바로 종목 상세 모달로 이어진다(StockSearch onSelect와 동일한 모달 타입). */}
        <Top5Card
          title="실시간 관심 TOP5"
          hint="조회수 기준 · 60초 갱신"
          loading={attentionQ.isLoading}
          rows={filterRowsByMarket(attentionTop?.rows, rankingMarketFilter)}
          onMore={() => setModal({ type: 'attention', title: '실시간 관심 종목 — 전체' })}
          renderRow={(row) => (
            <Top5RowTile
              key={row.code}
              clickable
              onClick={() => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
            >
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank ?? '-'}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          )}
        />
        {/* 스켈핑 후보(PLAN.md §5.2) — 거래대금 상위(value-rank/live) + 실시간 관심순위
            (attention) 조합 스코어 상위. 참고용 스크리닝이지 매매 신호가 아니라는
            문구를 hint에 항상 노출한다(§5 전체 원칙). 행 클릭은 실시간 관심 TOP5와
            동일하게 바로 종목 상세 모달로 이어진다.
            2026-07-21 정직화: 후보군·점수·등락률·회전율은 전부 value-rank/live
            (7분 캐시, routers/scalp.py 참고)에서 오므로 실제로는 7분 단위로만
            바뀐다 — "관심 TOP" 배지(attention, 60초 캐시)만 1분 단위다. 예전
            "1분 갱신"은 배지 하나의 주기를 리스트 전체의 주기인 것처럼 표시해
            거짓이었다(실측: cached_at이 60~90초 간격 폴링에도 7분 동안
            그대로였다가 7분 경계에서만 바뀜, 코드 리뷰 결과와도 일치). */}
        <Top5Card
          title="스켈핑 후보"
          hint="참고용 스크리닝 — 매매 신호 아님 · 7분 갱신(관심 TOP 배지만 1분)"
          footnote={scalpHitrateFootnote(scalpHitrate)}
          loading={scalpQ.isLoading}
          rows={filterRowsByMarket(scalpCandidates?.rows, rankingMarketFilter)}
          onMore={() => setModal({ type: 'scalp', title: '스켈핑 후보 — 전체' })}
          renderRow={(row, i) => (
            <Top5RowTile
              key={row.code}
              clickable
              onClick={() => openStockModal(row.code, row.name, { market: row.market })}
            >
              {/* 사용자 지적(2026-07-21): 카드 폭이 좁아 배지 4개(시장·회전율·스코어·
                  관심TOP)가 이름칸을 다 밀어내 종목명이 안 보였다 — 회전율/스코어
                  배지는 정보 밀도가 낮은 보조 정보라 "전체 보기" 모달(ScalpCandidatesFullModal,
                  가로 폭이 훨씬 넓음)에서만 유지하고, 압축 카드에서는 다른 3개 랭킹
                  카드와 동일하게 배지 최대 2개(시장·관심TOP)만 남긴다.
                  PLAN.md §5.20-3/§5.27-2: 수급 유입(flow_net_value)·at_risk(하락폭
                  과다) 배지도 각각 "왜 상위인지"·"위험 성격이 다르다"는 이유로 압축
                  카드에 노출하기로 했었으나, 시장+flow+at_risk+관심TOP 4개가 전부
                  붙으면(예: 하락폭 과다 + 수급 유입이 동시에 뜨는 종목) 이름칸
                  (실측 약 206px)에 배지만 약 230px+가 들어가 라벨이 완전히 0px로
                  밀려나 종목명 전체가 안 보이는 문제가 재발했다(2026-07-29, 사용자
                  재지적 — Playwright로 실측 확인). CSS flex-basis 튜닝만으로는
                  물리적으로 공간이 부족해 해결 불가 — 배지 자체를 줄이는 수밖에
                  없었다.
                  2026-07-29 재수정: 압축 카드는 시장 배지 + "가장 긴급한 신호
                  배지 최대 1개"만 남긴다 — at_risk(하락 경고, 드물게 발생)가
                  있으면 그걸, 없으면 flow(핵심 스코어 근거)를 보여준다. 관심TOP은
                  이제 압축 카드에서 뺐다(둘 다 없을 때만 등장하는 경우가 드물고,
                  실시간 관심 TOP5 카드에서 이미 별도로 확인 가능) — 회전율/스코어와
                  마찬가지로 "전체 보기" 모달에서만 확인한다. 이렇게 하면 배지는
                  항상 최대 2개(시장+신호 1개)로 고정되어 이름칸이 항상 남는다. */}
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {atRiskBadgeLabel(row.at_risk) ? (
                  <Badge kind="info">{atRiskBadgeLabel(row.at_risk)}</Badge>
                ) : (
                  flowBadgeLabel(row.flow_net_value) && (
                    <Badge kind="info">{flowBadgeLabel(row.flow_net_value)}</Badge>
                  )
                )}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          )}
        />
      </div>


      <Modal open={Boolean(modal)} onClose={closeModal} title={modal?.title}>
        {modal?.type === 'candle' && <CandleModal market={modal.market} />}
        {modal?.type === 'sentiment' && <SentimentModal />}
        {modal?.type === 'breadth' && <BreadthModal />}
        {modal?.type === 'concentration' && <ConcentrationModal regime={regime} />}
        {modal?.type === 'fund' && <FundModal />}
        {modal?.type === 'shortSelling' && <ShortSellingModal />}
        {modal?.type === 'macro' && <MacroModal />}
        {modal?.type === 'flowSummary' && <FlowSummaryModal />}
        {modal?.type === 'foreignPosition' && <ForeignPositionModal />}
        {modal?.type === 'derivativeEtf' && <DerivativeEtfModal />}
        {modal?.type === 'expiryPattern' && <ExpiryPatternModal />}
        {modal?.type === 'hynixPositioning' && <HynixPositioningModal />}
        {modal?.type === 'macroBriefing' && <MacroBriefingModal />}
        {/* 랭킹 3종 전체 보기 모달 — onRowClick을 STATIC_DATA일 때 undefined로 넘겨
            행 클릭 자체를 비활성화한다(TOP5 카드와 동일한 정적 모드 판단, 위
            Top5RowTile 주석 참고). */}
        {modal?.type === 'flowRank' && (
          <FlowRankFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'valueRank' && (
          <ValueRankFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'flowPath' && (
          <FlowPathFullModal onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)} />
        )}
        {modal?.type === 'etfWeightChanges' && (
          <EtfWeightChangesFullModal
            onRowClick={STATIC_DATA ? undefined : (code, name) => openStockModal(code, name)}
          />
        )}
        {modal?.type === 'attention' && (
          <AttentionFullModal
            onSelectStock={(row) => openStockModal(row.code, row.name, { market: row.market, is_etf: row.is_etf })}
          />
        )}
        {modal?.type === 'scalp' && (
          <ScalpCandidatesFullModal
            onSelectStock={(row) => openStockModal(row.code, row.name, { market: row.market })}
          />
        )}
        {modal?.type === 'groupTopStocks' && (
          <GroupTopStocksModal
            groupType={modal.groupType}
            name={modal.name}
            onSelectStock={(row) => openStockModal(row.code, row.name)}
          />
        )}
        {modal?.type === 'stock' && <StockDetailModal code={modal.code} initial={modal.stock} />}
      </Modal>
    </div>
  )
}
