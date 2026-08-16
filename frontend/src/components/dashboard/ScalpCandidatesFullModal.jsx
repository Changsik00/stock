import { useEffect, useState } from 'react'
import { fetchScalpCandidates } from '../../api'
import Badge from '../Badge'
import {
  Top5RowTile,
  atRiskBadgeLabel,
  flowBadgeLabel,
  rateClass,
  rateLabel,
  scalpScoreBadgeLabel,
  turnoverBadgeLabel,
} from './shared'

// 스켈핑 후보 전체 보기(PLAN.md §5.2) — AttentionFullModal과 동일하게 마운트 시
// 자기 데이터를 불러오는 자기완결 컴포넌트다(카드가 폴링하는 5개보다 넉넉하게
// limit=10 기본값으로 재조회). 근거 배지(회전율·관심 TOP)를 행마다 노출해 왜 이
// 순위인지 바로 보이게 한다 — score 자체는 z-score 가중합이라 절대값에 의미가
// 없으므로(app/quant/screener.py 참고) 근거 배지가 더 중요한 정보다.
export default function ScalpCandidatesFullModal({ onSelectStock }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchScalpCandidates(10)
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

  const rows = data?.rows || []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        거래대금 상위 + 실시간 관심순위 조합 스코어 · 참고용 스크리닝 — 매매 신호 아님
        {data?.market_closed && ' · 장 마감(마지막 갱신 유지)'}
      </div>
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && rows.length === 0 && <div className="state">표시할 데이터가 없습니다.</div>}
      {!loading && !error && rows.length > 0 && (
        <div>
          {rows.map((row, i) => (
            <Top5RowTile key={row.code} clickable onClick={() => onSelectStock(row)}>
              <span className="top5-row-name">
                <span className="top5-row-label">
                  {i + 1}. {row.name || row.code}
                </span>
                {row.market && <Badge kind={row.market} />}
                {turnoverBadgeLabel(row.turnover) && <Badge kind="info">{turnoverBadgeLabel(row.turnover)}</Badge>}
                {scalpScoreBadgeLabel(row.score) && <Badge kind="info">{scalpScoreBadgeLabel(row.score)}</Badge>}
                {flowBadgeLabel(row.flow_net_value) && <Badge kind="info">{flowBadgeLabel(row.flow_net_value)}</Badge>}
                {atRiskBadgeLabel(row.at_risk) && <Badge kind="info">{atRiskBadgeLabel(row.at_risk)}</Badge>}
                {row.in_attention_top && <Badge kind="live">관심 TOP</Badge>}
              </span>
              <span className={`top5-row-value ${rateClass(row.change_rate)}`}>{rateLabel(row.change_rate)}</span>
            </Top5RowTile>
          ))}
        </div>
      )}
    </div>
  )
}
