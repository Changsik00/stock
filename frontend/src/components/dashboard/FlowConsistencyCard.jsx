import { useState } from 'react'
import { Delta, Skel, heroEok } from './MetricTile'

// 장중 외인 매도 일관성 카드(PLAN.md Phase 5.113). 외국인 누적 순매수 곡선이 하루 종일 거의
// 직선으로 내려가는 날(분할·바스켓 매도 성격)을 10분 격자로 판정한 값을 코스피/코스닥 두 패널로 보여준다.
// 참고용 — 매매 신호 아님. 라벨 단어는 방향색(빨강/파랑)을 쓰지 않고 굵기로만 강조하며,
// '일관 매도'만 주의(--warn-text) 표식을 쓴다. ▲▼ 방향색은 수급 금액 쪽에만 쓴다.

const MARKET_NAME = { kospi: '코스피', kosdaq: '코스닥' }
const pctFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const pct1Fmt = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

function mmdd(iso) {
  return iso ? iso.slice(5) : ''
}

function signedPct(v) {
  if (typeof v !== 'number') return '-'
  return `${v < 0 ? '−' : '+'}${pct1Fmt.format(Math.abs(v))}%`
}

function direction(v) {
  return v > 0 ? 'up' : v < 0 ? 'down' : ''
}

function Panel({ market, analysis, instit, program, stats, hintOpen }) {
  const name = MARKET_NAME[market]
  if (!analysis) {
    return (
      <div className="vr-panel fc-panel">
        <h3 className="vr-name">{name}</h3>
        <span className="vr-state">표본 부족 (10분 단위 6개 미만)</span>
      </div>
    )
  }
  const isSell = analysis.label === '일관 매도'
  const final = analysis.final
  return (
    <div className="vr-panel fc-panel">
      <div className="vr-head">
        <h3 className="vr-name">{name} 외국인</h3>
        <span className={`fc-label${isSell ? ' fc-label-sell' : ''}`}>{analysis.label}</span>
      </div>
      <div className="vr-main">
        <span className="vr-hero">{heroEok(final)}</span>
        <Delta dir={direction(final)}>{final > 0 ? '순매수' : final < 0 ? '순매도' : '보합'}</Delta>
      </div>
      <dl className="fc-list">
        <div>
          <dt>10분 단위 매도 비율</dt>
          <dd>{pctFmt.format(analysis.down_share * 100)}%</dd>
        </div>
        <div>
          <dt>장중 최대 반등</dt>
          <dd>{heroEok(analysis.rebound) ?? '-'}</dd>
        </div>
        {typeof analysis.size_percentile === 'number' && (
          <div>
            <dt>과거 대비 순매수 하위</dt>
            <dd>{pctFmt.format(analysis.size_percentile)}%</dd>
          </div>
        )}
        {program && typeof program.non_arb === 'number' && (
          <div>
            <dt>비차익 프로그램 ({mmdd(program.date)} {program.provisional ? '장중 잠정' : '확정'})</dt>
            <dd>{heroEok(program.non_arb)}</dd>
          </div>
        )}
      </dl>
      {instit && (
        <span className="vr-aside">
          기관계 <strong className="fc-strong">{instit.label}</strong> · {heroEok(instit.final)}
        </span>
      )}
      {hintOpen && stats && stats.n > 0 && (
        <span className="vr-aside">
          과거 {stats.days}거래일 중 일관 매도 {stats.n}일 · 큰 일관 매도 {stats.n_large}일 이후 다음 거래일 평균{' '}
          {signedPct(stats.next_day_avg_pct)}, 상승 {typeof stats.next_day_up_pct === 'number' ? pctFmt.format(stats.next_day_up_pct) : '-'}% (전체 평균{' '}
          {signedPct(stats.baseline?.next_day_avg_pct)}, 상승{' '}
          {typeof stats.baseline?.next_day_up_pct === 'number' ? pctFmt.format(stats.baseline.next_day_up_pct) : '-'}%)
        </span>
      )}
    </div>
  )
}

export default function FlowConsistencyCard({ data, loading }) {
  const [hintOpen, setHintOpen] = useState(false)
  const hasData = data && data.date && data.markets && Object.keys(data.markets).length > 0
  return (
    <>
      <div className="section-title mt-title vr-title">
        <span>
          외인 매도 일관성
          {hasData && (
            <span className="narr-meta">
              {data.market_closed ? ` 장 마감 · ${mmdd(data.date)} 기준` : ` 장중 잠정 · ${data.as_of} 기준`}
            </span>
          )}
        </span>
        {hasData && (
          <button type="button" className="btn btn-link btn-sm" aria-expanded={hintOpen} onClick={() => setHintOpen((v) => !v)}>
            {hintOpen ? '설명 닫기' : '설명'}
          </button>
        )}
      </div>
      {loading ? (
        <div className="vr-card">
          <Skel w={160} h={26} />
        </div>
      ) : !hasData ? (
        <div className="vr-card vr-state">데이터 없음</div>
      ) : (
        <div className="vr-card">
          <div className="vr-panels fc-panels">
            {['kospi', 'kosdaq'].map((m) => (
              <Panel
                key={m}
                market={m}
                analysis={data.markets[m]?.['외국인']}
                instit={data.markets[m]?.['기관계']}
                program={data.program?.[m]}
                stats={data.history?.[m]}
                hintOpen={hintOpen}
              />
            ))}
          </div>
          {hintOpen && (
            <p className="vr-foot">
              10분 단위로 본 외국인 누적 순매수가 하루 종일 거의 한 방향(순매도)으로만 이어지고 중간 반등이 작으면
              {' '}일관 매도, 혼조, 일관 매수로 분류합니다. 일정한 속도로 나눠 파는 바스켓(프로그램) 매도 성격의 날을 가려내려는 것이며
              개별 사유(반대매매·공매도 등)를 구분하지는 않습니다. 비차익 프로그램은 장중엔 잠정치, 20:05 이후 확정치입니다.
              과거 통계는 표본이 작아 참고용입니다 — 매매 신호 아님.
            </p>
          )}
        </div>
      )}
    </>
  )
}
