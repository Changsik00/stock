import { formatDate } from '../format'

// 시장 종합 매수세/매도세 게이지 (PLAN.md §4.6 3.6-4).
//
// 순수 프레젠테이션 컴포넌트다 — 데이터 페칭 없이 props(GET /api/markets/sentiment
// 응답을 그대로 넘긴 것)만 그린다(BreadthBadge.jsx 패턴). -100(왼쪽, 매도세 우위,
// 파랑=--down) ~ +100(오른쪽, 매수세 우위, 빨강=--up) 수평 게이지 위에 현재 score
// 위치를 마커로 표시하고, 그 아래 breadth/flow/etf 요소별 미니 막대 3개로 근거를
// 보여준다(요소 score가 null이면 "데이터 없음").
//
// 색상은 §5.4 관행(매수/상승=빨강 --up, 매도/하락=파랑 --down, 중립=--text-muted)을
// 그대로 따르고, 하드코딩 hex 없이 전역 CSS 커스텀 프로퍼티만 재사용한다(라이트/다크
// 테마 자동 대응). 스타일은 컴포넌트 내부 <style> 블록에만 정의한다(index.css 전역
// 규칙은 건드리지 않음).
//
// 이 프로젝트 전체가 "상위 랭킹·ETF 유니버스 기반 근사"라는 한계를 갖고 있으므로
// (PLAN.md §4.6 한계 절) approx prop 값과 무관하게 안내 문구를 항상 고정 노출한다.
//
// breadth 요소는 2026-07-21(PLAN.md §5.5-4)부터 라이브(breadth/live)를 우선
// 반영한다 — 응답의 components.breadth.source가 "live"면 라벨 옆에 작은 "장중"
// 배지를 붙인다(장 마감/폴백이면 "eod"라 배지 없음). flow/etf 요소는 이번 범위
// 밖이라 source 필드 자체가 없다 — 있을 때만 조건부로 그린다.

const scoreFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })
const countFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

function clampPct(score) {
  const bounded = Math.max(-100, Math.min(100, score ?? 0))
  return ((bounded + 100) / 200) * 100
}

function scoreClass(score) {
  if (score === null || score === undefined) return ''
  if (score > 2) return 'up'
  if (score < -2) return 'down'
  return 'flat'
}

function scoreLabel(score) {
  if (score === null || score === undefined) return '데이터 없음'
  const sign = score > 0 ? '+' : ''
  return `${sign}${scoreFmt.format(score)}`
}

const COMPONENT_META = {
  breadth: {
    label: '등락 비율',
    detail: (c) =>
      `상승 ${countFmt.format(c.adv ?? 0)} · 보합 ${countFmt.format(c.flat ?? 0)} · 하락 ${countFmt.format(c.dec ?? 0)}`,
  },
  flow: {
    label: '외인·기관 수급',
    detail: (c) =>
      `매수 ${eokFmt.format((c.buy_sum ?? 0) / 100)}억 · 매도 ${eokFmt.format((c.sell_sum ?? 0) / 100)}억`,
  },
  etf: {
    label: 'ETF 순유입',
    detail: (c) =>
      `순유입 ${eokFmt.format((c.net_inflow_sum ?? 0) / 100)}억 · AUM ${eokFmt.format((c.aum_sum ?? 0) / 100)}억`,
  },
}

function ComponentBar({ id, component }) {
  const meta = COMPONENT_META[id]
  const hasScore = component && component.score !== null && component.score !== undefined
  const weight = component?.weight ?? 0

  return (
    <div className="sentiment-gauge-component">
      <div className="sentiment-gauge-component-head">
        <span className="sentiment-gauge-component-label">{meta.label}</span>
        {component?.source === 'live' && <span className="sentiment-gauge-component-live">장중</span>}
        <span className="sentiment-gauge-component-weight">가중치 {(weight * 100).toFixed(0)}%</span>
        <span className={`sentiment-gauge-component-score ${scoreClass(component?.score)}`}>
          {scoreLabel(component?.score)}
        </span>
      </div>
      {hasScore ? (
        <div className="sentiment-gauge-mini-track" style={{ opacity: 0.35 + weight * 0.65 }}>
          <div className="sentiment-gauge-mini-mid" />
          <div
            className={`sentiment-gauge-mini-marker ${scoreClass(component.score)}`}
            style={{ left: `${clampPct(component.score)}%` }}
          />
        </div>
      ) : (
        <div className="sentiment-gauge-mini-empty">데이터 없음</div>
      )}
      {component && (
        <div className="sentiment-gauge-component-detail">
          {component.date ? `${formatDate(component.date)} · ` : ''}
          {meta.detail(component)}
        </div>
      )}
    </div>
  )
}

