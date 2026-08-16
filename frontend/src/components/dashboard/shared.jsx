import { formatDate } from '../../format'

export const numFmt = new Intl.NumberFormat('ko-KR')

export const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

export const joFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

export const scoreFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

export const countFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })

// 환율(원/달러 1자리)·유가(달러 2자리) 타일 포맷 — 매크로 탭 통합(환율/WTI 타일 +
// 모달, 사용자 원문: "환율·유가 2~3개 차트만으로 탭 하나는 과하다").
export const fxFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

export const oilFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

// 전일 미국장 4대 지수(PLAN.md §5.8) 타일 포맷 — pt 단위, 소수 1자리(WTI의 달러 2자리
// 관례와 달리 지수 자체가 큰 값이라 1자리면 충분).
export const usIndexFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

// 베이시스(K200 선물-현물, pt) 타일 포맷 — PLAN.md §4.5-3/-5.
export const basisFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

// KPI 타일 초기 캔들 모달 기본 기간 — MarketPage와 동일하게 90일(3M)에서 시작한다.
export const DEFAULT_CANDLE_DAYS = 90

// 투자자별 수급 요약·외인 양손 상세 모달의 3M(EOD)/1D(오늘 장중 누적) 토글
// (PLAN.md §5.4-4) — StockDetailModal.jsx의 INTRADAY_OPTIONS 토글과 같은
// toggle-row/toggle-chip 패턴을 재사용한다. 기본값은 '1D'다(2026-07-21 요구사항
// 갱신 — 모달을 열면 오늘 장중 누적을 먼저 보여주고, 3M은 사용자가 명시적으로
// 전환해야 보인다).
export const CHART_MODE_OPTIONS = [
  { key: '1D', label: '1D' },
  { key: '3M', label: '3M' },
]

// BreadthModal("등락 종목수") 전용 토글(PLAN.md §5.13) — 이 모달은 3M(일별 히스토리)
// 차트가 없고 원래부터 "현재"(breadth/live 60초 스냅샷 배지)만 보여줬다. 여기에 순간
// 스냅샷만으로는 놓치는 시간 흐름을 볼 수 있는 "1D 추이"(상승비율 라인차트) 탭을
// 추가한다 — CHART_MODE_OPTIONS(1D/3M)를 그대로 재사용하면 존재하지 않는 3M 탭처럼
// 보이므로 이 모달 전용 옵션을 별도로 둔다(toggle-row/toggle-chip 패턴은 동일하게
// 재사용).
export const BREADTH_MODE_OPTIONS = [
  { key: 'live', label: '현재' },
  { key: '1D', label: '1D 추이' },
]

// 1D 탭 기간 선택(PLAN.md §5.14, 2026-07-22) — intraday-accumulated가 순수
// 메모리 버퍼에서 DB(intraday_sample) 영속화로 바뀌면서 재배포에도 데이터가
// 남고 과거 조회도 가능해졌다. FlowSummaryModal/ForeignPositionModal/
// BreadthModal의 1D 탭이 모두 공유하는 토글 — 최근 7일은 60초 원본, 8일 전부터는
// 15분 압축본이 섞여 나온다(collectors/intraday_compaction.py 배치). 기본값은
// 지금까지의 동작과 동일한 1일.
export const INTRADAY_DAYS_OPTIONS = [
  { key: 1, label: '1일' },
  { key: 7, label: '7일' },
  { key: 30, label: '30일' },
]

export const VALUE_RANK_MARKET_OPTIONS = [
  { key: 'all', label: '전체' },
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]

export const FLOW_RANK_LOOKBACK_DAYS = 7

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
export const BREADTH_LIVE_POLL_MS = 60_000

export function eokLabel(million) {
  if (typeof million !== 'number') return '-'
  return `${eokFmt.format(million / 100)}억원`
}

// PLAN.md §5.17 — 가속도 카드 문구용(부호 있는 억원 표기, "+12.3억원"/"-4.5억원").
// eokLabel은 부호를 안 붙이므로(음수는 Intl 기본 마이너스만) 별도로 분리한다.
export function signedEokLabel(million) {
  if (typeof million !== 'number') return '-'
  const sign = million > 0 ? '+' : ''
  return `${sign}${eokFmt.format(million / 100)}억원`
}

