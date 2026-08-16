import { useEffect, useState } from 'react'
import { fetchPairView } from '../../api'
import { numFmt, rateClass, rateLabel } from './shared'

// 호가 매물대 미니 사다리(PLAN.md §5.50-2) — quote.asks/bids(level 오름차순 1~10,
// backend app/clients/kiwoom.py::parse_quote_levels 참고)를 매도 10→1(위) / 매수
// 1→10(아래) 순서로 뒤집어 그린다. 화면 공간을 고려해 시장가에 가장 가까운 5단계
// (매도 5~1, 매수 1~5)만 항상 보여주고, 나머지 먼 호가(6~10단계)는 <details>로 접어
// 둔다. 매도=파랑(--down), 매수=빨강(--up) — 한국 HTS 관행과 동일하게 매도/매수
// 방향에만 색을 입힐 뿐 "매물대가 두껍다/얇다" 같은 해석 문구는 붙이지 않는다.
function QuoteLadder({ quote }) {
  if (!quote) {
    return (
      <div className="toggle-hint" style={{ fontSize: 11 }}>
        호가 없음
      </div>
    )
  }

  const asksAsc = quote.asks || []
  const bidsAsc = quote.bids || []
  const innerAsks = [...asksAsc.slice(0, 5)].reverse() // 5 -> 1 (아래로 갈수록 시장가에 근접)
  const outerAsks = [...asksAsc.slice(5)].reverse() // 10 -> 6
  const innerBids = bidsAsc.slice(0, 5) // 1 -> 5
  const outerBids = bidsAsc.slice(5) // 6 -> 10

  const row = (r, side) => (
    <tr key={`${side}-${r.level}`}>
      <td style={{ textAlign: 'right', padding: '1px 6px', color: 'var(--text-muted)' }}>{numFmt.format(r.qty)}</td>
      <td
        className={side === 'ask' ? 'down' : 'up'}
        style={{ textAlign: 'right', padding: '1px 6px', fontWeight: r.level === 1 ? 700 : 400 }}
      >
        {numFmt.format(r.price)}
      </td>
    </tr>
  )

  const outerDetails = (rows, side, summary) =>
    rows.length > 0 && (
      <tr>
        <td colSpan={2} style={{ padding: 0 }}>
          <details>
            <summary
              style={{ cursor: 'pointer', textAlign: 'right', padding: '1px 6px', fontSize: 10, color: 'var(--text-muted)' }}
            >
              {summary}
            </summary>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>{rows.map((r) => row(r, side))}</tbody>
            </table>
          </details>
        </td>
      </tr>
    )

  return (
    <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
      <tbody>
        {outerDetails(outerAsks, 'ask', '매도 6~10단계 펼치기')}
        {innerAsks.map((r) => row(r, 'ask'))}
        <tr>
          <td colSpan={2} style={{ borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', height: 4 }} />
        </tr>
        {innerBids.map((r) => row(r, 'bid'))}
        {outerDetails(outerBids, 'bid', '매수 6~10단계 펼치기')}
      </tbody>
    </table>
  )
}

// 종목 페어(본주+레버리지2X+인버스2X) 통합 뷰(PLAN.md §5.50-2/5.50-3) — 삼성전자/
// SK하이닉스 세트를 토글해 세 종목의 호가 매물대와 ETF 괴리율을 한 화면에서
// 비교한다. AttentionFullModal/HynixPositioningModal과 동일한 자기완결 패턴
// (마운트/`set` 변경 시 자기 데이터를 자기가 fetch). **house rule(§5): 관찰치만
// 보여준다** — 괴리율에도 색만 입힐 뿐 "고평가/저평가" 같은 판정 단어는 절대
// 쓰지 않는다.
const PAIR_VIEW_SETS = [
  { key: 'hynix', label: 'SK하이닉스 세트' },
  { key: 'samsung', label: '삼성전자 세트' },
]

export default function PairViewModal() {
  const [set, setSet] = useState('hynix')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPairView(set)
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
  }, [set])

  const columns = data
    ? [
        { key: 'stock', label: '본주', info: data.stock },
        { key: 'leverage', label: '레버리지2X', info: data.leverage },
        { key: 'inverse', label: '인버스2X', info: data.inverse },
      ]
    : []

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 12 }}>
        여기 있는 숫자는 관찰치이며 매수/매도 신호가 아닙니다.
      </div>

      <div className="toggle-row" style={{ marginBottom: 12 }}>
        {PAIR_VIEW_SETS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${set === opt.key ? 'active' : ''}`}
            onClick={() => setSet(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && data?.market_closed && <div className="state">장 마감 — 호가 없음</div>}

      {!loading && !error && data && !data.market_closed && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(150px, 1fr))', gap: 12, overflowX: 'auto' }}>
          {columns.map((col) => (
            <div key={col.key}>
              <div style={{ fontWeight: 700, marginBottom: 2 }}>{col.info.name}</div>
              <div className="toggle-hint" style={{ marginBottom: 6 }}>
                {col.info.code} · {col.label}
              </div>

              {col.key === 'stock' ? (
                <div className={rateClass(col.info.change_rate)} style={{ marginBottom: 8, fontWeight: 600 }}>
                  {rateLabel(col.info.change_rate)}
                </div>
              ) : (
                <div style={{ marginBottom: 8, fontSize: 12 }}>
                  <div>현재가 {col.info.now_value != null ? numFmt.format(col.info.now_value) : '-'}</div>
                  <div>NAV {col.info.nav != null ? numFmt.format(col.info.nav) : '-'}</div>
                  <div className={rateClass(col.info.deviation_pct)}>괴리율 {rateLabel(col.info.deviation_pct)}</div>
                </div>
              )}

              <QuoteLadder quote={col.info.quote} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
