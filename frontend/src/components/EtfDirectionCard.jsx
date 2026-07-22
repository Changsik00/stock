import { formatDate, formatEok } from '../format'

// 파생형(레버리지/인버스) ETF 방향성 게이지 (PLAN.md §4.5/§6 4.5-1).
//
// 순수 프레젠테이션 컴포넌트다 — 데이터 페칭 없이 props(GET /api/etf/derivative-flow
// 응답 필드를 그대로 넘긴 것)만 그린다(SentimentGauge.jsx와 동일 패턴: DashboardPage가
// score/components를 개별 prop으로 스프레드하듯, 이 컴포넌트도 universe/latest/series를
// 개별 prop으로 받는다 — 통합 단계에서 <EtfDirectionCard loading={..} error={..}
// universe={data?.universe} latest={data?.latest} series={data?.series ?? []} /> 형태로
// 붙이면 된다). fetch도, DashboardPage/api.js/index.css 수정도 이 작업 범위 밖이다
// (§4.5-1 지시 — 다른 에이전트 3개와 병렬 작업 중, 통합은 이후 단계).
//
// 색상은 §5.4 관행(양(+)=빨강 --up, 음(-)=파랑 --down, 중립=--text-muted)을 그대로
// 따른다 — "레버리지에 몰리면 좋다/인버스에 몰리면 나쁘다"는 판단이 아니라, 이
// 프로젝트 전체가 상승=빨강 하락=파랑으로 통일한 순수 표기 관례일 뿐이다(§4.5 배경:
// "중립적 상태 계기판", 함정 탐지기 아님). 스타일은 컴포넌트 내부 <style> 블록에만
// 정의한다(index.css 전역 규칙은 건드리지 않음).

const eokFromMillion = (million) => formatEok(million)

