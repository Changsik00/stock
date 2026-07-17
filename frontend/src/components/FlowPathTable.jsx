// ETF 경유 수급 상위 테이블 (PLAN.md §4.5, 시장 탭 "ETF 경유 수급 상위").
//
// flow_path.via_etf_net = Σ ETF의 순유입(우선 net_inflow, 없으면 flow_rank 근사) ×
// ETF 내 비중 — "ETF로 들어온 돈이 결국 어떤 개별 종목을 사는가"를 근사한 값이다
// (백엔드 collectors/flow_path.py 참고). direct_net은 flow_rank 랭킹 상위에 없으면
// NULL(미관측, 0 아님)이다. 단위는 net_value와 동일하게 백만원으로 오므로 화면에는
// 억원으로 환산(FlowRankTable과 동일 관례)한다.

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

function eokLabel(netValueMillion) {
  if (netValueMillion === null || netValueMillion === undefined) return '–'
  return `${eokFmt.format(netValueMillion / 100)}억원`
}

function signClass(netValueMillion) {
  if (netValueMillion === null || netValueMillion === undefined) return ''
  return netValueMillion >= 0 ? 'up' : 'down'
}

const BASIS_LABEL = { inflow: '순유입', rank: '랭킹근사' }

export default function FlowPathTable({ loading, error, date, rows }) {
  return (
    <div>
      <div className="toggle-row">
        {date && (
          <span className="toggle-hint">
            {date} 기준 (ETF 순유입 × 구성비중으로 추정한 경유 수급, top10 구성 기준)
          </span>
        )}
      </div>

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (!rows || rows.length === 0) && (
        <div className="state">표시할 데이터가 없습니다.</div>
      )}

      {!loading && !error && rows && rows.length > 0 && (
        <div className="flow-rank-card">
          <div className="table-scroll">
            <table className="flow-rank-table">
              <thead>
                <tr>
                  <th>순위</th>
                  <th>종목명</th>
                  <th className="num">ETF 경유 유입</th>
                  <th className="num">직접 순매수</th>
                  <th>기여 ETF</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={row.code}>
                    <td className="flow-rank-rank">{i + 1}</td>
                    <td>
                      <span className="flow-rank-name">{row.name || row.code}</span>
                    </td>
                    <td className={`num ${signClass(row.via_etf_net)}`}>{eokLabel(row.via_etf_net)}</td>
                    <td className={`num ${signClass(row.direct_net)}`}>{eokLabel(row.direct_net)}</td>
                    <td>
                      <div className="flow-path-etf-badges">
                        {(row.top_etfs || []).slice(0, 3).map((etf) => (
                          <span
                            key={etf.code}
                            className="flow-path-etf-badge"
                            title={`${etf.name || etf.code} · ${eokLabel(etf.contrib)} · ${
                              BASIS_LABEL[etf.basis] || etf.basis
                            } · ${etf.date}`}
                          >
                            {etf.name || etf.code}
                          </span>
                        ))}
                        {(!row.top_etfs || row.top_etfs.length === 0) && (
                          <span className="flow-path-etf-empty">–</span>
                        )}
                      </div>
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
