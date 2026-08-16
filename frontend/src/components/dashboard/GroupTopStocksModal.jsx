import { useEffect, useState } from 'react'
import { STATIC_DATA, fetchGroupTopStocks } from '../../api'
import StockHeatmap from '../StockHeatmap'

// 업종·테마 트리맵 박스 클릭 → 대장 종목 TOP10(PLAN.md §5.12). AttentionFullModal/
// ScalpCandidatesFullModal과 동일하게 마운트 시(모달이 열릴 때) 자기 데이터를 불러오는
// 자기완결 컴포넌트다 — groupType/name이 바뀌면(트리맵의 다른 박스를 클릭) 다시
// 불러온다. 기준은 거래대금 내림차순(시가총액 컬럼이 소스에 없음, naver_group.py
// 모듈 docstring 참고)이라 "대장 종목"은 순위 나열이지 매매 추천이 아니다(§5 "중립
// 계기판" 원칙 — 문구에 매수/추천 뉘앙스를 넣지 않는다).
// 렌더는 StockHeatmap(균일 크기 격자, 색=등락률)을 쓴다 — 사용자 피드백 "트리맵이
// 아니라 히트맵으로"(266개 테마 전체를 이 모달 하나로 커버하는 드릴인이라, 어제
// 만들었던 8개 고정 트리맵 그리드는 제거하고 이 드릴인으로 통합했다). onSelectStock은
// 기존 호출부가 row 객체 전체를 기대하므로, StockHeatmap의 onCellClick(code, name)에서
// rows.find로 원래 row를 되찾아 넘긴다.
export default function GroupTopStocksModal({ groupType, name, onSelectStock }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchGroupTopStocks(groupType, name, 10)
      .then((body) => {
        if (!cancelled) setData(body)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [groupType, name])

  const rows = data?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        거래대금 상위 10종목 · 참고용 순위 (시가총액 데이터 없음, 매매 추천 아님)
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (
        <StockHeatmap
          items={rows}
          onCellClick={STATIC_DATA ? undefined : (code) => {
            const row = rows.find((r) => r.code === code)
            if (row) onSelectStock(row)
          }}
        />
      )}
    </div>
  )
}
