import { useState } from 'react'

// 내러티브/제안 공용 컴팩트 아코디언 행 — 헤더(한 줄) 클릭 시 본문(children)이 펼쳐진다.
// 헤더는 <button> + aria-expanded 로 키보드 접근 가능. 기본 접힘.
export default function NarrativeRow({ className = '', title, titleBadge, meta, badge, children }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`narr-item ${className}${open ? ' narr-open' : ''}`}>
      <button type="button" className="narr-row-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        {titleBadge}
        <span className="narr-row-title" title={title}>{title}</span>
        {meta}
        {badge}
        <span className="narr-chevron" aria-hidden="true">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="narr-body">{children}</div>}
    </div>
  )
}
