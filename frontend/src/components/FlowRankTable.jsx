// 투자자별(외인/기관) 순매수 상위 종목 테이블 (PLAN.md §4.5, 시장 탭 "수급 상위").
//
// flow_rank는 코스피+코스닥을 합쳐 net_value 내림차순으로 재정렬한 통합 랭킹이다
// (백엔드 collectors/flow_rank.py 참고 — 소스가 시장별로 완전히 분리된 top20만 주고
// flow_rank 스키마에 시장 컬럼이 없어, 두 시장을 섞어 하나의 rank 공간에 재배치했다).
// net_value는 백만원 단위로 오므로 화면에는 억원으로 환산해 보여준다(1억원 = 100백만원).

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

const INVESTOR_OPTIONS = [
  { key: 'foreign', label: '외국인' },
  { key: 'institution', label: '기관' },
]

function eokLabel(netValueMillion) {
  if (netValueMillion === null || netValueMillion === undefined) return '-'
  return `${eokFmt.format(netValueMillion / 100)}억원`
}

export default function FlowRankTable({ investor, onInvestorChange, loading, error, dates }) {
  const latest = dates && dates.length > 0 ? dates[0] : null

  return (
    <div>
      <div className="toggle-row">
        {INVESTOR_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${investor === opt.key ? 'active' : ''}`}
            onClick={() => onInvestorChange(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        {latest && <span className="toggle-hint">{latest.date} 기준 (코스피+코스닥 통합 순매수 상위)</span>}
      </div>

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (!latest || latest.rows.length === 0) && (
        <div className="state">표시할 데이터가 없습니다.</div>
      )}

      {!loading && !error && latest && latest.rows.length > 0 && (
        <div className="flow-rank-card">
          <div className="table-scroll">
            <table className="flow-rank-table">
              <thead>
                <tr>
                  <th>순위</th>
                  <th>종목명</th>
                  <th className="num">순매수 금액</th>
                </tr>
              </thead>
              <tbody>
                {latest.rows.map((row) => (
                  <tr key={row.code}>
                    <td className="flow-rank-rank">{row.rank}</td>
                    <td>
                      <span className="flow-rank-name">
                        {row.name || row.code}
                        {row.is_etf && <span className="etf-badge">ETF</span>}
                      </span>
                    </td>
                    <td className="num">{eokLabel(row.net_value)}</td>
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
