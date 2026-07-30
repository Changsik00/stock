import { formatDate } from '../format'

// 시장 종합 매수세/매도세 게이지 (PLAN.md §4.6 3.6-4, §5.43).
//
// 순수 프레젠테이션 컴포넌트다 — 데이터 페칭 없이 props(GET /api/markets/sentiment
// 응답을 그대로 넘긴 것)만 그린다(BreadthBadge.jsx 패턴). -100(왼쪽, 매도세 우위,
// 파랑=--down) ~ +100(오른쪽, 매수세 우위, 빨강=--up) 수평 게이지 위에 현재 score
// 위치를 마커로 표시하고, 그 아래 breadth/flow/futures/etf 요소별 미니 막대 4개로
// 근거를 보여준다(요소 score가 null이면 "데이터 없음").
//
// 색상은 §5.4 관행(매수/상승=빨강 --up, 매도/하락=파랑 --down, 중립=--text-muted)을
// 그대로 따르고, 하드코딩 hex 없이 전역 CSS 커스텀 프로퍼티만 재사용한다(라이트/다크
// 테마 자동 대응). 스타일은 컴포넌트 내부 <style> 블록에만 정의한다(index.css 전역
// 규칙은 건드리지 않음).
//
// 이 프로젝트 전체가 "상위 랭킹·ETF 유니버스 기반 근사"라는 한계를 갖고 있으므로
// (PLAN.md §4.6 한계 절) approx prop 값과 무관하게 안내 문구를 항상 고정 노출한다.
//
// **라이브/EOD 표시(§5.43)**: breadth(§5.5-4)·flow·futures(§5.43, 신규) 세 요소는
// 장중이면 라이브를 우선한다 — 응답의 components.<id>.source가 "live"면 라벨 옆에
// 초록 "장중" 배지를, 장 마감/폴백으로 EOD 값을 쓸 때는 회색 "EOD" 배지를 붙인다.
// etf 요소는 4개 중 유일하게 라이브 소스가 없어(§5.43 설계 노트 — ETF 순유입/AUM은
// 일단위로만 확정) 항상 "EOD 전용" 배지를 고정 노출한다 — LIVE_CAPABLE 상수로 어떤
// 요소가 라이브 가능한지 판정한다.
//
// flow 요소는 §5.43부터 라이브일 때(코스피+코스닥 전체 투자자 net_value 합산)와
// EOD 폴백일 때(옛 flow_rank 상위 랭킹 근사치)가 서로 다른 원재료 필드를 응답에
// 담는다(individual/foreign/institution vs buy_sum/sell_sum) — 두 소스 자체가
// 다르기 때문에 COMPONENT_META.flow.detail이 있는 필드로 조건 분기해 보여준다.
//
// **flow.by_market(§5.44)**: 사용자가 "코스피만 따로 보면 지금이 평소보다
// 높은지 낮은지"를 물어서 추가됐다 — 종합 게이지 산식(코스피+코스닥 합산
// flow.score)은 그대로 두고, 라이브일 때만 그 아래 코스피/코스닥 개별 점수 +
// 과거 20거래일 대비 percentile(quant/flow_baseline.py, 관찰 지표일 뿐 예측
// 아님)을 추가로 보여준다. EOD 폴백 응답에는 by_market 키가 아예 없으므로
// (백엔드가 만들 수 있는 원재료 자체가 없음) 없으면 조용히 생략한다(이 파일의
// "데이터 없으면 안 그린다" 기존 관례, 예: hasScore 분기와 동일 원칙). 시장별
// baseline도 표본 부족이면 reason만 있고 percentile이 없으므로 그 시장 행의
// baseline 텍스트만 생략한다(점수 자체는 계속 보여줌).

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
  return `${sign}${scoreFmt.format(score)}%`
}

// 투자자별(개인/외국인/기관계) net_value 요약 — flow(라이브) · futures 공통 포맷.
function investorDetail(c) {
  return (
    `개인 ${eokFmt.format((c.individual ?? 0) / 100)}억 · ` +
    `외국인 ${eokFmt.format((c.foreign ?? 0) / 100)}억 · ` +
    `기관 ${eokFmt.format((c.institution ?? 0) / 100)}억`
  )
}

const pctFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

// flow.by_market의 시장 하나(코스피 또는 코스닥) 한 줄 — 점수 + 과거 대비
// percentile(§5.44, StockDetailModal.jsx FlowPercentileNote와 동일한 "N%ile"
// 표기·톤을 재사용). baseline.reason이 있으면(표본 부족) percentile 텍스트만
// 생략하고 점수는 그대로 보여준다("조용히 생략" 관례, 억지로 사유 문구를
// 노출하지 않음 — ComponentBar의 EOD/라이브 배지처럼 매번 설명하지 않는다).
function FlowMarketRow({ label, data }) {
  if (!data) return null
  const baseline = data.baseline
  const hasBaseline = baseline && !baseline.reason
  return (
    <div className="sentiment-gauge-flow-market-row">
      <span className="sentiment-gauge-flow-market-label">{label}</span>
      <span className={`sentiment-gauge-flow-market-score ${scoreClass(data.score)}`}>{scoreLabel(data.score)}</span>
      {hasBaseline && (
        <span
          className="sentiment-gauge-flow-market-baseline"
          title={`최근 ${baseline.lookback_days_used}거래일 평균 ${scoreLabel(baseline.mean_score)}`}
        >
          최근 {baseline.lookback_days_used}거래일 대비 {pctFmt.format(baseline.percentile)}%ile
        </span>
      )}
    </div>
  )
}

