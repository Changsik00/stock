import Badge from './Badge'
import { formatDate } from '../format'

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
// market·ETF 배지는 FlowRankTable과 동일하게 공용 Badge.jsx(코스피/코스닥/ETF 색상
// 구분)를 쓴다.

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const rateFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const turnoverFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

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

// onRowClick(code, name) — FlowRankTable과 동일한 선택 prop(하위호환, 미지정 시
// 기존처럼 클릭 불가). 종목 상세 모달 연결 통일(사용자 요구) 용도.
export default function ValueRankTable({ rows, loading, error, date, onRowClick }) {
  return (
    <div>
      <div className="toggle-row">
        {date && <span className="toggle-hint">{formatDate(date)} 기준</span>}
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
                {/* key에 rank도 섞는다 — market+code만으로는 같은 시장 안에 같은 code가
                    중복 적재된 경우(예: ETF 중복 수집) 여전히 겹칠 수 있다. rank는 이
                    리스트 안에서 항상 유일하므로 rank+market+code로 유일성을 보장한다
                    (FlowRankTable과 동일한 duplicate key 수정). */}
                {rows.map((row) => (
                  <tr
                    key={`${row.rank}-${row.market}-${row.code}`}
                    className={onRowClick ? 'flow-rank-row-clickable' : undefined}
                    onClick={onRowClick ? () => onRowClick(row.code, row.name) : undefined}
                  >
                    <td className="flow-rank-rank">{row.rank}</td>
                    <td>
                      <span className="flow-rank-name">
                        {row.name || row.code}
                        {row.market && <Badge kind={row.market} />}
                        {row.is_etf && <Badge kind="etf" />}
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
