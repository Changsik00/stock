// 공용 배지(칩) 컴포넌트 — 코스피/코스닥/ETF/선물 등 종목·상품 분류를 나타내는 pill.
// 색상은 index.css의 --badge-{kind}-bg/fg 변수로만 관리한다(라이트/다크 대응, 이
// 컴포넌트 자체에는 색을 하드코딩하지 않는다). 등락 표현(상승=빨강 --up, 하락=파랑
// --down)과 혼동되지 않도록 up/down 팔레트와 겹치지 않는 색만 배정했다
// (index.css --badge-kospi-*/--badge-kosdaq-*/--badge-etf-*/--badge-futures-*/
// --badge-live-* 주석 참고).
//
// FlowRankTable/ValueRankTable의 market·ETF 배지가 이 컴포넌트를 쓴다. FlowPathTable의
// "기여 ETF" 칩 목록(flow-path-etf-badge)은 분류 배지가 아니라 종목별 기여 ETF
// 이름+방향점을 나열하는 별개 UI라 대상에서 제외한다(FlowPathTable.jsx 주석 참고).
// live(장중 잠정, PLAN.md §6 3.7-3)는 분류 배지가 아니라 상태 배지지만 같은 pill
// 스타일을 재사용한다 — DashboardPage.jsx의 투자자별 수급 요약 타일 참고.
// investor-caution/warning/risk(PLAN.md §5.39) — KRX 투자주의/경고/위험종목
// 지정 배지. 분류 배지(코스피/코스닥/ETF)와 달리 "지금 이 순간의 공식 상태"를
// 나타내는 심각도 배지라, 색도 index.css의 --risk-info/warn/danger-*(§5.36
// 위험 배너와 동일한 심각도 색 언어)를 재사용한다 — 새 팔레트를 만들지 않고
// "주의<경고<위험" escalation을 기존 info<warn<danger 톤에 그대로 맞춘다.
const LABEL = {
  kospi: '코스피',
  kosdaq: '코스닥',
  etf: 'ETF',
  futures: '선물',
  live: '장중 잠정',
  'investor-caution': '투자주의종목',
  'investor-warning': '투자경고종목',
  'investor-risk': '투자위험종목',
}

export default function Badge({ kind, children, title }) {
  if (!kind) return null
  return (
    <span className={`badge badge-${kind}`} title={title}>
      {children ?? LABEL[kind] ?? kind}
    </span>
  )
}