// flow가 라이브(by_market 있음)일 때만 코스피/코스닥 개별 행을 그린다(모듈
// 상단 주석 "flow.by_market(§5.44)" 참고) — EOD 폴백이면 by_market 자체가
// 없으므로 아무것도 렌더링하지 않는다.
function FlowByMarket({ byMarket }) {
  if (!byMarket) return null
  return (
    <div className="sentiment-gauge-flow-by-market">
      <FlowMarketRow label="코스피" data={byMarket.kospi} />
      <FlowMarketRow label="코스닥" data={byMarket.kosdaq} />
      <span className="sentiment-gauge-flow-by-market-hint">
        percentile은 100에 가까울수록 그 시장 자신의 최근 거래일 대비 매수 쏠림(참고용, 매매 신호 아님)
      </span>
    </div>
  )
}

const COMPONENT_META = {
  breadth: {
    label: '등락 비율',
    detail: (c) =>
      `상승 ${countFmt.format(c.adv ?? 0)} · 보합 ${countFmt.format(c.flat ?? 0)} · 하락 ${countFmt.format(c.dec ?? 0)}`,
  },
  flow: {
    label: '현물 수급',
    // §5.43부터 라이브(individual/foreign/institution)와 EOD 폴백(옛 flow_rank
    // 상위 랭킹 근사치, buy_sum/sell_sum)이 서로 다른 원재료 필드를 담는다 —
    // 있는 필드로 분기한다.
    detail: (c) =>
      c.individual !== undefined
        ? investorDetail(c)
        : `매수 ${eokFmt.format((c.buy_sum ?? 0) / 100)}억 · 매도 ${eokFmt.format((c.sell_sum ?? 0) / 100)}억`,
  },
  futures: {
    label: 'K200 선물 수급',
    detail: investorDetail,
  },
  etf: {
    label: 'ETF 순유입',
    detail: (c) =>
      `순유입 ${eokFmt.format((c.net_inflow_sum ?? 0) / 100)}억 · AUM ${eokFmt.format((c.aum_sum ?? 0) / 100)}억`,
  },
}

// breadth/flow/futures는 장중이면 라이브를 우선하고 장 마감/실패 시 EOD로
// 폴백한다(source 필드로 구분) — etf는 라이브 소스가 아예 없어 항상 EOD 전용이다
// (§5.43 설계 노트). ComponentBar가 이 집합으로 배지 문구를 가른다.
const LIVE_CAPABLE_COMPONENTS = new Set(['breadth', 'flow', 'futures'])

function ComponentBar({ id, component }) {
  const meta = COMPONENT_META[id]
  const hasScore = component && component.score !== null && component.score !== undefined
  const weight = component?.weight ?? 0
  const isLive = component?.source === 'live'
  const isLiveCapable = LIVE_CAPABLE_COMPONENTS.has(id)

  return (
    <div className="sentiment-gauge-component">
      <div className="sentiment-gauge-component-head">
        <span className="sentiment-gauge-component-label">{meta.label}</span>
        {isLive && <span className="sentiment-gauge-component-live">장중</span>}
        {!isLive && component && (
          <span
            className="sentiment-gauge-component-eod"
            title={isLiveCapable ? '지금은 EOD 확정치를 쓰는 중(장 마감 또는 라이브 실패)' : '라이브 소스 없음 — 항상 EOD 확정치'}
          >
            {isLiveCapable ? 'EOD' : 'EOD 전용'}
          </span>
        )}
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
      {id === 'flow' && <FlowByMarket byMarket={component?.by_market} />}
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
        .sentiment-gauge-component-eod {
          font-size: 9px;
          font-weight: 700;
          color: var(--text-muted);
          border: 1px solid var(--border);
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
        .sentiment-gauge-flow-by-market {
          display: flex;
          flex-direction: column;
          gap: 2px;
          margin-top: 2px;
          padding-left: 4px;
          border-left: 2px solid var(--border);
        }
        .sentiment-gauge-flow-market-row {
          display: flex;
          align-items: baseline;
          gap: 6px;
          font-size: 10px;
        }
        .sentiment-gauge-flow-market-label {
          color: var(--text-secondary);
          font-weight: 600;
          min-width: 32px;
        }
        .sentiment-gauge-flow-market-score {
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        .sentiment-gauge-flow-market-score.up { color: var(--up); }
        .sentiment-gauge-flow-market-score.down { color: var(--down); }
        .sentiment-gauge-flow-market-score.flat { color: var(--text-muted); }
        .sentiment-gauge-flow-market-baseline {
          color: var(--text-muted);
          font-variant-numeric: tabular-nums;
        }
        .sentiment-gauge-flow-by-market-hint {
          font-size: 9px;
          color: var(--text-muted);
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
              <ComponentBar id="futures" component={components.futures} />
              <ComponentBar id="etf" component={components.etf} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
