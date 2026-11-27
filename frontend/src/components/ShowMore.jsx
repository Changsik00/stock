import { useEffect, useState } from 'react'

// 순위 표 "상위 N개 + 더 보기" 공용(Phase 5.131). 처음엔 limit개만 보여 주고 나머지는 버튼으로 펼친다.
// resetKey(필터·탭 값)가 바뀌면 다시 접는다 — 다른 목록으로 바꿨는데 펼친 상태가 남아 길어지지 않게.
export function useShowMore(rows, limit = 10, resetKey) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    setExpanded(false)
  }, [resetKey])
  const list = rows || []
  const hidden = Math.max(0, list.length - limit)
  return {
    shown: expanded ? list : list.slice(0, limit),
    hidden,
    expanded,
    toggle: () => setExpanded((v) => !v),
  }
}

// 표 카드 아래 저강도 버튼(.btn-ghost — DESIGN.md '더보기 등 저강도 동작'). 숨긴 행이 없으면 렌더하지 않는다.
export function ShowMoreButton({ hidden, expanded, onToggle }) {
  if (!hidden) return null
  return (
    <div className="show-more-row">
      <button type="button" className="btn btn-ghost btn-sm" aria-expanded={expanded} onClick={onToggle}>
        {expanded ? '접기' : `${hidden}개 더 보기`}
      </button>
    </div>
  )
}
