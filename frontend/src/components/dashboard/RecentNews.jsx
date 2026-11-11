import { useEffect, useState } from 'react'
import { getNarrativeNews } from '../../api'
import { fmtTime, isHttpUrl } from './narrativeShared'

// 전체 수집 뉴스 — 이슈로 묶이지 않은 기사까지 포함한 평면 목록. 맨 아래 접이식(기본 접힘).
export default function RecentNews() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!open) return
    getNarrativeNews(200).then((d) => setItems(d.items)).catch((e) => setErr(e.message))
  }, [open])
  return (
    <div className="narr-news">
      <button type="button" className="btn btn-link btn-sm narr-link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? '전체 수집 뉴스(묶이지 않은 기사 포함) 접기' : '전체 수집 뉴스(묶이지 않은 기사 포함) 보기'}
      </button>
      {open && !items && !err && <div className="narr-meta">불러오는 중…</div>}
      {open && err && <div className="state">뉴스 조회 실패: {err}</div>}
      {open && items && items.length === 0 && <div className="narr-meta">수집된 뉴스가 없습니다. ‘뉴스 수집’을 눌러 주세요.</div>}
      {open && items && (
        <ul className="narr-news-list">
          {items.map((n) => (
            <li key={n.id}>
              <span className="narr-meta">{fmtTime(n.published_at || n.fetched_at)} · {n.source}</span>{' '}
              {isHttpUrl(n.url) ? <a href={n.url} target="_blank" rel="noopener noreferrer">{n.title}</a> : n.title}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