// approx는 항상 고정 문구로 안내하므로(주석 참고) 값 자체는 쓰지 않는다 — 밑줄 접두사로 의도적 미사용 표시.
export default function SentimentGauge({ loading, error, score, approx: _approx, components, date }) {
  const hasScore = score !== null && score !== undefined

  return (
    <div className="sentiment-gauge">
      <style>{`
        .sentiment-gauge {
          display: flex;
          flex-direction: column;
          gap: 12px;
          font-family: inherit;
        }
        .sentiment-gauge-title-row {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          flex-wrap: wrap;
          gap: 6px;
        }
        .sentiment-gauge-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
        }
        .sentiment-gauge-approx {
          font-size: 11px;
          color: var(--text-muted);
        }
        .sentiment-gauge-score {
          font-size: 22px;
          font-weight: 700;
          font-variant-numeric: tabular-nums;
        }
        .sentiment-gauge-score.up { color: var(--up); }
        .sentiment-gauge-score.down { color: var(--down); }
        .sentiment-gauge-score.flat { color: var(--text-muted); }
        .sentiment-gauge-track {
          position: relative;
          height: 12px;
          border-radius: 999px;
          background: linear-gradient(to right, var(--down), var(--border) 50%, var(--up));
        }
        .sentiment-gauge-track-mid {
          position: absolute;
          left: 50%;
          top: -3px;
          bottom: -3px;
          width: 1px;
          background: var(--text-muted);
          opacity: 0.6;
        }
        .sentiment-gauge-marker {
          position: absolute;
          top: -5px;
          width: 2px;
          height: 22px;
          background: var(--text-primary);
          transform: translateX(-1px);
        }
        .sentiment-gauge-scale {
          display: flex;
          justify-content: space-between;
          font-size: 10px;
          color: var(--text-muted);
        }
        .sentiment-gauge-components {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-top: 4px;
        }
        .sentiment-gauge-component {
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .sentiment-gauge-component-head {
          display: flex;
          align-items: baseline;
          gap: 8px;
          font-size: 12px;
        }
        .sentiment-gauge-component-label {
          color: var(--text-secondary);
          font-weight: 600;
          min-width: 88px;
        }
        .sentiment-gauge-component-weight {
          color: var(--text-muted);
          font-size: 10px;
        }
        .sentiment-gauge-component-live {
          font-size: 9px;
          font-weight: 700;
          color: var(--up);
          border: 1px solid var(--up);
          border-radius: 999px;
          padding: 0 5px;
          line-height: 14px;
        }
        .sentiment-gauge-component-score {
          margin-left: auto;
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        .sentiment-gauge-component-score.up { color: var(--up); }
        .sentiment-gauge-component-score.down { color: var(--down); }
        .sentiment-gauge-component-score.flat { color: var(--text-muted); }
        .sentiment-gauge-mini-track {
          position: relative;
          height: 5px;
          border-radius: 999px;
          background: linear-gradient(to right, var(--down), var(--border) 50%, var(--up));
        }
        .sentiment-gauge-mini-mid {
          position: absolute;
          left: 50%;
          top: -1px;
          bottom: -1px;
          width: 1px;
          background: var(--text-muted);
          opacity: 0.5;
        }
        .sentiment-gauge-mini-marker {
          position: absolute;
          top: -2px;
          width: 2px;
          height: 9px;
          background: var(--text-primary);
          transform: translateX(-1px);
        }
        .sentiment-gauge-mini-empty {
          font-size: 11px;
          color: var(--text-muted);
        }
        .sentiment-gauge-component-detail {
          font-size: 10px;
          color: var(--text-muted);
          font-variant-numeric: tabular-nums;
        }
      `}</style>

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}

      {!loading && !error && (
        <>
          <div className="sentiment-gauge-title-row">
            <span className="sentiment-gauge-title">시장 매수세/매도세 게이지</span>
            <span className="sentiment-gauge-approx">
              근사치(상위 랭킹·ETF 유니버스 기반 — 시장 전체 정밀값 아님){date ? ` · ${formatDate(date)} 기준` : ''}
            </span>
          </div>

          <div>
            <div className={`sentiment-gauge-score ${scoreClass(score)}`}>{scoreLabel(score)}</div>
            {hasScore ? (
              <div className="sentiment-gauge-track">
                <div className="sentiment-gauge-track-mid" />
                <div
                  className={`sentiment-gauge-marker ${scoreClass(score)}`}
                  style={{ left: `${clampPct(score)}%` }}
                />
              </div>
            ) : (
              <div className="state">게이지를 계산할 데이터가 없습니다.</div>
            )}
            <div className="sentiment-gauge-scale">
              <span>매도세 -100</span>
              <span>0</span>
              <span>매수세 +100</span>
            </div>
          </div>

          {components && (
            <div className="sentiment-gauge-components">
              <ComponentBar id="breadth" component={components.breadth} />
              <ComponentBar id="flow" component={components.flow} />
              <ComponentBar id="etf" component={components.etf} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
