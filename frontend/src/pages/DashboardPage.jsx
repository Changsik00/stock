import { useEffect, useState } from 'react'
import {
  STATIC_DATA,
  fetchAttention,
  fetchBasis,
  fetchBasisLive,
  fetchBreadth,
  fetchBreadthIntradayAccumulated,
  fetchBreadthLive,
  fetchDerivativeFlow,
  fetchFlowConcentrationIntradayAccumulated,
  fetchFlowIntradayAccumulated,
  fetchFlowLive,
  fetchFlowPath,
  fetchFlowRank,
  fetchForeignPositionIntradayAccumulated,
  fetchFuturesFlowLive,
  fetchFxLive,
  fetchGroups,
  fetchGroupsLive,
  fetchGroupTopStocks,
  fetchIndexTilesLive,
  fetchMacroSeries,
  fetchMarketIntraday,
  fetchMarketSeries,
  fetchRegime,
  fetchScalpCandidates,
  fetchSentiment,
  fetchValueRank,
  fetchValueRankLive,
} from '../api'
import Badge from '../components/Badge'
import BreadthBadge from '../components/BreadthBadge'
import BreadthRatioChart from '../components/BreadthRatioChart'
import CandleChart from '../components/CandleChart'
import EtfDirectionCard from '../components/EtfDirectionCard'
import ForeignPositionChart from '../components/ForeignPositionChart'
import FlowChart from '../components/FlowChart'
import FlowPathTable from '../components/FlowPathTable'
import FlowRankTable from '../components/FlowRankTable'
import GroupTreemap from '../components/GroupTreemap'
import IntradayFlowChart from '../components/IntradayFlowChart'
import MacroChart from '../components/MacroChart'
import MarketFundChart from '../components/MarketFundChart'
import Modal from '../components/Modal'
import PeriodPicker from '../components/PeriodPicker'
import SentimentGauge from '../components/SentimentGauge'
import StockDetailModal from '../components/StockDetailModal'
import StockSearch from '../components/StockSearch'
import ValueRankTable from '../components/ValueRankTable'
import {
  DEFAULT_INVESTORS,
  INTRADAY_OPTIONS,
  INVESTOR_COLOR_VAR,
  MACRO_SERIES,
  MARKETS,
  MARKET_FUND_IDS,
  US_INDEX_SERIES,
} from '../constants'
import { formatDate } from '../format'

// 대시보드 탭 (PLAN.md §6 3.7-1, 사용자 원문: "장황하게 정보가 노출됨. 디테일은 뒤로
// 숨기고 핵심 숫자만. 차트는 모달/탭으로. 100개짜리 리스트도 뒤로.") — 기본 탭.
// 시장 탭(MarketPage)의 상세 컨트롤(투자자·기간·시장 토글이 있는 표/차트 전부)을
// 그대로 옮기지 않고, 여기서는 "핵심 숫자만" 골라 타일로 보여준 뒤 클릭 시에만 그
// 상세(차트/전체 리스트)를 모달로 연다. 데이터는 전부 기존 api.js 함수를 그대로
// 재사용한다 — 이 작업에서 백엔드/api.js는 건드리지 않는다(병렬 작업 중인 백엔드
// 검색/종목 API와 충돌 방지, PLAN.md §6 3.7-1 작업 지시).

const numFmt = new Intl.NumberFormat('ko-KR')
const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const joFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const scoreFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const countFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
// 환율(원/달러 1자리)·유가(달러 2자리) 타일 포맷 — 매크로 탭 통합(환율/WTI 타일 +
// 모달, 사용자 원문: "환율·유가 2~3개 차트만으로 탭 하나는 과하다").
const fxFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const oilFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
// 전일 미국장 4대 지수(PLAN.md §5.8) 타일 포맷 — pt 단위, 소수 1자리(WTI의 달러 2자리
// 관례와 달리 지수 자체가 큰 값이라 1자리면 충분).
const usIndexFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
// 베이시스(K200 선물-현물, pt) 타일 포맷 — PLAN.md §4.5-3/-5.
const basisFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

