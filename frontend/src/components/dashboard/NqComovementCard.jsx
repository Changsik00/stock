import { useState } from 'react'
import { Delta, Skel } from './MetricTile'

// 코스피–나스닥100 선물(NQ) 장중 동조도 카드(PLAN.md Phase 5.116). 오늘 5분 수익률 상관과
// 코스피 등락 분해(나스닥으로 설명되는 부분 / 한국 고유)를 보여 준다. 참고용 — 매매 신호 아님.
// 라벨·상관은 중립(기본 텍스트 + 굵기), 방향색(▲▼)은 등락 수치에만 쓴다. 주황 경고는 쓰지 않는다.

const pct0 = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const num2 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function mmdd(iso) {
  return iso ? iso.slice(5) : ''
}

function signedPct(v) {
  return `${v < 0 ? '−' : '+'}${num2.format(Math.abs(v))}%`
}

function dir(v) {
  // 0.005 미만은 표시상 0.00이라 보합 취급
  return v >= 0.005 ? 'up' : v <= -0.005 ? 'down' : ''
}

function Chg({ v }) {
  if (typeof v !== 'number') return <span>-</span>
  return <Delta dir={dir(v)}>{signedPct(v)}</Delta>
}

export default function NqComovementCard({ data, loading }) {
  const [hintOpen, setHintOpen] = useState(false)
  const hasData = data && data.date
  const today = data?.today
  const dec = data?.decomposition
  const gap = data?.overnight
  const hist = data?.history
  return (
    <>
      <div className="section-title mt-title vr-title">
        <span>
          코스피–나스닥 선물 동조도
          {hasData && (
            <span className="narr-meta">
              {data.market_closed ? ` 장 마감 · ${mmdd(data.date)} 기준` : ` 장중 · ${data.as_of ?? '-'} 기준`}
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
        <div className="vr-card nc-card">
          {!today ? (
            <span className="vr-state">표본 부족 (5분 수익률 쌍 12개 미만)</span>
          ) : (
            <div className="vr-panel">
              <div className="vr-head">
                <h3 className="vr-name">5분 수익률 상관</h3>
                {data.label && <span className="fc-label">{data.label}</span>}
              </div>
              <div className="vr-main">
                <span className="vr-hero">{num2.format(today.corr)}</span>
                <span className="vr-cap">
                  {typeof data.percentile === 'number' && hist?.days > 0
                    ? `과거 ${hist.days}거래일 중 ${pct0.format(data.percentile)}백분위`
                    : '과거 분포 없음'}
                </span>
              </div>
              <dl className="fc-list">
                <div>
                  <dt>베타 (코스피 ÷ NQ)</dt>
                  <dd>{num2.format(today.beta)}</dd>
                </div>
                <div>
                  <dt>설명력 R²</dt>
                  <dd>{pct0.format(today.r2 * 100)}%</dd>
                </div>
              </dl>
              {dec && (
                <dl className="fc-list nc-split">
                  <div>
                    <dt>코스피 시가 대비</dt>
                    <dd><Chg v={dec.kospi_change_pct} /></dd>
                  </div>
                  <div>
                    <dt>NQ 같은 구간</dt>
                    <dd><Chg v={dec.nq_change_pct} /></dd>
                  </div>
                  <div>
                    <dt>나스닥으로 설명되는 부분</dt>
                    <dd><Chg v={dec.explained_pct} /></dd>
                  </div>
                  <div>
                    <dt>한국 고유</dt>
                    <dd><Chg v={dec.unique_pct} /></dd>
                  </div>
                </dl>
              )}
              {gap && (
                <dl className="fc-list nc-split">
                  <div>
                    <dt>밤사이 NQ ({mmdd(gap.prev_date)} 15:30 → 09:00)</dt>
                    <dd><Chg v={gap.nq_pct} /></dd>
                  </div>
                  <div>
                    <dt>코스피 시가 갭</dt>
                    <dd><Chg v={gap.kospi_gap_pct} /></dd>
                  </div>
                </dl>
              )}
              <span className="vr-aside">참고용 — 매매 신호 아님</span>
            </div>
          )}
          {hintOpen && (
            <p className="vr-foot">
              코스피 5분 종가와 나스닥100 선물(NQ) 5분봉의 오늘 09:00 이후 수익률 쌍으로 상관·베타(코스피 = 베타 × NQ)를 구하고,
              코스피 시가 대비 등락을 베타 × NQ 같은 구간 등락(나스닥으로 설명되는 부분)과 나머지(한국 고유)로 나눕니다.
              라벨은 과거 일별 상관 분포(
              {hist?.days > 0
                ? `${hist.days}거래일, 중앙값 ${num2.format(hist.median)}, 하위 1/3 경계 ${num2.format(hist.lower)}, 상위 1/3 경계 ${num2.format(hist.upper)}`
                : '없음'}
              )의 상위 1/3은 동조 강함, 하위 1/3은 동조 약함, 나머지는 보통입니다. 과거 값은 정규장 전체 기준이라 장중에는 일부 구간과
              비교합니다. NQ는 약 10분 지연되며 둘 다 있는 마지막 5분까지만 계산합니다. 상관은 인과·예측이 아닙니다 — 참고용, 매매 신호 아님.
            </p>
          )}
        </div>
      )}
    </>
  )
}