export function trillion(million) {
  if (million === null || million === undefined) return null
  return million / 1e6
}

export function trillionLabel(million) {
  const t = trillion(million)
  return t === null ? '-' : `${joFmt.format(t)}조`
}

export function fxLabel(value) {
  if (typeof value !== 'number') return '-'
  return `${fxFmt.format(value)}원`
}

export function oilLabel(value) {
  if (typeof value !== 'number') return '-'
  return `$${oilFmt.format(value)}`
}

export function usIndexLabel(value) {
  if (typeof value !== 'number') return '-'
  return usIndexFmt.format(value)
}

export function basisLabel(value) {
  if (typeof value !== 'number') return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${basisFmt.format(value)}pt`
}

export function scoreClass(score) {
  if (score === null || score === undefined) return ''
  if (score > 2) return 'up'
  if (score < -2) return 'down'
  return 'flat'
}

export function scoreLabel(score) {
  if (score === null || score === undefined) return '-'
  const sign = score > 0 ? '+' : ''
  return `${sign}${scoreFmt.format(score)}%`
}

export function rateClass(rate) {
  if (rate === null || rate === undefined) return ''
  return rate > 0 ? 'up' : rate < 0 ? 'down' : ''
}

export function rateLabel(rate) {
  if (rate === null || rate === undefined) return '-'
  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

// 스켈핑 후보 카드 전용(PLAN.md §5.2) — turnover(회전율 %)는 근거 배지 문구로만
// 쓴다("회전율 8.2%"). score는 z-score 가중합이라(app/quant/screener.py 참고)
// sentiment 게이지의 scoreClass(-100~100 기준)를 재사용하면 임계값이 전혀 안
// 맞으므로 별도로 부호만 붙인 중립 표기를 쓴다 — 매매 방향을 암시하는 색(up/down)은
// 의도적으로 넣지 않는다(참고용 스크리닝, 매매 신호 아님 원칙).
export function turnoverBadgeLabel(turnover) {
  if (turnover === null || turnover === undefined) return null
  return `회전율 ${turnover.toFixed(1)}%`
}

export function scalpScoreLabel(score) {
  if (score === null || score === undefined) return '-'
  const sign = score > 0 ? '+' : ''
  return `${sign}${score.toFixed(2)}`
}

// 시장 전체 거래량 급증 감지(PLAN.md §5.24) — index-tiles/live의 `volume_surge`
// (compute_volume_surge, app/quant/volume_surge.py)를 그대로 문구로 옮긴다.
// null/부재면 null을 반환해 호출부가 아예 줄을 렌더하지 않게 한다(turnover/
// flow_net_value 등 부재 시 생략하는 이 파일의 기존 관례와 동일 — 0/placeholder로
// 채우지 않는다). §5 원칙대로 "많다/적다" 관찰 서술만, 매매 판단 문구는 넣지 않는다.
export function volumeSurgeLabel(volumeSurge) {
  if (!volumeSurge || typeof volumeSurge.multiple !== 'number') return null
  return `거래량 평소(직전 ${volumeSurge.baseline_minutes}분) 대비 ${volumeSurge.multiple.toFixed(1)}배`
}

export function scalpScoreBadgeLabel(score) {
  if (score === null || score === undefined) return null
  return `스코어 ${scalpScoreLabel(score)}`
}

// 스켈핑 후보 스크리너 적중률 사후 검증(PLAN.md §5.40) — GET
// /api/markets/scalp-candidates/hitrate(quant/scalp_hitrate.py) 응답을 카드 하단의
// 작은 안내 문구 한 줄로 옮긴다. **표본수·기간을 퍼센트 옆에 항상 함께 노출하고
// "검증됨"이 아니라 "관찰됨"으로만 서술한다**(§5.40 명시 요구, 비타협 — 표본이
// 며칠~열흘 안팎이고 이 기간이 서킷브레이커 수준의 극심한 하락장이라 평상시를
// 대표하지 않는다). 15분 호라이즌을 대표값으로 고정해 쓴다 — 5개 호라이즌을 전부
// 한 줄에 욱여넣으면 오히려 안 읽히고, 스켈핑(초단기)과 당일 마감(EOD)의 중간
// 지점이라 어느 한쪽에 치우치지 않는 대표성이 가장 낫다는 판단(구현 시 취향 —
// 다른 호라이즌으로 바꾸고 싶으면 이 함수만 고치면 된다). 표본이 아직 없거나
// 15분 호라이즌 표본이 0이면 null(카드에 아예 줄을 그리지 않는다 — 다른 배지/
// 라벨의 "부재 시 생략" 관례와 동일).
export function scalpHitrateFootnote(hitrate) {
  if (!hitrate || !hitrate.total_picks) return null
  const h = hitrate.horizons?.['15m']
  if (!h || !h.n) return null
  const sign = h.avg_change_rate > 0 ? '+' : ''
  return (
    `최근 ${hitrate.distinct_days}거래일(${hitrate.date_from}~${hitrate.date_to}, ` +
    `15분 표본 ${h.n}건) 관찰: 15분 후 상승 확률 ${h.win_rate}%, 평균 변동 ` +
    `${sign}${h.avg_change_rate.toFixed(1)}%(중앙값 ${h.median_change_rate > 0 ? '+' : ''}${h.median_change_rate.toFixed(1)}%) ` +
    `— 표본이 작고 이례적 하락장 기간이라 일반화할 수 없음, 검증된 성과 아님`
  )
}

// PLAN.md §5.20-3 — 종목별 당일 외국인+기관 순매수(flow_net_value) 배지. 단위는
// stock_flow.net_value 그대로(백만원 관례, 위 eokLabel/signedEokLabel과 동일 — 종목
// 상세 모달의 수급 타일과 같은 컨벤션, StockDetailModal.jsx "net_value/cum_net_value는
// market_flow와 동일하게 백만원 단위로 내려온다" 주석 참고). turnoverBadgeLabel/
// scalpScoreBadgeLabel과 동일하게 Badge kind="info"(중립색)만 쓴다 — 수급 방향이
// 부호에 이미 드러나 있지만("+"/"-"), 그걸 배지 색(up/down)으로까지 강조하면
// "사라"는 신호로 오해될 수 있다(§5 "관찰 사실만 서술" 원칙, scalpScoreBadgeLabel
// 주석의 동일한 판단 그대로 계승). 아직 `_run_stock_flow_scan`(10분 티어) 스윕이
// 그 종목까지 못 돈 경우 flow_net_value가 null이므로 배지 자체를 생략한다(turnover가
// 없을 때 배지를 생략하는 것과 동일한 관례 — "0"이나 자리표시자를 억지로 보여주지 않는다).
export function flowBadgeLabel(flowNetValue) {
  if (flowNetValue === null || flowNetValue === undefined) return null
  return `수급 ${signedEokLabel(flowNetValue)}`
}

// PLAN.md §5.27-2(2026-07-24, 사용자 지적) — 큰 폭 하락(change_rate <= -15.0)
// 종목 플래그 배지. 가격제한폭 근접 종목은 §5.27-1에서 후보 목록 자체에서
// 구조적으로 제외되므로 이 배지가 보이는 종목은 항상 "제한폭까지는 아니지만
// 하락폭이 큰" 경우다. turnoverBadgeLabel/flowBadgeLabel과 동일하게
// Badge kind="info"(중립색)만 쓴다 — "매도/회피" 같은 매매 판단 문구나 별도
// 경고색은 넣지 않는다(§5 "관찰 사실만 서술" 원칙, 이 파일의 다른 배지들과
// 동일한 판단).
export function atRiskBadgeLabel(atRisk) {
  return atRisk ? '하락폭 과다' : null
}

// MM-DD만 뽑는다 (StaleDate/TOP5 "…기준" 라벨 공용) — formatDate가 이미
// 'YYYY-MM-DD'로 정규화하므로 뒤 5글자만 자르면 된다.
export function mmdd(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? d.slice(5) : d
}

// 여러 후보 날짜 중 최신값 — 대표 기준일(대시보드 상단 1회 표시) 계산용.
// 소스마다 'YYYYMMDD'/'YYYY-MM-DD'가 섞여 올 수 있어 formatDate로 정규화한 뒤
// 문자열 비교한다(정규화된 'YYYY-MM-DD'는 사전순 비교 = 날짜순 비교).
export function latestOf(...rawDates) {
  const normalized = rawDates.map((d) => formatDate(d)).filter((d) => typeof d === 'string' && d.length === 10)
  if (normalized.length === 0) return null
  return normalized.reduce((a, b) => (b > a ? b : a))
}

// 대표 기준일(baseDate)보다 뒤처진 타일에만 붙는 작은 회색 날짜(MM-DD) — "뒤처짐" 신호.
// 사용자 피드백: "타일마다 날짜가 있는데 중복이다. 어차피 마지막 거래일일 텐데" —
// 실제로는 소스별 시차가 있어 다를 수 있으므로, 최신인 타일은 아무것도 표시하지
// 않고(대표 기준일과 같다고 간주) 뒤처진 타일에만 예외적으로 이 배지를 남긴다.
// 정확한 날짜는 항상 타일의 title 속성(hover)으로 확인 가능하다.
export function StaleDate({ date, baseDate, prefix = '' }) {
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
export function staleHintLabel(date, baseDate, suffix) {
  const d = formatDate(date)
  if (typeof d !== 'string' || d.length !== 10 || !baseDate || d >= baseDate) return null
  return suffix ? `${mmdd(date)} 기준 · ${suffix}` : `${mmdd(date)} 기준`
}

// 전일比 화살표 — prev가 없으면(첫 값) 표시하지 않는다.
// neutral=true면 up/down 색상 클래스를 붙이지 않는다(중립 회색) — 환율 타일 전용.
// 환율 상승이 "좋은 것"이 아니라 주가 등락(빨강=상승/파랑=하락) 관례와 혼동될 수
// 있어, 화살표 방향·값은 그대로 두고 색만 뺀다(다른 타일은 예탁금 타일과 동일하게
// up/down 색을 그대로 쓴다).
export function DiffArrow({ current, prev, formatter, neutral = false }) {
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
export function pctSuffix(diffAbs, prevValue) {
  if (!prevValue) return ''
  return ` (${((diffAbs / Math.abs(prevValue)) * 100).toFixed(2)}%)`
}

// 두 시장(코스피/코스닥)의 flows(투자자 -> [{date, net_value, net_volume}])를 투자자·
// 날짜 기준으로 합산한다 — market_flow는 시장별로만 적재되고 백엔드에 "합계" 엔드포인트가
// 없어(routers/markets.py MARKETS={kospi,kosdaq,futures}, FLOW_MARKETS={kospi,kosdaq})
// "시장 종합 수급"을 보여주려면 클라이언트에서 더해야 한다.
export function mergeFlows(flowsA, flowsB) {
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
export function mergeIntradayByTime(seriesA, seriesB) {
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

// 코스피/코스닥 "쏠림" 비율(PLAN.md §5.18, 시총 편향 보정은 §5.19) — flow/live
// 응답(fetchFlowLive의 { kospi: {investors}, kosdaq: {investors} } 모양)에서
// 코스피·코스닥 각각의 "활동량"(|외국인 순매수|+|기관계 순매수|, 방향 무관
// 절댓값)을 계산한 뒤, 절대금액을 그대로 비교하지 않고 각 시장 자신의 "평소
// 활동량"(baseline — GET /api/markets/regime의 kospi/kosdaq.activity_baseline.
// avg_daily_activity, 최근 20거래일 평균) 대비 "오늘 몇 배인지"로 먼저 정규화한
// 뒤 그 배율끼리 비교한다. 절대금액 비교였던 §5.18 최초 버전은 코스피 시장
// 규모가 구조적으로 훨씬 커서 평범한 날에도 90~100%가 나와 변별력이 없었다
// (사용자 지적 — "시총 차이가 너무 생겨서 코스피로만 발생할거야").
//
// 백엔드 collectors/intraday_snapshot.py의 get_market_concentration_series와
// 동일한 지표 정의 — 그쪽은 DB에 적립된 1D 시계열을 계산하고, 이 헬퍼는 KPI
// 타일/모달의 "현재" 탭이 이미 폴링 중인 flow/live 스냅샷 + regime 스냅샷(둘 다
// 새 API 호출 없음) 하나로 즉석 계산한다(breadthTotals와 동일한 "이미 fetch한
// 값을 프런트에서 합산" 관례). baseline이 없거나(아직 못 불러옴) 두 시장 중
// 하나라도 평소 활동량이 0 이하면 정규화할 기준이 없어 null(활동량 분모가
// 0이어도 null — §5.18과 동일한 "억지로 채우지 않는다" 원칙).
export function computeConcentration(flowLive, baseline) {
  if (!flowLive) return null
  const activity = (market) => {
    const investors = flowLive[market]?.investors
    const foreign = investors?.['외국인']?.net_value
    const inst = investors?.['기관계']?.net_value
    return Math.abs(foreign ?? 0) + Math.abs(inst ?? 0)
  }
  const kospiActivity = activity('kospi')
  const kosdaqActivity = activity('kosdaq')
  if (!baseline || !(baseline.kospi > 0) || !(baseline.kosdaq > 0)) return null
  const kospiMultiple = kospiActivity / baseline.kospi
  const kosdaqMultiple = kosdaqActivity / baseline.kosdaq
  const denom = kospiMultiple + kosdaqMultiple
  if (denom <= 0) return null
  const kospiShare = (kospiMultiple / denom) * 100
  return {
    kospiShare,
    kosdaqShare: 100 - kospiShare,
    moreActive: kospiShare >= 50 ? '코스피' : '코스닥',
    kospiMultiple,
    kosdaqMultiple,
  }
}

// flows(투자자 -> [{date, net_value, net_volume}])에서 특정 투자자의 가장 최근 행을
// 뽑는다 — market_flow 계열 응답을 다루는 여러 곳(외인 현물/선물 타일)에서 공용으로 쓴다.
export function latestFlowRow(flows, investor) {
  const rows = flows?.[investor]
  return rows && rows.length > 0 ? rows[rows.length - 1] : null
}

// 종목 랭킹 요약 카드(거래대금 상위/실시간 관심 TOP5/스켈핑 후보)의 시장 필터
// (PLAN.md §5.15-3) — rows 각 행에 이미 있는 market 필드('kospi'|'kosdaq'|null)로
// 걸러낸다. 'all'이면 그대로, 필터가 시장 하나로 좁혀지면 market이 없는(null)
// 행은 그 시장 소속인지 알 수 없으니 제외한다.
export function filterRowsByMarket(rows, marketFilter) {
  if (!rows) return rows
  if (marketFilter === 'all') return rows
  return rows.filter((r) => r.market === marketFilter)
}

// 차트 X축 라벨 — 'YYYY-MM-DD' -> 'MM/DD' (MacroChart.jsx/MarketFundChart.jsx의
// dateLabel과 동일한 관례, 여기서는 formatDate로 먼저 정규화해 'YYYYMMDD' 등도 대응).
export function chartDateLabel(date) {
  const d = formatDate(date)
  return typeof d === 'string' && d.length === 10 ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : d
}

// ---------------------------------------------------------------------------
// KPI 타일 — 클릭 가능한 순수 버튼. 값 자체 계산/포맷은 호출부가 마친 뒤 넘긴다.
// ---------------------------------------------------------------------------
export function KpiTile({ label, value, valueClass, sub, onClick, title }) {
  return (
    <button type="button" className="kpi-tile" onClick={onClick} title={title}>
      <span className="kpi-tile-label">{label}</span>
      <span className={`kpi-tile-value ${valueClass || ''}`}>{value}</span>
      {sub}
    </button>
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
export function Top5RowTile({ clickable, onClick, children }) {
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

// 업종 자금 흐름 관찰 카드(PLAN.md §5.33-3) — GET /api/markets/{market}/
// sector-rotation 응답을 유입 상위/유출 상위 2열 + 보조 통계 한 줄로 보여준다.
// quant/sector_rotation.py가 "로테이션 있다/없다"를 판정하지 않는 것과 동일하게
// 이 컴포넌트도 "배율"/"유입·유출 합계" 숫자만 그대로 노출할 뿐 매매 판단이나
// 판정 문구를 붙이지 않는다(예: "매수"/"매도"/"로테이션 있음" 금지, house rule).
export function SectorRotationCard({ data }) {
  if (!data) {
    return <div className="state">불러오는 중…</div>
  }
  if (!data.date) {
    // 표본 부족(sector_flow 이력이 짧음) — 에러가 아니라 정직한 "아직 안 됨" 상태.
    return <div className="state">{data.reason || '데이터가 없습니다.'}</div>
  }

  const { gainers = [], losers = [], aggregate, date, baseline_days_used: baselineDaysUsed } = data

  return (
    <div>
      <div className="top5-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="top5-card">
          <div className="top5-card-header">
            <span className="top5-card-title">유입 상위</span>
          </div>
          {gainers.length === 0 && (
            <div className="state" style={{ padding: '16px 0' }}>평소보다 뚜렷하게 유입된 업종 없음</div>
          )}
          {gainers.map((r) => (
            <Top5RowTile key={r.sector_code}>
              <span className="top5-row-name">{r.sector_name}</span>
              <span className="top5-row-value up">평소 대비 {scoreFmt.format(r.multiple)}배</span>
            </Top5RowTile>
          ))}
        </div>
        <div className="top5-card">
          <div className="top5-card-header">
            <span className="top5-card-title">유출 상위</span>
          </div>
          {losers.length === 0 && (
            <div className="state" style={{ padding: '16px 0' }}>평소보다 뚜렷하게 유출된 업종 없음</div>
          )}
          {losers.map((r) => (
            <Top5RowTile key={r.sector_code}>
              <span className="top5-row-name">{r.sector_name}</span>
              <span className="top5-row-value down">평소 대비 {scoreFmt.format(r.multiple)}배</span>
            </Top5RowTile>
          ))}
        </div>
      </div>
      {aggregate && (
        <div className="toggle-hint" style={{ marginTop: 8 }}>
          {formatDate(date)} 확정 · 베이스라인 {baselineDaysUsed}거래일 · 유입 합계{' '}
          {eokLabel(aggregate.gaining_sum)} · 유출 합계 {eokLabel(aggregate.losing_sum)} · 업종 전체 합계{' '}
          {eokLabel(aggregate.today_net_value)}(평소 {eokLabel(aggregate.baseline_signed_avg)})
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TOP5 요약 행 — 표(FlowRankTable 등)를 그대로 축소하지 않고, "종목명·핵심 숫자·배지"
// 만 남긴 가벼운 목록을 별도로 그린다(사용자 요구: "100개짜리 리스트도 뒤로").
// ---------------------------------------------------------------------------
export function Top5Card({
  title,
  hint,
  footnote,
  rows,
  onMore,
  renderRow,
  emptyText = '표시할 데이터가 없습니다.',
  hoverDate,
}) {
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
      {/* PLAN.md §5.40 — 스켈핑 후보 적중률 사후 검증 문구. hint(상단, 스크리닝
          자체의 성격 설명)와 분리해 하단에 둔다 — 관찰 통계는 스크리닝 설명과
          섹션이 달라 시각적으로도 구분하는 게 자연스럽다. hint와 동일하게 작은
          muted 텍스트(toggle-hint)만 쓴다 — 강조 배지가 아니라 투명성/맥락
          제공이 목적이므로 튀지 않게 유지한다. */}
      {footnote && <div className="toggle-hint" style={{ marginTop: 6 }}>{footnote}</div>}
    </div>
  )
}
