// 거래대금 상위 종목 테이블 — "돈이 모이는 곳" (PLAN.md §4.6 3.6-1).
//
// 순수 표시 컴포넌트다: fetch 없이 props로만 데이터를 받는다. 데이터 로딩/시장 필터
// 상태는 상위 페이지(추후 통합 단계에서 MarketPage.jsx가 GET /api/markets/value-rank를
// 불러 rows를 넘겨준다 — 이 파일은 그 배선을 하지 않는다, PLAN.md §4.6 지시).
//
// value(거래대금)는 백만원 단위로 오므로 FlowRankTable/FlowPathTable과 동일 관례로
// 억원으로 환산해 보여준다(1억원 = 100백만원). change_rate는 부호 있는 %(양수=상승,
// 음수=하락) — 한국 증시 관행대로 상승은 빨강(up), 하락은 파랑(down)으로 칠한다
// (FlowRankTable과 달리 이 표는 buy/sell 탭이 없고 값 자체의 부호로 방향이
// 정해지므로 FlowPathTable의 signClass 패턴을 따른다).
//
// market 배지(코스피/코스닥)는 새 CSS를 추가하지 않고 기존 flow-path-etf-badge
// 클래스(중립색 pill)를 재사용한다. ETF 배지는 FlowRankTable과 동일하게 etf-badge
// 클래스(청록색)를 그대로 쓴다.

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const rateFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const turnoverFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

const MARKET_LABEL = { kospi: '코스피', kosdaq: '코스닥' }

function eokLabel(valueMillion) {
  if (valueMillion === null || valueMillion === undefined) return '-'
  return `${eokFmt.format(valueMillion / 100)}억원`
}

function changeRateLabel(changeRate) {
  if (changeRate === null || changeRate === undefined) return '-'
  const sign = changeRate > 0 ? '+' : ''
  return `${sign}${rateFmt.format(changeRate)}%`
}

function changeRateClass(changeRate) {
  if (changeRate === null || changeRate === undefined) return ''
  return changeRate > 0 ? 'up' : changeRate < 0 ? 'down' : ''
}

function turnoverLabel(turnoverPercent) {
  if (turnoverPercent === null || turnoverPercent === undefined) return '-'
  return `${turnoverFmt.format(turnoverPercent)}%`
}

export default function ValueRankTable({ rows, loading, error, date }) {
  return (
    <div>
      <div className="toggle-row">
        {date && <span className="toggle-hint">{date} 기준</span>}
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
                  <th className="num">거래대금</th>
                  <th className="num">등락률</th>
                  <th className="num">회전율</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.market}-${row.code}`}>
                    <td className="flow-rank-rank">{row.rank}</td>
                    <td>
                      <span className="flow-rank-name">
                        {row.name || row.code}
                        {row.market && (
                          <span className="flow-path-etf-badge">
                            {MARKET_LABEL[row.market] || row.market}
                          </span>
                        )}
                        {row.is_etf && <span className="etf-badge">ETF</span>}
                      </span>
                    </td>
                    <td className="num">{eokLabel(row.value)}</td>
                    <td className={`num ${changeRateClass(row.change_rate)}`}>
                      {changeRateLabel(row.change_rate)}
                    </td>
                    <td className="num flow-rank-turnover">{turnoverLabel(row.turnover)}</td>
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
