// 앱 전체에서 공유하는 상수. 투자자 카테고리 색은 페이지(시장/향후 종목)를 넘나들며
// 항상 같은 투자자 = 같은 색이 되도록 여기 한 곳에서만 정의한다 (dataviz 스킬:
// "Color follows the entity" — categorical 8-hue 고정 순서, index.css의 --investor-1..8).
//
// 순서: 개인/외국인/기관계(기본 3분류) + 금융투자/보험/투신/사모/연기금(세부 토글 5개) = 8개.
// 은행/기타금융/기타법인/기타외국인은 정성적으로 덜 주목되는 소분류라 8-hue 예산 밖에 두고
// (dataviz 스킬: "9번째 시리즈는 생성된 hue를 쓰지 않는다") 이 화면에서는 노출하지 않는다 —
// 필요해지면 market_flow에 이미 적재돼 있으므로 새 slot 없이 옵션만 추가하면 된다.
export const DEFAULT_INVESTORS = ['개인', '외국인', '기관계']
export const EXTRA_INVESTORS = ['금융투자', '보험', '투신', '사모', '연기금']
export const ALL_INVESTORS = [...DEFAULT_INVESTORS, ...EXTRA_INVESTORS]

export const INVESTOR_COLOR_VAR = Object.fromEntries(
  ALL_INVESTORS.map((name, i) => [name, `var(--investor-${i + 1})`])
)

export const PERIOD_OPTIONS = [
  { key: 30, label: '1M' },
  { key: 90, label: '3M' },
  { key: 180, label: '6M' },
  { key: 365, label: '1Y' },
  { key: 1095, label: '3Y' },
]

// 분봉 토글(PLAN.md §5.1) — 'daily'는 기존 일봉(PeriodPicker), 나머지는 정수 분(interval
// 쿼리파라미터, 백엔드 clients/kiwoom.py MINUTE_CHART_INTERVALS 중 실사용 옵션만 노출).
// 키움 실측으로는 45분도 됐지만 요구된 노출 옵션은 이 6개뿐이라 그대로 좁힌다.
export const INTRADAY_OPTIONS = [
  { key: 'daily', label: '일봉' },
  { key: 1, label: '1분' },
  { key: 3, label: '3분' },
  { key: 5, label: '5분' },
  { key: 10, label: '10분' },
  { key: 60, label: '60분' },
]

export const MARKETS = [
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
  { key: 'futures', label: '선물 (코스피200)' },
]

export const MACRO_SERIES = [
  { id: 'usdkrw', label: '원/달러 환율 (USD/KRW)', unit: '원' },
  { id: 'wti', label: 'WTI 유가', unit: '달러' },
  { id: 'brent', label: '브렌트유', unit: '달러' },
]

// 시장 자금·대차 보조 차트 (PLAN.md §3.5/§6 1.5-4) — kofia freesis 적재, macro_series
// 재사용. 값은 전부 백만원 단위로 DB에 있어 화면에서는 조원으로 환산(§3.5: 1조원 =
// 1,000,000백만원)해 표시한다. 신용융자는 코스피/코스닥 두 라인을 한 차트에 겹친다.
export const MARKET_FUND_SERIES = [
  { id: 'investor_deposit', label: '투자자예탁금', color: 'var(--series-price)' },
  { id: 'lending_balance', label: '대차잔고', color: 'var(--series-price)' },
]

export const CREDIT_LOAN_SERIES = [
  { id: 'credit_loan_kospi', label: '코스피', color: 'var(--series-price)' },
  { id: 'credit_loan_kosdaq', label: '코스닥', color: 'var(--series-value)' },
]

export const MARKET_FUND_IDS = [
  ...MARKET_FUND_SERIES.map((s) => s.id),
  ...CREDIT_LOAN_SERIES.map((s) => s.id),
]
