import { useState } from 'react'
import { Skel } from './MetricTile'

// 내러티브 행 컴팩트 아코디언 행 — 헤더(한 줄) 클릭 시 본문(children)이 펼쳐진다.
// 헤더는 <button> + aria-expanded 로 키보드 접근 가능. 기본 접힘(defaultOpen으로 펼침 가능).
export default function NarrativeRow({ className = '', title, titleBadge, meta, badge, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={`narr-item ${className}${open ? ' narr-open' : ''}`}>
      <button type="button" className="narr-row-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="narr-row-main">
          <span className="narr-row-title" title={title}>{title}</span>
          <span className="narr-row-sub">
            {titleBadge}
            {meta}
          </span>
        </span>
        {badge}
        {/* ▲▼는 가격 방향 전용이라 펼침 표시는 CSS 쉐브론으로 그린다 */}
        <span className="narr-chevron" aria-hidden="true" />
      </button>
      {open && <div className="narr-body">{children}</div>}
    </div>
  )
}

// 목록 로딩 스켈레톤 — 실제 행과 같은 높이로 자리를 잡아 로드 후 레이아웃이 튀지 않게 한다.
export function NarrSkeleton({ rows = 2 }) {
  return (
    <div className="narr-list" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="narr-item narr-skel-row">
          <Skel w={i % 2 ? '70%' : '85%'} h={16} />
          <Skel w={140} h={12} />
        </div>
      ))}
    </div>
  )
}