// KPI 타일 초기 캔들 모달 기본 기간 — MarketPage와 동일하게 90일(3M)에서 시작한다.
const DEFAULT_CANDLE_DAYS = 90
// 자금(예탁금/대차잔고/신용융자) 모달 차트 기본 기간 — 추세를 보려면 90일보다 넉넉해야
// 자연스럽다.
const DEFAULT_FUND_DAYS = 180
// 매크로(환율/유가) 모달 차트 기본 기간 — 옛 MacroPage.jsx와 동일하게 1Y로 시작한다
// (환율/유가는 자금 지표보다 변동 주기가 길어 1년 창이 자연스럽다는 기존 판단 유지).
const DEFAULT_MACRO_DAYS = 365
// 환율/WTI 타일의 최신값·전일비 계산용 — 최근 2거래일만 있으면 되므로 짧게 잡는다
// (fundSeries가 fundLatest/fundPrev용으로 10일을 쓰는 관례와 동일).
const MACRO_TILE_DAYS = 10
// 투자자별 수급 요약 타일 + 모달 — 시장 탭과 동일하게 3M 기본.
const DEFAULT_FLOW_DAYS = 90
// 외인 양손(현물·선물·베이시스) 섹션 — PLAN.md §4.5-5. 타일 최신값 계산용으로는
// 짧은 창(10일)이면 충분하고(마지막 값 + 전일 비교), 상세 모달(ForeignPositionModal)은
// 시장 탭과 동일하게 90일 기본으로 시작한다.
const FOREIGN_POSITION_TILE_DAYS = 10
const DEFAULT_FOREIGN_POSITION_DAYS = 90
// 투자자별 수급 요약·외인 양손 상세 모달의 3M(EOD)/1D(오늘 장중 누적) 토글
// (PLAN.md §5.4-4) — StockDetailModal.jsx의 INTRADAY_OPTIONS 토글과 같은
// toggle-row/toggle-chip 패턴을 재사용한다. 기본값은 '1D'다(2026-07-21 요구사항
// 갱신 — 모달을 열면 오늘 장중 누적을 먼저 보여주고, 3M은 사용자가 명시적으로
// 전환해야 보인다).
const CHART_MODE_OPTIONS = [
  { key: '1D', label: '1D' },
  { key: '3M', label: '3M' },
]
// BreadthModal("등락 종목수") 전용 토글(PLAN.md §5.13) — 이 모달은 3M(일별 히스토리)
// 차트가 없고 원래부터 "현재"(breadth/live 60초 스냅샷 배지)만 보여줬다. 여기에 순간
// 스냅샷만으로는 놓치는 시간 흐름을 볼 수 있는 "1D 추이"(상승비율 라인차트) 탭을
// 추가한다 — CHART_MODE_OPTIONS(1D/3M)를 그대로 재사용하면 존재하지 않는 3M 탭처럼
// 보이므로 이 모달 전용 옵션을 별도로 둔다(toggle-row/toggle-chip 패턴은 동일하게
// 재사용).
const BREADTH_MODE_OPTIONS = [
  { key: 'live', label: '현재' },
  { key: '1D', label: '1D 추이' },
]
// FlowSummaryModal(투자자별 수급 요약) 3M/1D 공통 시장 필터(PLAN.md §5.10,
// 2026-07-22) — "코스피로 다 몰려 있어서 코스닥이 주목 받는 날을 못 본다"는
// 사용자 요구로 코스피/코스닥을 분리해 볼 수 있게 한 토글. VALUE_RANK_MARKET_OPTIONS와
// 같은 3분기 패턴이지만 라벨만 "합계"로 다르다(거래대금 상위는 "전체"가 더
// 자연스럽고, 여기는 두 시장을 더한 값이라 "합계"가 더 정확한 표현). 기본값은
// 지금까지의 동작과 동일한 'all'(합계).
const FLOW_MARKET_FILTER_OPTIONS = [
  { key: 'all', label: '합계' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]
// 1D 탭 기간 선택(PLAN.md §5.14, 2026-07-22) — intraday-accumulated가 순수
// 메모리 버퍼에서 DB(intraday_sample) 영속화로 바뀌면서 재배포에도 데이터가
// 남고 과거 조회도 가능해졌다. FlowSummaryModal/ForeignPositionModal/
// BreadthModal의 1D 탭이 모두 공유하는 토글 — 최근 7일은 60초 원본, 8일 전부터는
// 15분 압축본이 섞여 나온다(collectors/intraday_compaction.py 배치). 기본값은
// 지금까지의 동작과 동일한 1일.
const INTRADAY_DAYS_OPTIONS = [
  { key: 1, label: '1일' },
  { key: 7, label: '7일' },
  { key: 30, label: '30일' },
]
// 프로그램매매 차익 순매수(macro_series prog_arb_*, PLAN.md §4.5-4) — 코스피+코스닥
// 합산해서 "프로그램 차익 순매수" 타일 하나로 보여준다(신용융자 타일의 creditLoanSum과
// 동일한 관례).
const PROGRAM_ARB_IDS = ['prog_arb_kospi', 'prog_arb_kosdaq']
// 만기 임박 시그널 배지 기준(D-n 이내, PLAN.md §4.5-5 "만기 D-3 이내").
const EXPIRY_SOON_D_DAY = 3
const GROUP_TYPE_OPTIONS = [
  { key: 'upjong', label: '업종' },
  { key: 'theme', label: '테마' },
]
const VALUE_RANK_MARKET_OPTIONS = [
  { key: 'all', label: '전체' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]
const FLOW_RANK_LOOKBACK_DAYS = 7
// 1분 티어(등락 종목수 breadth/live·장중 잠정 수급 flow/live·실시간 관심 TOP20
// attention·스켈핑 후보 scalp-candidates) 자동 갱신 주기 — 백엔드 각각 60초 캐시
// (routers/markets.py)와 맞춘다. 2026-07-20까지는 이 값을 쓰는 두 useEffect
// (DashboardPage 본문 + BreadthModal) 모두 최초 1회만 fetch하고 재폴링이 없어서
// 아무도 요청하지 않으면 화면이 멈춰 있는 버그가 있었다 — 서버 측 능동 60초 갱신
// 작업(PLAN.md)과 함께 수정. breadth·flowLive·attentionTop·scalpCandidates는
// 원래 독립 setInterval 4개였으나 1분 티어 useEffect 하나로 통합했다(BreadthModal의
// setInterval은 모달 전용이라 별개로 남는다).
//
// 2026-07-21(§5.5-2)부터 업종/테마 등락률 groups·베이시스 basis·외인 선물수급
// futures-flow도 이 1분 티어에 합류했다(원래 7분 티어 소속, 아래 EXTRA_LIVE_POLL_MS
// 주석 참고) — 백엔드 캐시 TTL 자체는 여전히 420초(routers/basis.py·groups.py·
// markets.py)라 프런트가 60초마다 재요청해도 캐시가 갱신될 때만 새 값을 받는다.
// 즉 서버 부담은 늘지 않고(캐시 미스 빈도 그대로), 프런트가 캐시 갱신 시점을 더
// 빨리(최대 1분 지연) 따라잡을 뿐이다.
const BREADTH_LIVE_POLL_MS = 60_000
// 7분 티어 — 이제 거래대금 상위(value-rank) 하나만 남았다. 백엔드 5~10분 캐시
// (routers/flow_rank.py의 LIVE_TTL_SECONDS=420초)와 맞춘다(PLAN.md §4.7 3단 갱신
// 주기, 2026-07-20 장중 실측으로 편입). value-rank만 7분을 유지하는 이유(§5.5-2
// 진단②): 코스피+코스닥 전 종목(~4,300종목) 페이지네이션이 필요해 사이클당
// ~44요청·13초+가 걸리는 진짜 비싼 호출이라, 유가(yfinance) 429 차단 전례와 같은
// 리스크 카테고리로 보수적으로 유지한다. 업종/테마·베이시스·외인선물수급은 목록·
// 단일 조회 1회뿐이라 비용이 없어 위 1분 티어로 옮겼다. 수급 상위(flow-rank)는
// 실측 결과 소스가 2영업일 이상 지연돼 있어 애초에 라이브로 편입하지 않았다 —
// EOD(FLOW_RANK_LOOKBACK_DAYS 기준) 그대로 유지.
const EXTRA_LIVE_POLL_MS = 420_000

function eokLabel(million) {
  if (typeof million !== 'number') return '-'
  return `${eokFmt.format(million / 100)}억원`
}

// PLAN.md §5.17 — 가속도 카드 문구용(부호 있는 억원 표기, "+12.3억원"/"-4.5억원").
// eokLabel은 부호를 안 붙이므로(음수는 Intl 기본 마이너스만) 별도로 분리한다.
function signedEokLabel(million) {
  if (typeof million !== 'number') return '-'
  const sign = million > 0 ? '+' : ''
  return `${sign}${eokFmt.format(million / 100)}억원`
}

function trillion(million) {
  if (million === null || million === undefined) return null
  return million / 1e6
}

function trillionLabel(million) {
  const t = trillion(million)
  return t === null ? '-' : `${joFmt.format(t)}조`
}

function fxLabel(value) {
  if (typeof value !== 'number') return '-'
  return `${fxFmt.format(value)}원`
}

function oilLabel(value) {
  if (typeof value !== 'number') return '-'
  return `$${oilFmt.format(value)}`
}

function usIndexLabel(value) {
  if (typeof value !== 'number') return '-'
  return usIndexFmt.format(value)
}

function basisLabel(value) {
  if (typeof value !== 'number') return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${basisFmt.format(value)}pt`
}

function scoreClass(score) {
  if (score === null || score === undefined) return ''
  if (score > 2) return 'up'
  if (score < -2) return 'down'
  return 'flat'
}

function scoreLabel(score) {
  if (score === null || score === undefined) return '-'
  const sign = score > 0 ? '+' : ''
  return `${sign}${scoreFmt.format(score)}%`
}

function rateClass(rate) {
  if (rate === null || rate === undefined) return ''
  return rate > 0 ? 'up' : rate < 0 ? 'down' : ''
}

function rateLabel(rate) {
  if (rate === null || rate === undefined) return '-'
  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

// 스켈핑 후보 카드 전용(PLAN.md §5.2) — turnover(회전율 %)는 근거 배지 문구로만
// 쓴다("회전율 8.2%"). score는 z-score 가중합이라(app/quant/screener.py 참고)
// sentiment 게이지의 scoreClass(-100~100 기준)를 재사용하면 임계값이 전혀 안
// 맞으므로 별도로 부호만 붙인 중립 표기를 쓴다 — 매매 방향을 암시하는 색(up/down)은
// 의도적으로 넣지 않는다(참고용 스크리닝, 매매 신호 아님 원칙).
function turnoverBadgeLabel(turnover) {
  if (turnover === null || turnover === undefined) return null
  return `회전율 ${turnover.toFixed(1)}%`
}

function scalpScoreLabel(score) {
  if (score === null || score === undefined) return '-'
  const sign = score > 0 ? '+' : ''
  return `${sign}${score.toFixed(2)}`
}

function scalpScoreBadgeLabel(score) {
  if (score === null || score === undefined) return null
  return `스코어 ${scalpScoreLabel(score)}`
}

// MM-DD만 뽑는다 (StaleDate/TOP5 "…기준" 라벨 공용) — formatDate가 이미
// 'YYYY-MM-DD'로 정규화하므로 뒤 5글자만 자르면 된다.
function mmdd(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? d.slice(5) : d
}

// 여러 후보 날짜 중 최신값 — 대표 기준일(대시보드 상단 1회 표시) 계산용.
// 소스마다 'YYYYMMDD'/'YYYY-MM-DD'가 섞여 올 수 있어 formatDate로 정규화한 뒤
// 문자열 비교한다(정규화된 'YYYY-MM-DD'는 사전순 비교 = 날짜순 비교).
function latestOf(...rawDates) {
  const normalized = rawDates.map((d) => formatDate(d)).filter((d) => typeof d === 'string' && d.length === 10)
  if (normalized.length === 0) return null
  return normalized.reduce((a, b) => (b > a ? b : a))
}

// 대표 기준일(baseDate)보다 뒤처진 타일에만 붙는 작은 회색 날짜(MM-DD) — "뒤처짐" 신호.
// 사용자 피드백: "타일마다 날짜가 있는데 중복이다. 어차피 마지막 거래일일 텐데" —
// 실제로는 소스별 시차가 있어 다를 수 있으므로, 최신인 타일은 아무것도 표시하지
// 않고(대표 기준일과 같다고 간주) 뒤처진 타일에만 예외적으로 이 배지를 남긴다.
// 정확한 날짜는 항상 타일의 title 속성(hover)으로 확인 가능하다.
function StaleDate({ date, baseDate, prefix = '' }) {
  const d = formatDate(date)
  if (typeof d !== 'string' || d.length !== 10 || !baseDate || d >= baseDate) return null
  return (
    <span className="stale-date" title={d}>
      {prefix}
      {mmdd(date)}
    </span>
  )
}

// TOP5 카드 "…기준" 라벨 — 대표 기준일과 같으면 생략(중복), 다르면 MM-DD만 붙인다.
// suffix가 있으면(예: ETF 경유 상위의 "유입") 날짜가 없을 때도 suffix만 별도로
// 붙일 수 있도록 호출부에서 처리한다(이 함수는 "뒤처졌을 때"만 문자열을 낸다).
function staleHintLabel(date, baseDate, suffix) {
  const d = formatDate(date)
  if (typeof d !== 'string' || d.length !== 10 || !baseDate || d >= baseDate) return null
  return suffix ? `${mmdd(date)} 기준 · ${suffix}` : `${mmdd(date)} 기준`
}

// 전일比 화살표 — prev가 없으면(첫 값) 표시하지 않는다.
// neutral=true면 up/down 색상 클래스를 붙이지 않는다(중립 회색) — 환율 타일 전용.
// 환율 상승이 "좋은 것"이 아니라 주가 등락(빨강=상승/파랑=하락) 관례와 혼동될 수
// 있어, 화살표 방향·값은 그대로 두고 색만 뺀다(다른 타일은 예탁금 타일과 동일하게
// up/down 색을 그대로 쓴다).
function DiffArrow({ current, prev, formatter, neutral = false }) {
  if (current === null || current === undefined || prev === null || prev === undefined) return null
  const diff = current - prev
  if (diff === 0) return <span className="kpi-tile-sub">보합</span>
  const up = diff > 0
  const cls = neutral ? '' : up ? 'up' : 'down'
  return (
    <span className={`kpi-tile-sub ${cls}`}>
      {up ? '▲' : '▼'} {formatter(Math.abs(diff))}
    </span>
  )
}

// 사용자 지적(2026-07-23): "point, 원, 달러 같은거 얼마나 변동됐는지 % 로 다
// 처리해줘" — DiffArrow의 formatter는 절대 차액(diffAbs)만 받으므로, 전일값
// (prevValue)을 알고 있는 호출부가 이 헬퍼로 "(N.NN%)" 접미사를 붙인다.
// prevValue가 없거나 0이면(분모가 0인 경우 포함) 빈 문자열 — 계산 불가 상황을
// 조용히 생략한다(억지로 0%나 에러를 보여주지 않음).
function pctSuffix(diffAbs, prevValue) {
  if (!prevValue) return ''
  return ` (${((diffAbs / Math.abs(prevValue)) * 100).toFixed(2)}%)`
}

// 두 시장(코스피/코스닥)의 flows(투자자 -> [{date, net_value, net_volume}])를 투자자·
// 날짜 기준으로 합산한다 — market_flow는 시장별로만 적재되고 백엔드에 "합계" 엔드포인트가
// 없어(routers/markets.py MARKETS={kospi,kosdaq,futures}, FLOW_MARKETS={kospi,kosdaq})
// "시장 종합 수급"을 보여주려면 클라이언트에서 더해야 한다.
function mergeFlows(flowsA, flowsB) {
  const investors = new Set([...Object.keys(flowsA || {}), ...Object.keys(flowsB || {})])
  const merged = {}
  for (const inv of investors) {
    const byDate = new Map()
    for (const arr of [flowsA?.[inv] || [], flowsB?.[inv] || []]) {
      for (const e of arr) {
        const row = byDate.get(e.date) || { date: e.date, net_value: 0, net_volume: 0 }
        row.net_value += e.net_value || 0
        row.net_volume += e.net_volume || 0
        byDate.set(e.date, row)
      }
    }
    merged[inv] = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1))
  }
  return merged
}

// 1D(오늘 장중 누적) 두 시장의 단일 투자자 시리즈([{time, value}])를 시간(time) 키
// 기준으로 합산한다(PLAN.md §5.10) — 위 mergeFlows는 3M(EOD, {date, net_value,
// net_volume}) 전용이라 재사용할 수 없어 더 단순한 1D 전용 버전을 따로 둔다.
// 백엔드 intraday_snapshot._merge_foreign_spot_series와 동일한 방식(시간 문자열
// 매칭, 먼저 등장한 순서 보존) — 두 시장이 항상 같은 warm 틱에서 함께 append되므로
// 보통 인덱스도 일치하지만, 한쪽만 있는 시각이 있어도 그 값 그대로 반영된다.
function mergeIntradayByTime(seriesA, seriesB) {
  const order = []
  const totals = new Map()
  for (const arr of [seriesA || [], seriesB || []]) {
    for (const p of arr) {
      if (!totals.has(p.time)) {
        order.push(p.time)
        totals.set(p.time, 0)
      }
      totals.set(p.time, totals.get(p.time) + (p.value || 0))
    }
  }
  return order.map((time) => ({ time, value: totals.get(time) }))
}

// 코스피/코스닥 "쏠림" 비율(PLAN.md §5.18) — flow/live 응답(fetchFlowLive의
// { kospi: {investors}, kosdaq: {investors} } 모양)에서 코스피·코스닥 각각의
// "활동량"(|외국인 순매수|+|기관계 순매수|, 방향 무관 절댓값)을 계산해 쏠림%를
// 구한다. 백엔드 collectors/intraday_snapshot.py의 get_market_concentration_series
// 와 동일한 지표 정의 — 그쪽은 DB에 적립된 1D 시계열을 계산하고, 이 헬퍼는 KPI
// 타일/모달의 "현재" 탭이 이미 폴링 중인 flow/live 스냅샷 하나로 즉석 계산한다
// (새 API 호출 없음, breadthTotals와 동일한 "이미 fetch한 값을 프런트에서 합산"
// 관례). 활동량 분모가 0이면(둘 다 활동 없음) 쏠림을 정의할 수 없어 null.
function computeConcentration(flowLive) {
  if (!flowLive) return null
  const activity = (market) => {
    const investors = flowLive[market]?.investors
    const foreign = investors?.['외국인']?.net_value
    const inst = investors?.['기관계']?.net_value
    return Math.abs(foreign ?? 0) + Math.abs(inst ?? 0)
  }
  const kospiActivity = activity('kospi')
  const kosdaqActivity = activity('kosdaq')
  const denom = kospiActivity + kosdaqActivity
  if (denom <= 0) return null
  const kospiShare = (kospiActivity / denom) * 100
  return { kospiShare, kosdaqShare: 100 - kospiShare, moreActive: kospiShare >= 50 ? '코스피' : '코스닥' }
}

// flows(투자자 -> [{date, net_value, net_volume}])에서 특정 투자자의 가장 최근 행을
// 뽑는다 — market_flow 계열 응답을 다루는 여러 곳(외인 현물/선물 타일)에서 공용으로 쓴다.
function latestFlowRow(flows, investor) {
  const rows = flows?.[investor]
  return rows && rows.length > 0 ? rows[rows.length - 1] : null
}

// 종목 랭킹 요약 카드(거래대금 상위/실시간 관심 TOP5/스켈핑 후보)의 시장 필터
// (PLAN.md §5.15-3) — rows 각 행에 이미 있는 market 필드('kospi'|'kosdaq'|null)로
// 걸러낸다. 'all'이면 그대로, 필터가 시장 하나로 좁혀지면 market이 없는(null)
// 행은 그 시장 소속인지 알 수 없으니 제외한다.
function filterRowsByMarket(rows, marketFilter) {
  if (!rows) return rows
  if (marketFilter === 'all') return rows
  return rows.filter((r) => r.market === marketFilter)
}

// 차트 X축 라벨 — 'YYYY-MM-DD' -> 'MM/DD' (MacroChart.jsx/MarketFundChart.jsx의
// dateLabel과 동일한 관례, 여기서는 formatDate로 먼저 정규화해 'YYYYMMDD' 등도 대응).
function chartDateLabel(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : d
}

// ---------------------------------------------------------------------------
// KPI 타일 — 클릭 가능한 순수 버튼. 값 자체 계산/포맷은 호출부가 마친 뒤 넘긴다.
// ---------------------------------------------------------------------------
function KpiTile({ label, value, valueClass, sub, onClick, title }) {
  return (
    <button type="button" className="kpi-tile" onClick={onClick} title={title}>
      <span className="kpi-tile-label">{label}</span>
      <span className={`kpi-tile-value ${valueClass || ''}`}>{value}</span>
      {sub}
    </button>
  )
}

// ---------------------------------------------------------------------------
// 모달 본문 컴포넌트 — Modal이 open=false일 때 렌더 트리에서 완전히 빠지므로(Modal.jsx
// 주석 참고) 아래 컴포넌트들은 "마운트될 때(=모달이 열릴 때)"만 데이터를 불러온다.
// MarketPage.jsx의 동일 섹션 로직을 모달 안에서 재현한 것으로, 백엔드 응답 스키마·
// 상태 관리 패턴은 그대로 가져왔다(중복이지만 페이지 단위로 완전히 분리하라는
// 작업 지시에 따라 MarketPage를 import하지 않고 이 파일 안에서 자기완결로 둔다).
// ---------------------------------------------------------------------------

// 지수 타일 클릭 시 뜨는 캔들 모달 — 분봉 토글(PLAN.md §5.5-1) 추가 전에는 90일
// EOD만 보여줘 다른 수급 모달들(1D 기본)과 기본값이 어긋나 있었다. MarketPage.jsx의
// intradayMode 토글과 동일한 패턴을 이식한다(코드 재사용보다 최소 침습 이식을
// 선택 — 작업 지시 참고). 기본값은 1분(§5.5-1), 선물은 분봉 소스가 없어
// (routers/markets.py 501) 'daily'로 강제, 정적 배포(STATIC_DATA)도 실시간
// 온디맨드 소스가 없어 'daily'로 시작하고 토글 UI 자체를 숨긴다.
function CandleModal({ market }) {
  const label = MARKETS.find((m) => m.key === market)?.label || market
  const [intradayMode, setIntradayMode] = useState(STATIC_DATA || market === 'futures' ? 'daily' : 1)
  const [days, setDays] = useState(DEFAULT_CANDLE_DAYS)
  const [prices, setPrices] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [intradayBars, setIntradayBars] = useState([])
  const [intradayDate, setIntradayDate] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)

  // 선물엔 분봉 옵션이 없다 — 혹시라도 futures 모달이 분봉 모드로 남아 있으면
  // 되돌린다(MarketPage.jsx와 동일한 안전장치).
  useEffect(() => {
    if (market === 'futures' && intradayMode !== 'daily') setIntradayMode('daily')
  }, [market, intradayMode])

  useEffect(() => {
    if (STATIC_DATA || intradayMode === 'daily' || market === 'futures') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchMarketIntraday(market, intradayMode)
      .then((body) => {
        if (!cancelled) {
          setIntradayBars(body.bars || [])
          setIntradayDate(body.date)
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
  }, [market, intradayMode])

  useEffect(() => {
    if (intradayMode !== 'daily') return undefined
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMarketSeries(market, days)
      .then((body) => {
        if (!cancelled) setPrices(body.prices || [])
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
  }, [market, days, intradayMode])

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        {label} · 캔들 + 거래량
      </div>

      {!STATIC_DATA && (
        <div className="toggle-row">
          {INTRADAY_OPTIONS.map((opt) => {
            const disabled = market === 'futures' && opt.key !== 'daily'
            return (
              <button
                key={opt.key}
                type="button"
                className={`toggle-chip ${intradayMode === opt.key ? 'active' : ''}`}
                disabled={disabled}
                title={disabled ? 'K200 선물은 분봉 데이터 소스가 없습니다' : undefined}
                onClick={() => setIntradayMode(opt.key)}
              >
                {opt.label}
              </button>
            )
          })}
          <span className="toggle-hint">
            {intradayMode === 'daily' ? '분봉은 오늘 하루치만 제공' : '오늘 하루치 · 참고용'}
          </span>
        </div>
      )}

      {intradayMode === 'daily' && (
        <>
          <PeriodPicker value={days} onChange={setDays} />
          {loading && <div className="state">불러오는 중…</div>}
          {error && <div className="state error">{error}</div>}
          {!loading && !error && prices && prices.length > 0 && <CandleChart data={prices} height={320} />}
          {!loading && !error && prices && prices.length === 0 && (
            <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
          )}
        </>
      )}

      {intradayMode !== 'daily' && (
        <>
          {intradayLoading && <div className="state">불러오는 중…</div>}
          {intradayError && <div className="state error">{intradayError}</div>}
          {!intradayLoading && !intradayError && intradayBars.length === 0 && (
            <div className="state">오늘 분봉 데이터가 없습니다(장 시작 전이거나 휴장일 수 있음).</div>
          )}
          {!intradayLoading && !intradayError && intradayBars.length > 0 && (
            <CandleChart
              key={`${market}-${intradayMode}`}
              data={intradayBars}
              intraday
              height={320}
              title={`캔들 · 거래량 (${intradayMode}분봉 · ${formatDate(intradayDate)})`}
            />
          )}
        </>
      )}
    </div>
  )
}

function SentimentModal() {
  const [sentiment, setSentiment] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
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
  }, [])

  return (
    <SentimentGauge
      loading={loading}
      error={error}
      score={sentiment?.score ?? null}
      approx={sentiment?.approx ?? true}
      components={sentiment?.components ?? null}
    />
  )
}

function BreadthModal() {
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

function ConcentrationModal() {
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

  const concentration = computeConcentration(flowLive)

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
            쏠림% = 코스피 활동량 / (코스피+코스닥 활동량) × 100, 활동량 = |외국인 순매수|+|기관계 순매수|
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

function FundModal() {
  const [days, setDays] = useState(DEFAULT_FUND_DAYS)
  const [seriesMap, setSeriesMap] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMacroSeries(MARKET_FUND_IDS, days)
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && <MarketFundChart seriesMap={seriesMap} />}
    </div>
  )
}

// 환율(USD/KRW) · WTI · 브렌트 라인차트 3개 + 전일 미국장 4대 지수(S&P500/나스닥/다우/SOX,
// PLAN.md §5.8) + 기간 선택 — 옛 MacroPage.jsx를 그대로 모달로 옮긴 것이다(차트 렌더
// 로직은 components/MacroChart.jsx로 뽑아 공용화). 타일은 환율/WTI/미국 4대 지수만 두지만
// (브렌트는 타일 생략 지시) 모달에서는 기존 3개 라인 + 미국 4대 지수까지 전부 보여준다
// (환율/유가와 스케일이 달라 섹션 제목으로만 구분하고 차트 자체는 나누지 않는다).
function MacroModal() {
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

function FlowSummaryModal() {
  const [chartMode, setChartMode] = useState(STATIC_DATA ? '3M' : '1D')
  // 코스피/코스닥 분리 토글(PLAN.md §5.10, 2026-07-22) — 기본은 지금까지의
  // 동작과 동일한 'all'(합계), 3M/1D 두 탭 모두 이 필터를 공유한다.
  const [marketFilter, setMarketFilter] = useState('all')
  const [days, setDays] = useState(DEFAULT_FLOW_DAYS)
  // 3M: 코스피/코스닥 원본 flows를 각각 보관해 두고(이미 두 번 fetch하던 그대로),
  // marketFilter가 바뀔 때 재요청 없이 즉시 합계/개별로 다시 계산한다.
  const [flowsByMarket, setFlowsByMarket] = useState({ kospi: {}, kosdaq: {} })
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
    Promise.all([fetchMarketSeries('kospi', days), fetchMarketSeries('kosdaq', days)])
      .then(([kospiBody, kosdaqBody]) => {
        if (!cancelled) setFlowsByMarket({ kospi: kospiBody.flows || {}, kosdaq: kosdaqBody.flows || {} })
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
    fetchFlowIntradayAccumulated(intradayDays)
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

  // marketFilter에 따라 3M flows를 합계/코스피/코스닥으로 분기(PLAN.md §5.10) —
  // 'all'은 지금까지와 동일한 mergeFlows, 나머지는 fetch해 둔 원본을 그대로 쓴다.
  const flows =
    marketFilter === 'kospi'
      ? flowsByMarket.kospi
      : marketFilter === 'kosdaq'
        ? flowsByMarket.kosdaq
        : mergeFlows(flowsByMarket.kospi, flowsByMarket.kosdaq)
  const hasFlows = Object.keys(flows || {}).length > 0

  // net_value는 백만원 단위(market_flow와 동일) — FlowChart.jsx eok() 관례와
  // 통일해 억원으로 변환한 뒤 IntradayFlowChart에 넘긴다. 1D 응답이 이제
  // series.kospi/series.kosdaq로 나뉘어 오므로(§5.10) marketFilter에 따라 한쪽만
  // 쓰거나 mergeIntradayByTime으로 시간 키 기준 합산한다.
  const intradaySeries = {}
  for (const name of ['개인', '외국인', '기관계']) {
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
    marketFilter === 'kospi' ? '코스피' : marketFilter === 'kosdaq' ? '코스닥' : '코스피+코스닥 합계'

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        {marketFilterLabel} (선물 제외 — 투자자별 수급 미수집)
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

// 외인 양손 상세 — 외인 현물 vs 선물 순매수 시계열 + 베이시스 오버레이(PLAN.md §4.5-5
// 시그널 배지 클릭 시 열리는 모달). 코스피+코스닥(현물) + 선물 + 베이시스 3개 소스를
// 날짜 기준으로 병합한다 — CreditLoanChart(MarketFundChart.jsx)와 동일한 "여러 시리즈를
// Map으로 합친 뒤 라인 여러 개를 겹쳐 그리는" 패턴.
function ForeignPositionModal() {
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

// 개인 파생ETF 방향성 게이지 상세 — EtfDirectionCard(순수 프레젠테이션, PLAN.md §4.5-1)에
// derivative-flow 데이터를 붙여주는 자기완결 래퍼. 컴팩트 타일에서 클릭했을 때만
// 마운트되므로(Modal.jsx 주석 참고) 열 때마다 최신 데이터를 새로 받는다.
function DerivativeEtfModal() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchDerivativeFlow(90)
      .then((body) => {
        if (!cancelled) setData(body)
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
  }, [])

  return (
    <EtfDirectionCard
      loading={loading}
      error={error}
      universe={data?.universe}
      latest={data?.latest}
      series={data?.series ?? []}
    />
  )
}

function FlowRankFullModal({ onRowClick }) {
  const [investor, setInvestor] = useState('foreign')
  const [side, setSide] = useState('buy')
  const [dates, setDates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowRank(investor, side, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setDates(body.dates || [])
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
  }, [investor, side])

  return (
    <FlowRankTable
      investor={investor}
      onInvestorChange={setInvestor}
      side={side}
      onSideChange={setSide}
      loading={loading}
      error={error}
      dates={dates}
      onRowClick={onRowClick}
    />
  )
}

function ValueRankFullModal({ onRowClick }) {
  const [market, setMarket] = useState('all')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchValueRank(market, FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) {
          setDate(body.date)
          setRows(body.rows || [])
        }
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
  }, [market])

  return (
    <div>
      <div className="toggle-row">
        {VALUE_RANK_MARKET_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${market === opt.key ? 'active' : ''}`}
            onClick={() => setMarket(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <ValueRankTable rows={rows} loading={loading} error={error} date={date} onRowClick={onRowClick} />
    </div>
  )
}

function FlowPathFullModal({ onRowClick }) {
  const [direction, setDirection] = useState('in')
  const [date, setDate] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 30, direction)
      .then((body) => {
        if (!cancelled) {
          setDate(body.date)
          setRows(body.rows || [])
        }
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
  }, [direction])

  return (
    <FlowPathTable
      loading={loading}
      error={error}
      date={date}
      rows={rows}
      direction={direction}
      onDirectionChange={setDirection}
      onRowClick={onRowClick}
    />
  )
}

// 실시간 관심 종목 TOP20 전체 보기 — ValueRankFullModal/FlowPathFullModal과 동일하게
// 마운트 시(모달이 열릴 때) 자기 데이터를 불러오는 자기완결 컴포넌트다. 다른
// FullModal들과 달리 행이 클릭 가능해야 하므로(종목 상세 모달로 전환) 호출부
// (DashboardPage)가 onSelectStock 콜백을 넘겨준다 — 이 컴포넌트 자체는 setModal을
// 모르므로 이 방식으로만 상위 상태를 바꿀 수 있다. 20행짜리 단순 목록이라 별도
// 테이블 컴포넌트 파일(ValueRankTable처럼)로 뽑지 않고 여기 인라인으로 둔다.
function AttentionFullModal({ onSelectStock }) {
  const [attention, setAttention] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchAttention()
      .then((body) => {
        if (!cancelled) setAttention(body)
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
  }, [])

  const rows = attention?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        조회수 기준 · 60초 갱신
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row) => (
            <Top5RowTile key={row.code} clickable onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank ?? '-'}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}

