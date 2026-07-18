import { useEffect, useRef, useState } from 'react'
import { fetchStockSearch } from '../api'
import Badge from './Badge'

// 종목 검색 자동완성 (PLAN.md §6 3.7-2) — 대시보드 상단 검색창. 입력 250ms 디바운스
// 후 /api/stocks/search를 호출해 드롭다운으로 후보를 보여주고, 선택 시 onSelect로
// 종목({code, name, market, is_etf})을 넘긴다. 자체적으로 열림/닫힘·하이라이트 상태를
// 관리하는 "제어되지 않는(uncontrolled)" 컴포넌트라 호출부는 onSelect 콜백 하나만
// 신경 쓰면 된다 — 선택 즉시 입력값·드롭다운을 초기화해 다음 검색을 바로 받을 수
// 있게 한다.
export default function StockSearch({ onSelect }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [highlight, setHighlight] = useState(-1)
  const wrapRef = useRef(null)

  // 디바운스 검색 — 타이핑이 250ms 멈춘 뒤에만 요청한다. 빈 문자열(trim 후)은 백엔드가
  // min_length=1로 거부하므로 아예 요청을 보내지 않고 드롭다운을 닫는다.
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setResults([])
      setOpen(false)
      setLoading(false)
      return undefined
    }

    let cancelled = false
    setLoading(true)
    const timer = setTimeout(() => {
      fetchStockSearch(trimmed)
        .then((rows) => {
          if (cancelled) return
          setResults(rows || [])
          setOpen(true)
          setHighlight(-1)
        })
        .catch(() => {
          if (!cancelled) {
            setResults([])
            setOpen(true)
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 250)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  // 바깥 클릭 시 드롭다운만 닫는다 — 입력한 텍스트는 그대로 남긴다(작업 지시).
  useEffect(() => {
    function onMouseDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [])

  function select(stock) {
    onSelect?.(stock)
    setQuery('')
    setResults([])
    setOpen(false)
    setHighlight(-1)
  }

  function onKeyDown(e) {
    if (!open || results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      select(results[highlight >= 0 ? highlight : 0])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="stock-search" ref={wrapRef}>
      <input
        type="text"
        className="dashboard-search"
        placeholder="종목 검색 (종목명 또는 코드)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => {
          if (results.length > 0) setOpen(true)
        }}
        onKeyDown={onKeyDown}
        aria-label="종목 검색"
      />
      {open && (
        <div className="stock-search-dropdown">
          {loading && <div className="stock-search-empty">검색 중…</div>}
          {!loading && results.length === 0 && <div className="stock-search-empty">검색 결과 없음</div>}
          {!loading &&
            results.map((row, i) => (
              <button
                type="button"
                key={row.code}
                className={`stock-search-row ${i === highlight ? 'highlighted' : ''}`}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => select(row)}
              >
                <span className="stock-search-row-name">{row.name}</span>
                <span className="stock-search-row-code">{row.code}</span>
                <Badge kind={row.market?.toLowerCase()} />
                {row.is_etf && <Badge kind="etf" />}
              </button>
            ))}
        </div>
      )}
    </div>
  )
}
