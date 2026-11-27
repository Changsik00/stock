import { useState } from 'react'
import { STATIC_DATA } from '../api'
import { useFreshness } from '../queries'

// 데이터 신선도 경고(PLAN.md Phase 5.120 C2) — /api/health/freshness가 문제(stale/early)를
// 알린 때만 주황 한 줄을 보여준다. 문제가 없거나 조회 실패/정적 배포면 아무것도 렌더하지 않는다.
// 5분 폴링은 useFreshness(react-query)가 맡는다. 갱신 실패 시 직전 값 유지(기존 동작).
const STATUS_LABEL = { stale: '지연', early: '확정 전 수집' }

export default function FreshnessBanner() {
  const [open, setOpen] = useState(false)
  const problems = (useFreshness().data?.items || []).filter((i) => i.status !== 'ok')

  if (STATIC_DATA || problems.length === 0) return null

  const headline = problems
    .map((p) => `${p.name}(${p.status === 'early' ? '확정 전 수집' : '최신 아님'})`)
    .join(', ')

  return (
    <div className="banner freshness-banner" role="status">
      <div className="freshness-banner-main">
        <span className="freshness-banner-text">데이터 지연: {headline}</span>
        <button
          type="button"
          className="btn btn-link btn-sm"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '접기' : '자세히'}
        </button>
      </div>
      {open && (
        <ul className="freshness-banner-list">
          {problems.map((p) => (
            <li key={p.key}>
              <strong>{p.name}</strong> — {STATUS_LABEL[p.status] || p.status}: 기대 {p.expected_date}
              {' · '}실제 {p.actual_date || '-'}
              {p.detail ? ` (${p.detail})` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