// 스켈핑 후보 전체 보기(PLAN.md §5.2) — AttentionFullModal과 동일하게 마운트 시
// 자기 데이터를 불러오는 자기완결 컴포넌트다(카드가 폴링하는 5개보다 넉넉하게
// limit=10 기본값으로 재조회). 근거 배지(회전율·관심 TOP)를 행마다 노출해 왜 이
// 순위인지 바로 보이게 한다 — score 자체는 z-score 가중합이라 절대값에 의미가
// 없으므로(app/quant/screener.py 참고) 근거 배지가 더 중요한 정보다.
function ScalpCandidatesFullModal({ onSelectStock }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchScalpCandidates(10)
      .then((body) => {
        if (!cancelled) setData(body)
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
  }, [])

  const rows = data?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        거래대금 상위 + 실시간 관심순위 조합 스코어 · 참고용 스크리닝 — 매매 신호 아님
        {data?.market_closed && ' · 장 마감(마지막 갱신 유지)'}
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row, i) => (
            <Top5RowTile key={row.code} clickable onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {turnoverBadgeLabel(row.turnover) && <Badge kind="info">{turnoverBadgeLabel(row.turnover)}</Badge>}
                {scalpScoreBadgeLabel(row.score) && <Badge kind="info">{scalpScoreBadgeLabel(row.score)}</Badge>}
                {row.in_attention_top && <Badge kind="live">관심 TOP</Badge>}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}

