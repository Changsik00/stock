import { useEffect, useState } from 'react'
import { fetchAttention } from '../../api'
import Badge from '../Badge'
import { Top5RowTile, rateClass, rateLabel } from './shared'

// 실시간 관심 종목 TOP20 전체 보기 — ValueRankFullModal/FlowPathFullModal과 동일하게
// 마운트 시(모달이 열릴 때) 자기 데이터를 불러오는 자기완결 컴포넌트다. 다른
// FullModal들과 달리 행이 클릭 가능해야 하므로(종목 상세 모달로 전환) 호출부
// (DashboardPage)가 onSelectStock 콜백을 넘겨준다 — 이 컴포넌트 자체는 setModal을
// 모르므로 이 방식으로만 상위 상태를 바꿀 수 있다. 20행짜리 단순 목록이라 별도
// 테이블 컴포넌트 파일(ValueRankTable처럼)로 뽑지 않고 여기 인라인으로 둔다.
export default function AttentionFullModal({ onSelectStock }) {
  const [attention, setAttention] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchAttention()
      .then((body) => {
        if (!cancelled) setAttention(body)
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
  }, [])

  const rows = attention?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        조회수 기준 · 60초 갱신
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row) => (
            <Top5RowTile key={row.code} clickable onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {row.rank ?? '-'}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {row.is_etf && <Badge kind="etf" />}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}
