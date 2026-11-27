// 시장 탭 로딩 스켈레톤(Phase 5.132) — 로딩 중에도 실제 섹션과 비슷한 높이를 차지해 데이터 도착 때
// 아래 내용이 밀리지 않게 한다(CLS 방지). 높이는 CSS 클래스(.sk-*, index.css)가 정하고 모바일/데스크톱별로 다르다.
// 색·움직임은 공용 .mt-skel(펄스, prefers-reduced-motion에서 정지)을 그대로 쓴다.

// 단일 블록. variant = 'chart' | 'flow' | 'breadth' | 'gauge' | 'treemap' | 'fund' | 'regime'
export function SkeletonBlock({ variant = 'chart', label = '불러오는 중' }) {
  return <div className={`mt-skel sk-block sk-${variant}`} role="status" aria-busy="true" aria-label={label} />
}

// 표 로딩 — 상위 10행 높이(헤더 + 10행)를 차지한다.
export function SkeletonRows({ rows = 10, label = '불러오는 중' }) {
  return (
    <div className="flow-rank-card sk-rows" role="status" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="mt-skel sk-row" aria-hidden="true" />
      ))}
    </div>
  )
}
