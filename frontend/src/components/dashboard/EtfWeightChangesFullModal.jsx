import { useEffect, useState } from 'react'
import { fetchEtfWeightChanges } from '../../api'
import Badge from '../Badge'
import { formatDate } from '../../format'

// ETF 비중 변화 상위 — 전체 보기 (PLAN.md §5.25). FlowPathFullModal과 동일한
// 자기완결 컴포넌트 패턴(마운트 시 자기 데이터를 불러온다)이지만, 유입/유출 같은
// 토글은 없다(4가지 이벤트가 이미 한 목록에 섞여 나온다 — event 파라미터로 필터를
// 걸 수도 있지만 "감별"이라는 원래 요청 취지상 기본은 전부 보여주는 쪽을 택했다).
// code를 넘기지 않아 시장 전체 스크리닝 결과를 받는다(카드 자체가 "market-wide").
export default function EtfWeightChangesFullModal({ onRowClick }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchEtfWeightChanges({ limit: 50 })
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
  }, [])

  const rows = data?.changes || []

  return (
    <div>
      {data?.curr_date && (
        <div className="toggle-hint">
          {formatDate(data.prev_date)} → {formatDate(data.curr_date)} 스냅샷 비교 · etf_holdings는 각 ETF의 top10
          구성만 매일 스냅샷이라 "신규편입"/"편출"이 top10 안팎 이동일 수 있습니다 — 실제 매수/매도가 시작됐다는
          뜻은 아닙니다.
        </div>
      )}

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}

      {!loading && !error && rows.length > 0 && (
        <div className="flow-rank-card">
          <div className="table-scroll">
            <table className="flow-rank-table">
              <thead>
                <tr>
                  <th>이벤트</th>
                  <th>종목명</th>
                  <th>ETF</th>
                  <th className="num">비중 변화</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={`${i}-${row.etf_code}-${row.code}`}
                    className={onRowClick ? 'flow-rank-row-clickable' : undefined}
                    onClick={onRowClick ? () => onRowClick(row.code, row.name) : undefined}
                  >
                    <td>
                      <span
                        className={`etf-weight-event ${row.event === '비중확대' || row.event === '신규편입' ? 'up' : 'down'}`}
                      >
                        {row.event}
                      </span>
                    </td>
                    <td>
                      <span className="flow-rank-name">{row.name || row.code}</span>
                    </td>
                    <td>
                      {row.etf_name || row.etf_code}
                      {row.is_active && <Badge kind="info">액티브</Badge>}
                    </td>
                    <td className={`num ${row.delta == null ? '' : row.delta >= 0 ? 'up' : 'down'}`}>
                      {row.prev_weight != null ? `${row.prev_weight.toFixed(2)}%` : '–'}
                      {' → '}
                      {row.curr_weight != null ? `${row.curr_weight.toFixed(2)}%` : '–'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