// 업종·테마 트리맵 박스 클릭 → 대장 종목 TOP10(PLAN.md §5.12). AttentionFullModal/
// ScalpCandidatesFullModal과 동일하게 마운트 시(모달이 열릴 때) 자기 데이터를 불러오는
// 자기완결 컴포넌트다 — groupType/name이 바뀌면(트리맵의 다른 박스를 클릭) 다시
// 불러온다. 기준은 거래대금 내림차순(시가총액 컬럼이 소스에 없음, naver_group.py
// 모듈 docstring 참고)이라 "대장 종목"은 순위 나열이지 매매 추천이 아니다(§5 "중립
// 계기판" 원칙 — 문구에 매수/추천 뉘앙스를 넣지 않는다).
function GroupTopStocksModal({ groupType, name, onSelectStock }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchGroupTopStocks(groupType, name, 10)
      .then((body) => {
        if (!cancelled) setData(body)
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
  }, [groupType, name])

  const rows = data?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        거래대금 상위 10종목 · 참고용 순위 (시가총액 데이터 없음, 매매 추천 아님)
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row, i) => (
            <Top5RowTile key={row.code} clickable={!STATIC_DATA} onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code}
                </span>
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>
                {rateLabel(row.change_rate)} · {eokLabel(row.value)}
              </span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TOP5 카드 행 — clickable=true면 <button>(hover 배경 + 클릭), false면 기존과 동일한
