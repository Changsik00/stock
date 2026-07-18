import Badge from './Badge'
import { formatDate } from '../format'

// 투자자별(외인/기관) 순매수/순매도 상위 종목 테이블 (PLAN.md §4.5/§6 3.5-2b, 시장 탭
// "수급 상위").
//
// flow_rank는 코스피+코스닥을 합쳐 |net_value| 내림차순으로 재정렬한 통합 랭킹이다
// (백엔드 collectors/flow_rank.py 참고 — 소스가 시장별로 완전히 분리된 top20만 주고
// flow_rank 스키마에 시장 컬럼이 없어, 두 시장을 섞어 하나의 rank 공간에 재배치했다).
// net_value는 백만원 단위로 오므로 화면에는 억원으로 환산해 보여준다(1억원 = 100백만원).
//
// net_value/quantity는 백엔드가 이미 항상 양수(크기)로 정규화해서 준다 — 방향은
// side(buy/sell)로만 구분한다(models.py FlowRank docstring 참고). 그래서 이 표의 금액
// 색상은 "값의 부호"가 아니라 "현재 보고 있는 탭"으로 정해진다: 순매수 탭=빨강,
// 순매도 탭=파랑 (§5.4 한국 증시 색상 관행 — 위/빨강, 아래/파랑).
//
// turnover(회전율, %)는 정렬/판단 기준이 아닌 부가 지표(손바뀜 해석용)라서 중립색으로
// 표시하고 소수 1자리로 맞춘다 — 순매수↑+회전율↓=조용한 매집, 순매수↑+회전율↑=세력
// 교체/공방(§4 주포 시그널, PLAN.md §6 3.5-2b).

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const qtyFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const turnoverFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

// §4.6 3.6-1: flow_rank.market은 2026-07-18부터 적재되는 nullable 컬럼 — NULL(과거
// 적재분)이면 배지를 표시하지 않는다. 배지는 ValueRankTable과 동일하게 공용
// Badge.jsx(코스피/코스닥/ETF 색상 구분)를 재사용한다.

const INVESTOR_OPTIONS = [
  { key: 'foreign', label: '외국인' },
  { key: 'institution', label: '기관' },
]

const SIDE_OPTIONS = [
  { key: 'buy', label: '순매수' },
  { key: 'sell', label: '순매도' },
]

function eokLabel(netValueMillion) {
  if (netValueMillion === null || netValueMillion === undefined) return '-'
  return `${eokFmt.format(netValueMillion / 100)}억원`
}

function qtyLabel(quantityThousandShares) {
  if (quantityThousandShares === null || quantityThousandShares === undefined) return '-'
  return qtyFmt.format(quantityThousandShares)
}

function turnoverLabel(turnoverPercent) {
  if (turnoverPercent === null || turnoverPercent === undefined) return '-'
  return `${turnoverFmt.format(turnoverPercent)}%`
}

export default function FlowRankTable({
  investor,
  onInvestorChange,
  side,
  onSideChange,
  loading,
  error,
  dates,
}) {
  const latest = dates && dates.length > 0 ? dates[0] : null
  const amountColorClass = side === 'sell' ? 'down' : 'up'
  const amountHeaderLabel = side === 'sell' ? '순매도 금액' : '순매수 금액'

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
        {SIDE_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${side === opt.key ? 'active' : ''}`}
            onClick={() => onSideChange(opt.key)}
          >
            {opt.label}
          </button>
        ))}
        {latest && <span className="toggle-hint">{formatDate(latest.date)} 기준 (코스피+코스닥 통합)</span>}
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
                  <th className="num">{amountHeaderLabel}</th>
                  <th className="num">수량(천주)</th>
                  <th className="num">회전율</th>
                </tr>
              </thead>
              <tbody>
                {/* key는 row.code가 아니라 rank+code — 같은 종목(code)이 코스피/코스닥
                    통합 랭킹 안에 두 번(예: 다른 소스로 중복 적재된 ETF) 나타날 수 있어
                    row.code만으로는 React key가 겹쳐 "duplicate key" 경고가 났다.
                    rank는 이 리스트 안에서 항상 유일하므로 rank+code로 유일성을 보장한다. */}
                {latest.rows.map((row) => (
                  <tr key={`${row.rank}-${row.code}`}>
                    <td className="flow-rank-rank">{row.rank}</td>
                    <td>
                      <span className="flow-rank-name">
                        {row.name || row.code}
                        {row.market && <Badge kind={row.market} />}
                        {row.is_etf && <Badge kind="etf" />}
                      </span>
                    </td>
                    <td className={`num ${amountColorClass}`}>{eokLabel(row.net_value)}</td>
                    <td className="num">{qtyLabel(row.quantity)}</td>
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
