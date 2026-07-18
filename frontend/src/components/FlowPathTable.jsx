import { formatDate } from '../format'

// ETF 경유 수급 상위 테이블 (PLAN.md §4.5, 시장 탭 "ETF 경유 수급 상위").
//
// flow_path.via_etf_net = Σ ETF의 순유입(우선 net_inflow, 없으면 flow_rank 근사) ×
// ETF 내 비중 — "ETF로 들어온 돈이 결국 어떤 개별 종목을 사는가"를 근사한 값이다
// (백엔드 collectors/flow_path.py 참고). direct_net은 flow_rank 랭킹 상위에 없으면
// NULL(미관측, 0 아님)이다. 단위는 net_value와 동일하게 백만원으로 오므로 화면에는
// 억원으로 환산(FlowRankTable과 동일 관례)한다.
//
// 유입/유출 토글(§4.6 3.6-4) — 제어 컴포넌트(FlowRankTable의 investor/side 패턴과
// 동일): direction/onDirectionChange를 상위(MarketPage)가 소유한다. 유출 모드에서는
// via_etf_net이 백엔드에서 이미 전부 음수로 오므로 signClass가 자동으로 down(파랑)을
// 붙인다 — 색상 로직은 그대로 두고 헤더/힌트 문구만 모드에 맞게 바꾼다.
//
// ETF 배지 방향 점(dot) — top_etfs[].contrib 부호로 그 ETF의 설정(+, 빨강)/환매(-,
// 파랑) 방향을 표시한다(§4.6 3.6-4 "ETF 방향 배지"). 새 전역 CSS 클래스를 추가하지
// 않고 인라인 style로 배경색만 --up/--down var로 지정한다(.toggle-chip .dot과 동일한
// 8px 원 패턴).

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

const DIRECTION_OPTIONS = [
  { key: 'in', label: '유입' },
  { key: 'out', label: '유출' },
]

function EtfContribDot({ contrib }) {
  if (contrib === null || contrib === undefined || contrib === 0) return null
  const color = contrib > 0 ? 'var(--up)' : 'var(--down)'
  return (
    <span
      style={{
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: color,
        marginRight: 4,
      }}
    />
  )
}

export default function FlowPathTable({ loading, error, date, rows, direction = 'in', onDirectionChange }) {
  const isOut = direction === 'out'
  const columnHeaderLabel = isOut ? 'ETF 경유 유출' : 'ETF 경유 유입'
  const hintText = isOut
    ? '(ETF 순유출 × 구성비중으로 추정한 경유 유출, top10 구성 기준)'
    : '(ETF 순유입 × 구성비중으로 추정한 경유 수급, top10 구성 기준)'

  return (
    <div>
      <div className="toggle-row">
        {onDirectionChange &&
          DIRECTION_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              className={`toggle-chip ${direction === opt.key ? 'active' : ''}`}
              onClick={() => onDirectionChange(opt.key)}
            >
              {opt.label}
            </button>
          ))}
        {date && (
          <span className="toggle-hint">
            {formatDate(date)} 기준 {hintText}
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
                  <th className="num">{columnHeaderLabel}</th>
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
                            } · ${formatDate(etf.date)} · ${etf.contrib > 0 ? '설정' : etf.contrib < 0 ? '환매' : ''}`}
                          >
                            <EtfContribDot contrib={etf.contrib} />
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