// <div>(정적 텍스트). 모든 랭킹 행 클릭 → 종목 상세 모달 통일(사용자 요구, 이전엔
// 실시간 관심 TOP5만 클릭됐다) 작업에서 4개 TOP5 카드가 공유한다. 정적 배포
// (STATIC_DATA)에서는 호출부가 clickable=false를 넘겨 행을 비활성으로 둔다 —
// fetchStockSeries가 정적 스냅샷을 지원하지 않아(§ 정적 모드 판단, api.js 참고)
// 클릭해봤자 항상 에러만 뜨므로, 차라리 클릭 자체를 막는 쪽이 낫다고 판단했다.
// ---------------------------------------------------------------------------
function Top5RowTile({ clickable, onClick, children }) {
  const Tag = clickable ? 'button' : 'div'
  return (
    <Tag
      type={clickable ? 'button' : undefined}
      className={`top5-row ${clickable ? 'top5-row-clickable' : ''}`}
      onClick={clickable ? onClick : undefined}
    >
      {children}
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// TOP5 요약 행 — 표(FlowRankTable 등)를 그대로 축소하지 않고, "종목명·핵심 숫자·배지"
// 만 남긴 가벼운 목록을 별도로 그린다(사용자 요구: "100개짜리 리스트도 뒤로").
// ---------------------------------------------------------------------------
function Top5Card({ title, hint, rows, onMore, renderRow, emptyText = '표시할 데이터가 없습니다.', hoverDate }) {
  return (
    <div className="top5-card" title={hoverDate}>
      <div className="top5-card-header">
        <span className="top5-card-title">{title}</span>
        <button type="button" className="top5-more" onClick={onMore}>
          전체 보기 ›
        </button>
      </div>
      {hint && <div className="toggle-hint" style={{ marginBottom: 6 }}>{hint}</div>}
      {(!rows || rows.length === 0) && <div className="state" style={{ padding: '16px 0' }}>{emptyText}</div>}
      {rows && rows.length > 0 && <div>{rows.slice(0, 5).map(renderRow)}</div>}
    </div>
  )
}

export default function DashboardPage() {
  // 지수 3종(코스피/코스닥/선물) — 타일 표시용 + 캔들 모달 기본 데이터를 겸한다.
  const [marketData, setMarketData] = useState({})
  const [marketLoading, setMarketLoading] = useState(true)
  // 지수 3종 타일 1D 실시간(GET /api/markets/index-tiles/live, 60초 서버 캐시,
  // 2026-07-21) — { kospi: {close, change_rate, date, time, prev_close, source}|null,
  // kosdaq, futures, market_closed, cached_at }. marketData(3M EOD, 캔들 모달 기본
  // 데이터)는 그대로 유지하고 타일 표시값만 이걸로 오버레이한다(아래 1분 티어 폴링에
  // 편입, 별도 setInterval 신설 금지).
  const [indexTilesLive, setIndexTilesLive] = useState(null)

  const [sentiment, setSentiment] = useState(null)
  const [breadth, setBreadth] = useState(null)

  // 장중 잠정 수급(PLAN.md §6 3.7-3) — null이면 "라이브 없음"(폴링 전 최초 로딩 중이거나
  // 실패)이라 아래 투자자별 수급 요약 타일은 항상 기존 확정치(flowInvestorSummary)로
  // 폴백한다. 정적 배포(STATIC_DATA)에서는 이 state가 항상 null로 남아 기존 동작 그대로다.
  const [flowLive, setFlowLive] = useState(null)

  const [fundSeries, setFundSeries] = useState({})
  // 환율(USD/KRW)·WTI 타일의 최신값/전일비/기준일 계산용 — 모달(MacroModal)은 별도로
  // 자기 기간(기본 1Y)만큼 다시 불러오므로 이 state와 무관하다.
  const [macroSeries, setMacroSeries] = useState({})

  // 외인 양손 · 현선물 섹션(PLAN.md §4.5-5) — 베이시스/만기(basisData), 파생ETF
  // 방향성 게이지(derivativeFlow), 프로그램 차익 순매수(programFlow) 타일용 데이터.
  // 외인 현물/선물 순매수 자체는 새로 fetch하지 않고 위 marketData(선물 포함)와
  // 아래 flowInvestorSummary/flowLiveSummary(현물, 코스피+코스닥)를 그대로 재사용한다.
  const [basisData, setBasisData] = useState(null)
  const [derivativeFlow, setDerivativeFlow] = useState(null)
  const [programFlow, setProgramFlow] = useState({})

  // PLAN.md §4.7 3단 갱신 주기(2026-07-20 장중 실측 편입, §5.5-2로 7분→1분 이동) —
  // 베이시스·외인 선물수급의 1분 라이브 오버레이. null이면 "라이브 없음"(폴링 전/
  // 실패/정적 배포)이라 아래 KPI 타일은 항상 기존 EOD 값(basisData/foreignFuturesRow)
  // 으로 폴백한다 — flowLive와 동일한 "오버레이 + 폴백" 관례. 시그널 판정
  // (foreignSignals)은 EOD 데이터 기준을 그대로 유지한다(라이브 값은 표시만, 판정
  // 기준은 안 바꿈).
  const [basisLive, setBasisLive] = useState(null)
  const [futuresFlowLive, setFuturesFlowLive] = useState(null)
  // 환율(USD/KRW) 1분 라이브 오버레이(PLAN.md §5.5-3, 2026-07-21 실측 편입) — naver
  // front-api의 "오늘" 행이 장중 고시회차 갱신을 그대로 반영함을 실측으로 확인해
  // basisLive/futuresFlowLive와 같은 "오버레이 + 폴백" 관례로 추가했다. null이면
  // macroSeries(EOD, usdkrw)로 폴백한다.
  const [fxLive, setFxLive] = useState(null)

  const [groupType, setGroupType] = useState('upjong')
  const [groupItems, setGroupItems] = useState([])
  const [groupLoading, setGroupLoading] = useState(false)
  const [groupError, setGroupError] = useState(null)
  // 업종/테마 등락률 1분 라이브 오버레이(PLAN.md §4.7, §5.5-2로 7분→1분 이동) —
  // { type, rows: [{name, change_rate}], ... }. groupItems(EOD, value/market_sum
  // 포함)와 이름 기준으로 병합해 트리맵 박스 크기는 유지하면서 색(등락률)만
  // 갱신한다(아래 groupTreemapItems).
  const [groupLive, setGroupLive] = useState(null)

  const [flowRankTop, setFlowRankTop] = useState(null)
  const [valueRankTop, setValueRankTop] = useState(null)
  // 거래대금 상위 7분 라이브(PLAN.md §4.7) — 응답 스키마가 valueRankTop(EOD, market='all')과
  // 동일해({date, rows}) 별도 병합 없이 그대로 대체해 쓴다(아래 렌더 시
  // `!STATIC_DATA && valueRankLive ? valueRankLive : valueRankTop`).
  const [valueRankLive, setValueRankLive] = useState(null)
  const [flowPathTop, setFlowPathTop] = useState(null)
  // 실시간 관심 종목 TOP20(PLAN.md 사용자 지시, live-only) — API 응답 바디를 그대로
  // 담는다({ rows, qry_tp, queried_at }). flowLive와 동일하게 정적 배포에서는 항상
  // null로 남는다.
  const [attentionTop, setAttentionTop] = useState(null)
  // 스켈핑 후보(PLAN.md §5.2) — GET /api/markets/scalp-candidates 응답 바디를 그대로
  // 담는다({ date, market_closed, cached_at, rows }). value-rank/live·attention 두
  // 라이브 캐시를 조합한 참고용 스크리닝이라 정적 배포에서는 항상 null(다른 로컬
  // 전용 기능과 동일한 관례).
  const [scalpCandidates, setScalpCandidates] = useState(null)

  // "지금 유입 우세" 판정(PLAN.md §5.15) — GET /api/markets/regime 응답 바디를
  // 그대로 담는다({ regime, reason, reliable_signal, market_closed, kospi,
  // kosdaq, cached_at }). scalpCandidates 등과 동일하게 로컬 전용 기능이라
  // 정적 배포에서는 항상 null.
  const [regime, setRegime] = useState(null)

  // 종목 랭킹 요약 3개 카드(거래대금 상위/실시간 관심 TOP5/스켈핑 후보)의 시장
  // 필터(PLAN.md §5.15-3) — 'all'|'kospi'|'kosdaq', 기본 전체. 수급 상위/ETF
  // 경유 상위는 시장 구분이 뚜렷하지 않은 소스(수급 상위는 market이 일부만 채워짐,
  // ETF 경유 상위는 애초에 market 필드가 없음)라 필터 대상에서 뺀다. 이름을
  // FlowSummaryModal의 지역 marketFilter(합계/코스피/코스닥, §5.10)와 구분하기
  // 위해 rankingMarketFilter로 부른다 — 서로 다른 컴포넌트 스코프라 충돌은
  // 없지만 같은 파일 안에서 헷갈리지 않도록.
  const [rankingMarketFilter, setRankingMarketFilter] = useState('all')

  const [modal, setModal] = useState(null) // { type, title, ...params } | null

  // 지수 3종 — 타일(최신 종가/등락률) + 캔들 모달 기본 기간(90일) 데이터를 한 번에
  // 담는다. 병렬 요청(Promise.all)이라 시장 하나가 느려도 나머지 타일은 먼저 뜬다.
  useEffect(() => {
    let cancelled = false
    setMarketLoading(true)
    Promise.all(MARKETS.map((m) => fetchMarketSeries(m.key, DEFAULT_CANDLE_DAYS).catch(() => null))).then(
      (results) => {
        if (cancelled) return
        const next = {}
        MARKETS.forEach((m, i) => {
          next[m.key] = results[i] || { prices: [], flows: {} }
        })
        setMarketData(next)
        setMarketLoading(false)
      }
    )
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchSentiment()
      .then((body) => {
        if (!cancelled) setSentiment(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 1분 티어 폴링 통합 — breadth(등락 종목수)·flowLive(장중 잠정 수급)·attentionTop
  // (실시간 관심 TOP20)·scalpCandidates(스켈핑 후보)는 전부 백엔드 60초 캐시라
  // 원래 4개의 독립된 setInterval로 따로 돌았다(폴링 시점이 서로 어긋나 있었을 뿐
  // 실질적 차이는 없었다). 하나의 useEffect/setInterval로 묶어 Promise.all로 한
  // 번에 실행한다 — 백엔드는 엔드포인트별 TTL 캐시를 그대로 쓰므로 서버 부하는
  // 그대로고, 프런트 쪽 타이머 개수만 준다. breadth는 fetchBreadthLive() 내부에서
  // STATIC_DATA를 이미 처리하므로(정적 스냅샷 폴백) 이 효과 전체에는 STATIC_DATA
  // 가드를 두지 않고, 나머지는 개별적으로 가드한다.
  //
  // groupLive(업종/테마 등락률)·basisLive(베이시스)·futuresFlowLive(외인 선물수급)도
  // 2026-07-21(§5.5-2)부터 이 1분 티어에 합류했다 — 원래 value-rank와 같은 7분
  // 티어에 있었지만 실측 결과(PLAN.md §5.5 진단②) 이 셋은 그룹 목록/단일 조회
  // 1회뿐인 가벼운 호출이라 "단순함을 위해" 묶여 있었을 뿐 1분으로 당겨도 백엔드
  // 비용이 늘지 않는다(비싼 건 코스피+코스닥 전 종목 페이지네이션인 value-rank
  // 하나뿐 — 아래 7분 티어에 그대로 남긴다). groupLive는 groupType(업종/테마 탭)에
  // 의존하므로 이 effect의 dependency에도 groupType을 추가했다 — 탭이 바뀌면 나머지
  // (breadth·flowLive 등)도 함께 재요청되지만 전부 자기 완결적인 서버 TTL 캐시라
  // 무해하다(기존 7분 티어가 groupType 의존일 때와 동일한 트레이드오프).
  useEffect(() => {
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

    async function loadBreadth() {
      try {
        const body = await fetchBreadthLive()
        if (cancelled) return
        setBreadth({ kospi: toCamel(body.kospi), kosdaq: toCamel(body.kosdaq) })
        return
      } catch {
        // 폴백으로 진행
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
        setBreadth({ kospi: toCamel(last(kospiBody)), kosdaq: toCamel(last(kosdaqBody)) })
      } catch {
        // 배지는 데이터 없음 상태로 자연히 표시된다.
      }
    }

    function loadFlowLive() {
      return fetchFlowLive()
        .then((body) => {
          if (!cancelled) setFlowLive(body)
        })
        .catch(() => {
          if (!cancelled) setFlowLive(null)
        })
    }

    function loadAttentionTop() {
      return fetchAttention()
        .then((body) => {
          if (!cancelled) setAttentionTop(body)
        })
        .catch(() => {
          if (!cancelled) setAttentionTop(null)
        })
    }

    function loadScalpCandidates() {
      // PLAN.md §5.15-3 시장 필터 — Top5Card는 어차피 rows.slice(0,5)만 렌더하지만,
      // 필터가 코스피/코스닥 한쪽으로 좁혀지면 상위 5개 중 그 시장 종목이 5개 미만일
      // 수 있다. limit을 20으로 넉넉히 받아와 필터 후에도 5개를 채울 여유를 둔다
      // (기존 5 -> 20, 백엔드는 이미 캐시된 value-rank/live·attention 조합을
      // 스코어링만 다시 하는 거라 비용 증가 없음, 최대 50까지 허용됨).
      return fetchScalpCandidates(20)
        .then((body) => {
          if (!cancelled) setScalpCandidates(body)
        })
        .catch(() => {
          if (!cancelled) setScalpCandidates(null)
        })
    }

    // "지금 유입 우세" 판정(PLAN.md §5.15) — 백엔드가 이미 60초 캐시라 이 1분
    // 티어에 그대로 합류한다(별도 setInterval 신설 금지).
    function loadRegime() {
      return fetchRegime()
        .then((body) => {
          if (!cancelled) setRegime(body)
        })
        .catch(() => {
          if (!cancelled) setRegime(null)
        })
    }

    // 지수 3종 타일 1D 실시간(PLAN.md 작업 지시, 2026-07-21) — 기존 1분 티어에
    // 편입(별도 setInterval 신설 금지). 실패 시 null로 두면 렌더 쪽이 자동으로
    // marketData(3M EOD)의 마지막 봉으로 폴백한다(아래 렌더부 indexTileOf 참고).
    function loadIndexTilesLive() {
      return fetchIndexTilesLive()
        .then((body) => {
          if (!cancelled) setIndexTilesLive(body)
        })
        .catch(() => {
          if (!cancelled) setIndexTilesLive(null)
        })
    }

    // §5.5-2로 7분 티어에서 이 1분 티어로 옮겨온 3개(가벼운 소스만 — value-rank는
    // 7분 티어에 그대로 남음, 근거는 위 주석 참고).
    function loadGroupLive() {
      return fetchGroupsLive(groupType)
        .then((body) => {
          if (!cancelled) setGroupLive(body)
        })
        .catch(() => {
          if (!cancelled) setGroupLive(null)
        })
    }

    function loadBasisLive() {
      return fetchBasisLive()
        .then((body) => {
          if (!cancelled) setBasisLive(body)
        })
        .catch(() => {
          if (!cancelled) setBasisLive(null)
        })
    }

    function loadFuturesFlowLive() {
      return fetchFuturesFlowLive()
        .then((body) => {
          if (!cancelled) setFuturesFlowLive(body)
        })
        .catch(() => {
          if (!cancelled) setFuturesFlowLive(null)
        })
    }

    // §5.5-3 — 환율도 위 groupLive/basisLive/futuresFlowLive와 같은 1분 티어에
    // 합류한다(백엔드 fx/live 60초 캐시와 맞춤).
    function loadFxLive() {
      return fetchFxLive()
        .then((body) => {
          if (!cancelled) setFxLive(body)
        })
        .catch(() => {
          if (!cancelled) setFxLive(null)
        })
    }

    function load() {
      const tasks = [loadBreadth()]
      if (!STATIC_DATA) {
        tasks.push(
          loadFlowLive(),
          loadAttentionTop(),
          loadScalpCandidates(),
          loadIndexTilesLive(),
          loadGroupLive(),
          loadBasisLive(),
          loadFuturesFlowLive(),
          loadFxLive(),
          loadRegime()
        )
      }
      return Promise.all(tasks)
    }

    load()
    const intervalId = setInterval(load, BREADTH_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [groupType])

  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(MARKET_FUND_IDS, 10)
      .then((body) => {
        if (!cancelled) setFundSeries(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(
      [...MACRO_SERIES.map((s) => s.id), ...US_INDEX_SERIES.map((s) => s.id)],
      MACRO_TILE_DAYS
    )
      .then((body) => {
        if (!cancelled) setMacroSeries(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 베이시스 + 다음 만기(PLAN.md §4.5-3/-5) — 짧은 창(타일용)이면 충분하지만, 최근
  // 며칠치가 있어야 "직전 값과 비교" 없이도(베이시스는 전일비 화살표를 두지 않는다)
  // 최소한 latest가 안정적으로 채워진다.
  useEffect(() => {
    let cancelled = false
    fetchBasis(FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setBasisData(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 개인 방향성(파생ETF, PLAN.md §4.5-1) 타일 최신값 — 상세는 DerivativeEtfModal이
  // 별도로(90일) 다시 불러온다.
  useEffect(() => {
    let cancelled = false
    fetchDerivativeFlow(FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setDerivativeFlow(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 프로그램매매 차익 순매수(PLAN.md §4.5-4) — 코스피+코스닥 두 시리즈를 신용융자
  // 타일(creditLoanSum)과 동일하게 합산해서 보여준다.
  useEffect(() => {
    let cancelled = false
    fetchMacroSeries(PROGRAM_ARB_IDS, FOREIGN_POSITION_TILE_DAYS)
      .then((body) => {
        if (!cancelled) setProgramFlow(body.series || {})
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setGroupLoading(true)
    setGroupError(null)
    fetchGroups(groupType)
      .then((items) => {
        if (!cancelled) setGroupItems(items || [])
      })
      .catch((e) => {
        if (!cancelled) setGroupError(e.message)
      })
      .finally(() => {
        if (!cancelled) setGroupLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [groupType])

  useEffect(() => {
    let cancelled = false
    fetchFlowRank('foreign', 'buy', FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setFlowRankTop((body.dates && body.dates[0]) || null)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchValueRank('all', FLOW_RANK_LOOKBACK_DAYS)
      .then((body) => {
        if (!cancelled) setValueRankTop(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchFlowPath(FLOW_RANK_LOOKBACK_DAYS, 5, 'in')
      .then((body) => {
        if (!cancelled) setFlowPathTop(body)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 7분 티어(PLAN.md §4.7, 2026-07-20 장중 실측 편입) — value-rank(거래대금 상위)만
  // 남았다. 2026-07-21(§5.5-2)에 groupLive/basisLive/futuresFlowLive를 위 1분 티어로
  // 옮겼다 — 실측 결과(§4.7-1) 4개 소스 모두 값이 장중에 갱신되는 건 맞지만, 그중
  // 코스피+코스닥 전 종목(~4,300종목) 페이지네이션이 필요한 value-rank만 진짜
  // 비싸고(사이클당 ~44요청·13초+) groups/basis/futures-flow는 목록·단일 조회 1회뿐이라
  // "단순함을 위해" 같은 티어에 묶여 있었을 뿐이었다(진단 근거는 PLAN.md §5.5
  // 진단②). 실패 시 조용히 null로 두고 카드/타일이 EOD 폴백을 자연히 보여주게 둔다.
  useEffect(() => {
    if (STATIC_DATA) return undefined
    let cancelled = false

    function loadValueRankLive() {
      return fetchValueRankLive()
        .then((body) => {
          if (!cancelled) setValueRankLive(body)
        })
        .catch(() => {
          if (!cancelled) setValueRankLive(null)
        })
    }

    function load() {
      return loadValueRankLive()
    }

    load()
    const intervalId = setInterval(load, EXTRA_LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [])

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
  const indexTileOf = (key) => {
    const row = indexTilesLiveOpen ? indexTilesLive?.[key] : null
    if (row) {
      return { close: row.close, changeRate: row.change_rate, date: row.date, time: row.time, isLive: true }
    }
    const fallback = latestPriceOf(key)
    return fallback ? { ...fallback, isLive: false } : null
  }

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

  // "코스피/코스닥 쏠림" KPI 타일(PLAN.md §5.18) — 이미 1분 티어에서 폴링 중인
  // flowLive를 computeConcentration으로 즉석 계산한다(breadthTotals와 동일한
  // 관례, 새 API 호출 없음).
  const concentrationLive = computeConcentration(flowLive)

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

  // 외인 양손 · 현선물 섹션(PLAN.md §4.5-5) — 현물은 위에서 이미 계산한
  // flowInvestorSummary/flowLiveSummary의 '외국인' 값을 그대로 재사용하고(중복 fetch
  // 없음), 선물은 marketData.futures(지수 3종 fetch가 이미 flows까지 받아온다)의
  // '외국인' 마지막 행을 쓴다.
  const foreignSpotLiveValue = flowLiveSummary?.['외국인']
  const foreignSpotIsLive = foreignSpotLiveValue !== undefined
  const foreignSpotRow = flowInvestorSummary?.['외국인']
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

  const basisLatest = basisData?.latest
  // 베이시스 1분 라이브 오버레이(PLAN.md §4.7, §5.5-2로 7분→1분 이동) — KPI 타일
  // 표시 전용(시그널 판정 backwardationSignal은 EOD 기준 basisLatest 그대로).
  const basisLiveActive = Boolean(
    !STATIC_DATA && basisLive && basisLive.market_closed === false && typeof basisLive.basis === 'number'
  )
  // 환율(USD/KRW) 1분 라이브 오버레이(PLAN.md §5.5-3) — basisLiveActive와 동일한
  // 관례. 값 표시만 라이브로 바꾸고, DiffArrow의 기준(prev)은 여전히 macroPrev
  // (전일 종가)를 쓴다 — "전일 대비"라는 의미 자체는 유지한 채 "현재"만 갱신한다.
  const fxLiveActive = Boolean(
    !STATIC_DATA && fxLive && fxLive.market_closed === false && typeof fxLive.usdkrw?.value === 'number'
  )
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

  // 시그널 배지(PLAN.md §4.5-5, 중립 표현 — "함정" 단정 금지) — ① 외인 현물·선물 방향
  // 대치, ② 만기 D-3 이내. 값이 없거나(0 포함) 한쪽이 없으면 판단하지 않는다(오검
  // 방지 — Math.sign(0)===0이라 자연히 걸러진다).
  // 2026-07-23(§5.15 후속): 백워데이션 배지는 제거했다 — 3년치 실측 결과 다음날
  // 하락확률이 콘탱고와 거의 차이 없어(43.8% vs 43.0%, PLAN.md §5.15) 예측력이
  // 없다는 게 실증됐다. 베이시스 값 자체(콘탱고/역전 텍스트)는 위 KpiTile에 그대로
  // 남아 있다 — 이 배지 행에서만 뺀다.
  const foreignSpotSign = foreignSpotValue === null || foreignSpotValue === undefined ? 0 : Math.sign(foreignSpotValue)
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
    ...MARKETS.map((m) => (indexTilesLiveOpen ? indexTilesLive?.[m.key]?.date : null)),
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
    programArbDate
  )

  // 업종/테마 트리맵 — groupItems(EOD, value/market_sum 포함)에 groupLive(1분 라이브,
  // §5.5-2로 7분→1분 이동, change_rate만)를 이름 기준으로 병합한다(PLAN.md §4.7).
  // 박스 크기(value)는 EOD 그대로 유지하고 색(change_rate)만 장중에 갱신되는 셈 —
  // GroupTreemap.jsx는 이 병합 여부를 모르는 순수 컴포넌트라 그대로 재사용한다.
  // groupLive가 없거나(정적 배포/폴링 전/실패) 해당 그룹 타입과 다르면 groupItems를
  // 그대로 쓴다.
  const groupLiveActive = Boolean(!STATIC_DATA && groupLive && groupLive.type === groupType && groupLive.market_closed === false)
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
  const effectiveValueRank = valueRankLiveActive ? valueRankLive : valueRankTop

  return (
    <div className="dashboard-page">
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

      {/* 장 상태 배너(PLAN.md §5.9) — 정규장 중엔 렌더하지 않는다(가장 흔한 상태라
          배너로 화면을 어지럽히지 않는다). 정적 배포(STATIC_DATA)는 라이브 폴링이
          없어 판정 근거가 없으므로 아예 숨긴다(다른 라이브 전용 UI와 동일한 관례). */}
      {!STATIC_DATA && marketStatus === 'nxt-only' && (
        <div className="banner">
          NXT 확장세션 — 실시간 관심 TOP5 등 개별 종목만 갱신 중이며, 지수·수급·업종·테마
          등은 정규장(09:00~15:30) 확정치입니다.
        </div>
      )}
      {!STATIC_DATA && marketStatus === 'closed' && (
        <div className="banner">장 마감 — 모든 지표가 최근 확정치입니다.</div>
      )}

      {/* 0.5 지금 유입 우세(PLAN.md §5.15, 2026-07-23) — 코스닥·외국인 연속
          순매수/매도일수 검증 결과(3년치 index_ohlcv/market_flow 백테스트)만
          근거로 "지금 어느 시장이 유리한지" 판정한다. 문구는 항상 관찰+확률
          서술이다(§5 전체 원칙) — "사라"/"지금이 기회" 같은 명령형·추천형 문구는
          이 카드에서도 절대 쓰지 않는다. 코스피 등 신뢰도 낮은 조합은 숨기지 않고
          흐리게("참고용 · 신호 약함") 구분해 그대로 노출한다(정직성 원칙). 정적
          배포는 라이브 폴링이 없어 대상이 아니다(다른 로컬 전용 카드와 동일). */}
      {!STATIC_DATA && regime && (
        <>
          <div className="section-title">지금 유입 우세</div>
          <div className={`regime-card regime-card-${regime.regime === '코스닥우세' ? 'kosdaq' : 'neutral'}`}>
            <div className="regime-card-top">
              <span className="regime-verdict">{regime.regime}</span>
              {regime.regime === '코스닥우세' && (
                <button
                  type="button"
                  className="toggle-chip"
                  onClick={() => setRankingMarketFilter('kosdaq')}
                  title="아래 종목 랭킹 요약을 코스닥으로 필터"
                >
                  코스닥만 보기 ›
                </button>
              )}
            </div>
            <div className="regime-reason">{regime.reason}</div>
            <div className="regime-combo-grid">
              {['kosdaq', 'kospi'].map((m) =>
                ['외국인', '기관계'].map((inv) => {
                  const combo = regime[m]?.[inv]
                  if (!combo) return null
                  const streakLabel =
                    combo.streak > 0
                      ? `${combo.streak}일 연속 매수`
                      : combo.streak < 0
                        ? `${Math.abs(combo.streak)}일 연속 매도`
                        : '연속 없음'
                  return (
                    <div
                      key={`${m}-${inv}`}
                      className={`regime-combo ${combo.reliable ? 'regime-combo-reliable' : 'regime-combo-weak'}`}
                    >
                      <span className="regime-combo-label">
                        {m === 'kospi' ? '코스피' : '코스닥'} · {inv}
                      </span>
                      <span className="regime-combo-streak">{streakLabel}</span>
                      {combo.bucket_stats ? (
                        <span className="regime-combo-stats">
                          다음날 상승확률 {combo.bucket_stats.positive_rate_pct}% (표본 {combo.bucket_stats.n}일)
                        </span>
                      ) : (
                        <span className="regime-combo-stats">이 구간 과거 표본 없음</span>
                      )}
                      {!combo.reliable && <span className="regime-combo-hint">참고용 · 신호 약함</span>}
                      {/* 2026-07-23 수정 — 사용자 지적: "오늘 수급이 좋아 보이는데
                          왜 중립이냐". 확정 스트릭은 하루치 잠정 데이터로 방향을
                          안 뒤집는(보수적 처리) 게 맞지만, 오늘 실제로 반대 방향
                          움직임이 있다는 사실 자체는 숨기지 않는다. */}
                      {combo.live_reversal && (
                        <span className="regime-combo-hint regime-combo-reversal">
                          오늘 장중은 현재 {combo.today_live_net_value > 0 ? '매수' : '매도'} 전환 조짐
                        </span>
                      )}
                      {/* PLAN.md §5.17 — 수급 가속도(실시간 반응성 지표). 스트릭(위
                          streakLabel/bucket_stats, 느리지만 검증된 신호)과는 별도
                          필드로 항상 따로 보여준다 — 종합 판정에 섞지 않는다. 부호만
                          일관되게 해석 가능한 관찰 서술이고("가속"/"감속"), "좋다/
                          나쁘다" 판단 문구는 쓰지 않는다(§5 원칙). */}
                      {combo.acceleration ? (
                        <span className="regime-combo-hint regime-combo-reversal">
                          최근 30분 순매수 속도 {signedEokLabel(combo.acceleration.recent_velocity)}
                          (직전 30분 대비{' '}
                          {combo.acceleration.acceleration > 0
                            ? '가속'
                            : combo.acceleration.acceleration < 0
                              ? '감속'
                              : '속도 유지'}
                          )
                        </span>
                      ) : (
                        <span className="regime-combo-hint">가속도 데이터 부족(적립 중)</span>
                      )}
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </>
      )}

      {/* 1. 지수 3종 */}
      <div className="section-title" style={{ marginTop: 16 }}>
        지수
      </div>
      <div className="kpi-grid">
        {MARKETS.map((m) => {
          const latest = indexTileOf(m.key)
          const liveTime =
            latest?.isLive && latest.time && latest.time.length === 4
              ? `${latest.time.slice(0, 2)}:${latest.time.slice(2)}`
              : null
          return (
            <KpiTile
              key={m.key}
              label={m.label}
              value={latest ? numFmt.format(latest.close) : marketLoading ? '…' : '-'}
              valueClass={latest ? rateClass(latest.changeRate) : ''}
              sub={
                latest && (
                  <span className={`kpi-tile-sub ${rateClass(latest.changeRate)}`}>
                    {rateLabel(latest.changeRate)}
                    {latest.isLive ? (
                      <>
                        {' · '}
                        <Badge kind="live">장중</Badge>
                      </>
                    ) : (
                      <StaleDate date={latest.date} baseDate={baseDate} prefix=" · " />
                    )}
                  </span>
                )
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

      {/* 2. 투자자별 수급 요약 — 장중에는 라이브 잠정치(PLAN.md §6 3.7-3) + "장중 잠정"
          배지, 그 외(장마감 후·라이브 실패·정적 배포)에는 기존 확정치 + "확정" 라벨로
          자동 전환된다(flowLiveActive, 위 계산부 참고). 라이브 배지가 날짜 역할을
          겸하므로 라이브 타일에는 StaleDate를 붙이지 않는다(작업 지시). */}
      <div className="section-title">투자자별 수급 요약</div>
      <div className="kpi-grid">
        {DEFAULT_INVESTORS.map((name) => {
          const liveValue = flowLiveSummary?.[name]
          const isLive = liveValue !== undefined
          const row = flowInvestorSummary?.[name]
          const value = isLive ? liveValue : row?.net_value
          const hasValue = value !== undefined && value !== null
          return (
            <KpiTile
              key={name}
              label={
                <>
                  <span className="dot" style={{ background: INVESTOR_COLOR_VAR[name] }} /> {name}
                </>
              }
              value={hasValue ? eokLabel(value) : marketLoading ? '…' : '-'}
              valueClass={hasValue ? (value >= 0 ? 'up' : 'down') : ''}
              sub={
                isLive ? (
                  <span className="kpi-tile-sub">
                    <Badge kind="live" />
                  </span>
                ) : (
                  row?.date && (
                    <span className="kpi-tile-sub">
                      확정
                      <StaleDate date={row.date} baseDate={baseDate} prefix=" · " />
                    </span>
                  )
                )
              }
              title={!isLive && row?.date ? formatDate(row.date) : undefined}
              onClick={() => setModal({ type: 'flowSummary', title: '투자자별 수급 (코스피+코스닥)' })}
            />
          )
        })}
      </div>

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
      <div className="kpi-grid">
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
          title="코스피+코스닥 합계"
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
        <KpiTile
          label="외인 선물 (K200)"
          value={
            futuresFlowLiveNetValue !== null
              ? eokLabel(futuresFlowLiveNetValue)
              : foreignFuturesRow
                ? eokLabel(foreignFuturesRow.net_value)
                : marketLoading
                  ? '…'
                  : '-'
          }
          valueClass={
            futuresFlowLiveNetValue !== null
              ? futuresFlowLiveNetValue >= 0
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
            ) : (
              foreignFuturesRow?.date && (
                <span className="kpi-tile-sub">
                  확정
                  <StaleDate date={foreignFuturesRow.date} baseDate={baseDate} prefix=" · " />
                </span>
              )
            )
          }
          title={foreignFuturesRow?.date ? formatDate(foreignFuturesRow.date) : undefined}
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
          value={basisLabel(basisLiveActive ? basisLive.basis : basisLatest?.basis)}
          valueClass={
            basisLiveActive
              ? basisLive.basis >= 0
                ? 'up'
                : 'down'
              : basisLatest?.basis === undefined || basisLatest?.basis === null
                ? ''
                : basisLatest.basis >= 0
                  ? 'up'
                  : 'down'
          }
          sub={
            <span className="kpi-tile-sub">
              {(basisLiveActive ? basisLive.backwardation : basisLatest?.backwardation) === undefined ||
              (basisLiveActive ? basisLive.backwardation : basisLatest?.backwardation) === null ? (
                '-'
              ) : (basisLiveActive ? basisLive.backwardation : basisLatest?.backwardation) ? (
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
              {basisLiveActive ? ' · 1분 갱신' : <StaleDate date={basisLatest?.date} baseDate={baseDate} prefix=" · " />}
            </span>
          }
          title={basisLatest?.date ? formatDate(basisLatest.date) : undefined}
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
          label="다음 만기"
          value={expiry?.date ? `${mmdd(expiry.date)} · D-${expiry.d_day}` : '…'}
          sub={expiry?.quadruple && <Badge kind="warn">네 마녀의 날</Badge>}
          title={expiry?.date ? formatDate(expiry.date) : undefined}
          onClick={() => setModal({ type: 'foreignPosition', title: '외인 현물 vs 선물 · 베이시스' })}
        />
      </div>

      {/* 3. 시황 · 자금 */}
      <div className="section-title">시황 · 자금</div>
      <div className="kpi-grid">
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
          value={fxLabel(fxLiveActive ? fxLive.usdkrw.value : macroLatest('usdkrw'))}
          sub={
            <>
              <DiffArrow
                current={fxLiveActive ? fxLive.usdkrw.value : macroLatest('usdkrw')}
                prev={macroPrev('usdkrw')}
                formatter={(v) => `${fxFmt.format(v)}원${pctSuffix(v, macroPrev('usdkrw'))}`}
                neutral
              />
              {fxLiveActive ? (
                <span className="kpi-tile-sub"> · 1분 갱신 · 장중</span>
              ) : (
                <StaleDate date={macroDate('usdkrw')} baseDate={baseDate} prefix=" · " />
              )}
            </>
          }
          title={macroDate('usdkrw') ? formatDate(macroDate('usdkrw')) : undefined}
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
          {groupLiveActive ? '박스 크기 = 일별 거래대금 · 색(등락률) 1분 갱신 · 장중' : '일별 스냅샷 · 색 = 등락률'}
        </span>
      </div>
      {groupLoading && <div className="state">불러오는 중…</div>}
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
            <span title="소스가 최근 확정된 거래일까지만 제공 — 당일 장마감 전에는 전날 데이터가 최신입니다.">
              외국인 순매수
              <StaleDate date={flowRankTop?.date} baseDate={baseDate} prefix=" · 확정 " />
            </span>
          }
          hoverDate={flowRankTop?.date ? formatDate(flowRankTop.date) : undefined}
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
              <span className="top5-row-value up">{eokLabel(row.net_value)}</span>
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
        {/* 실시간 관심 종목 TOP20(조회수 기준, live-only) — flowLive와 동일하게 정적
            배포에서는 attentionTop이 항상 null이라 카드가 "표시할 데이터가 없습니다"로
            자연히 빈 상태를 보여준다. 다른 3개 카드와 달리 행 자체가 클릭 가능해
            바로 종목 상세 모달로 이어진다(StockSearch onSelect와 동일한 모달 타입). */}
        <Top5Card
          title="실시간 관심 TOP5"
          hint="조회수 기준 · 60초 갱신"
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
                  배지는 정보 밀도가 낮은 보조 정보라 "전체 보기" 모달(위 ScalpFullModal,
                  가로 폭이 훨씬 넓음)에서만 유지하고, 압축 카드에서는 다른 3개 랭킹
                  카드와 동일하게 배지 최대 2개(시장·관심TOP)만 남긴다. */}
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.in_attention_top && <Badge kind="live">관심 TOP</Badge>}
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
        {modal?.type === 'concentration' && <ConcentrationModal />}
        {modal?.type === 'fund' && <FundModal />}
        {modal?.type === 'macro' && <MacroModal />}
        {modal?.type === 'flowSummary' && <FlowSummaryModal />}
        {modal?.type === 'foreignPosition' && <ForeignPositionModal />}
        {modal?.type === 'derivativeEtf' && <DerivativeEtfModal />}
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