function scoreClass(value) {
  if (value === null || value === undefined) return ''
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

function signLabel(value) {
  if (value === null || value === undefined) return ''
  if (value > 0) return '+'
  return ''
}

// 최근 N일 net_bet 미니 막대 — 각 막대 높이는 그 창 안의 |net_bet| 최댓값 대비 비율,
// 부호에 따라 위(양)/아래(음)로 자란다(가운데 기준선 기준 diverging bar).
function MiniBars({ series }) {
  const values = series.map((d) => d.net_bet ?? 0)
  const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)))

  return (
    <div className="etf-direction-bars">
      {series.map((d) => {
        const v = d.net_bet ?? 0
        const heightPct = (Math.abs(v) / maxAbs) * 100
        return (
          <div className="etf-direction-bar-col" key={d.date} title={`${formatDate(d.date)} · ${eokFromMillion(v)}`}>
            <div className="etf-direction-bar-track">
              {v >= 0 ? (
                <div
                  className="etf-direction-bar up"
                  style={{ height: `${heightPct}%` }}
                />
              ) : (
                <div
                  className="etf-direction-bar down"
                  style={{ height: `${heightPct}%` }}
                />
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function EtfDirectionCard({ loading, error, universe, latest, series }) {
  const rows = series ?? []
  const hasLatest = latest !== null && latest !== undefined
  const netBet = hasLatest ? latest.net_bet : null

  return (
    <div className="etf-direction-card">
      <style>{`
        .etf-direction-card {
          display: flex;
          flex-direction: column;
          gap: 12px;
          font-family: inherit;
        }
        .etf-direction-title-row {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          flex-wrap: wrap;
          gap: 6px;
        }
        .etf-direction-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
        }
        .etf-direction-date {
          font-size: 11px;
          color: var(--text-muted);
        }
        .etf-direction-score {
          font-size: 22px;
          font-weight: 700;
          font-variant-numeric: tabular-nums;
        }
        .etf-direction-score.up { color: var(--up); }
        .etf-direction-score.down { color: var(--down); }
        .etf-direction-score.flat { color: var(--text-muted); }
        .etf-direction-subtitle {
          font-size: 11px;
          color: var(--text-muted);
          margin-top: 2px;
        }
        .etf-direction-rows {
          display: flex;
          flex-direction: column;
          gap: 6px;
          margin-top: 4px;
        }
        .etf-direction-row {
          display: flex;
          align-items: baseline;
          gap: 8px;
          font-size: 12px;
        }
        .etf-direction-row-label {
          color: var(--text-secondary);
          font-weight: 600;
          min-width: 96px;
        }
        .etf-direction-row-value {
          margin-left: auto;
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        .etf-direction-row-value.up { color: var(--up); }
        .etf-direction-row-value.down { color: var(--down); }
        .etf-direction-row-value.flat { color: var(--text-muted); }
        .etf-direction-row-count {
          font-size: 10px;
          color: var(--text-muted);
        }
        .etf-direction-hedge {
          font-size: 11px;
          color: var(--text-muted);
          border-top: 1px solid var(--border);
          padding-top: 6px;
          margin-top: 2px;
        }
        .etf-direction-hedge-value {
          font-variant-numeric: tabular-nums;
          font-weight: 600;
          color: var(--text-secondary);
        }
        .etf-direction-bars {
          display: flex;
          align-items: center;
          gap: 3px;
          height: 48px;
          margin-top: 4px;
        }
        .etf-direction-bar-col {
          /* 2026-07-22 수정 — flex:1로 무제한 늘어나면 데이터가 며칠치뿐일 때
             (예: 2일) 막대 하나가 카드 폭의 절반을 차지해 차트처럼 안 보였다
             (사용자 지적: "차트 크기도 안 맞아"). max-width로 막대 하나의
             최대 폭을 제한해 포인트 수와 무관하게 항상 "얇은 막대들" 모양을
             유지한다 — 포인트가 적으면 왼쪽에 모여 남는 공간은 그냥 비운다. */
          flex: 0 1 auto;
          width: 16px;
          max-width: 16px;
          height: 100%;
          display: flex;
          align-items: center;
          min-width: 2px;
        }
        .etf-direction-bar-track {
          position: relative;
          width: 100%;
          height: 100%;
          display: flex;
          flex-direction: column;
          justify-content: center;
        }
        .etf-direction-bar {
          width: 100%;
          border-radius: 2px;
          align-self: center;
        }
        .etf-direction-bar.up { background: var(--up); align-self: flex-end; }
        .etf-direction-bar.down { background: var(--down); align-self: flex-start; }
      `}</style>

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}

      {!loading && !error && (
        <>
          <div className="etf-direction-title-row">
            <span className="etf-direction-title">개인 방향성(파생ETF)</span>
            <span className="etf-direction-date">
              {hasLatest && latest.date ? `${formatDate(latest.date)} 기준` : ''}
              {universe ? ` · 레버리지 ${universe.leverage ?? 0} · 인버스 ${universe.inverse ?? 0}종목` : ''}
            </span>
          </div>

          {hasLatest ? (
            <div>
              <div className={`etf-direction-score ${scoreClass(netBet)}`}>
                {signLabel(netBet)}
                {eokFromMillion(netBet)}
              </div>
              <div className="etf-direction-subtitle">
                방향성 순베팅(노출 가중) — 레버리지 +, 인버스 − · 참고치(중립 지표, 함정 탐지기 아님)
              </div>

              <div className="etf-direction-rows">
                <div className="etf-direction-row">
                  <span className="etf-direction-row-label">레버리지 순유입</span>
                  <span className={`etf-direction-row-value ${scoreClass(latest.leverage_inflow)}`}>
                    {signLabel(latest.leverage_inflow)}
                    {eokFromMillion(latest.leverage_inflow)}
                  </span>
                  <span className="etf-direction-row-count">{latest.counts?.leverage ?? 0}종목</span>
                </div>
                <div className="etf-direction-row">
                  <span className="etf-direction-row-label">인버스 순유입</span>
                  <span className={`etf-direction-row-value ${scoreClass(latest.inverse_inflow)}`}>
                    {signLabel(latest.inverse_inflow)}
                    {eokFromMillion(latest.inverse_inflow)}
                  </span>
                  <span className="etf-direction-row-count">{latest.counts?.inverse ?? 0}종목</span>
                </div>
              </div>

              <div className="etf-direction-hedge">
                LP 헤지 수요 추정(참고치):{' '}
                <span className="etf-direction-hedge-value">
                  {latest.lp_hedge_est === null || latest.lp_hedge_est === undefined
                    ? '데이터 없음(전일 AUM 관측치 필요)'
                    : `${signLabel(latest.lp_hedge_est)}${eokFromMillion(latest.lp_hedge_est)}`}
                </span>
              </div>
            </div>
          ) : (
            <div className="state">게이지를 계산할 데이터가 없습니다.</div>
          )}

          {rows.length > 0 && (
            <div>
              {/* 2026-07-22 수정 — "최근 N일"은 달력상 연속 N일처럼 읽히지만
                  실제로는 ETF 순유입 소스가 매일 갱신되지 않아(모듈 상단 라우터
                  docstring 참고) 관측치 사이에 며칠씩 공백이 있을 수 있다.
                  포인트가 1개뿐이 아니면 실제 관측 날짜 범위를 같이 보여줘
                  "연속 며칠"로 오해하지 않게 한다. */}
              <div className="etf-direction-subtitle">
                {rows.length === 1
                  ? `관측 ${rows.length}건`
                  : `관측 ${rows.length}건 순베팅 (${formatDate(rows[0].date)} ~ ${formatDate(rows[rows.length - 1].date)})`}
              </div>
              <MiniBars series={rows} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
