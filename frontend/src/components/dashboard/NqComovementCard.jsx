import { useState } from 'react'
import { Delta, Skel } from './MetricTile'

// 코스피 등락 분해 카드(PLAN.md Phase 5.116 → 5.126 개편). 질문은 하나: "오늘 코스피 등락 중
// 나스닥100 선물(E-mini, NQ=F)로 설명되는 몫과 한국 요인 몫은 각각 얼마인가".
// 핵심은 두 몫(막대+한 문장), 상관·베타·R²·밤사이 갭은 '자세히'로 접는다. 참고용 — 매매 신호 아님.
// 방향색(▲▼)은 등락 수치에만, 막대는 중립색(크기만 표현), 주황 경고는 쓰지 않는다.

const pct0 = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const num2 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const NQ = '나스닥100 선물'

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

// 과거 백분위 -> "하위 22%" / "상위 30%"
function rankText(p) {
  if (typeof p !== 'number') return null
  return p < 50 ? `하위 ${pct0.format(Math.max(p, 1))}%` : `상위 ${pct0.format(Math.max(100 - p, 1))}%`
}

// 두 몫을 한 문장으로. "선물 움직임만 보면 X였을 텐데 한국 요인이 Y만큼 끌어올렸/끌어내렸습니다."
function summaryLine(dec) {
  const { explained_pct: ex, unique_pct: un } = dec
  if (typeof ex !== 'number' || typeof un !== 'number') return null
  if (Math.abs(un) < 0.005) return `${NQ} 움직임으로 대부분 설명됩니다.`
  const verb = un < 0 ? '끌어내렸습니다' : '끌어올렸습니다'
  return `${NQ} 움직임만 보면 ${signedPct(ex)}였을 텐데, 한국 요인이 ${num2.format(Math.abs(un))}%p 만큼 ${verb}.`
}

function Bar({ label, v, max }) {
  const w = typeof v === 'number' && max > 0 ? Math.max((Math.abs(v) / max) * 100, 2) : 0
  return (
    <div className="nc-row">
      <span className="nc-row-label">{label}</span>
      <span className="nc-bar" aria-hidden="true">
        <span className="nc-bar-fill" style={{ width: `${w}%` }} />
      </span>
      <span className="nc-row-val">
        <Chg v={v} />
      </span>
    </div>
  )
}

export default function NqComovementCard({ data, loading }) {
  const [hintOpen, setHintOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const hasData = data && data.date
  const today = data?.today
  const dec = data?.decomposition
  const gap = data?.overnight
  const hist = data?.history
  const max = dec ? Math.max(Math.abs(dec.explained_pct || 0), Math.abs(dec.unique_pct || 0)) : 0
  const weak = data?.label === '동조 약함'
  const rank = rankText(data?.percentile)
  return (
    <>
      <div className="section-title mt-title vr-title">
        <span>
          코스피 등락 분해 ({NQ} 기준)
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
          {!today || !dec ? (
            <span className="vr-state">아직 계산할 표본이 부족합니다(장 시작 후 약 1시간 필요).</span>
          ) : (
            <div className="vr-panel">
              <div className="vr-main">
                <span className="vr-cap">오늘 코스피 (시가 대비)</span>
                <span className="nc-hero-row">
                  <span className="vr-hero">{signedPct(dec.kospi_change_pct)}</span>
                  {dir(dec.kospi_change_pct) && <Delta dir={dir(dec.kospi_change_pct)}>{dir(dec.kospi_change_pct) === 'up' ? '상승' : '하락'}</Delta>}
                </span>
              </div>
              <div className="nc-rows">
                <Bar label={`${NQ} 영향`} v={dec.explained_pct} max={max} />
                <Bar label="한국 요인" v={dec.unique_pct} max={max} />
              </div>
              <p className="nc-summary">{summaryLine(dec)}</p>
              <div className="vr-aside">
                오늘 동조: <b>{data.label || '-'}</b>
                {rank && hist?.days > 0 && ` (과거 ${hist.days}거래일 대비 ${rank})`}
                {weak && ' · 같이 움직인 정도가 낮아 분해는 참고 정도로만 보세요.'}
              </div>
              <button type="button" className="btn btn-link btn-sm" aria-expanded={moreOpen} onClick={() => setMoreOpen((v) => !v)}>
                {moreOpen ? '자세히 접기' : '자세히'}
              </button>
              {moreOpen && (
                <dl className="fc-list nc-split">
                  <div>
                    <dt>{NQ} 같은 구간 등락</dt>
                    <dd><Chg v={dec.nq_change_pct} /></dd>
                  </div>
                  <div>
                    <dt>5분 수익률 상관</dt>
                    <dd>{num2.format(today.corr)}</dd>
                  </div>
                  <div>
                    <dt>베타 (선물 1% 움직일 때 코스피 평균 움직임)</dt>
                    <dd>{num2.format(today.beta)}</dd>
                  </div>
                  <div>
                    <dt>설명력 R²</dt>
                    <dd>{pct0.format(today.r2 * 100)}%</dd>
                  </div>
                  {gap && (
                    <>
                      <div>
                        <dt>밤사이 {NQ} ({mmdd(gap.prev_date)} 15:30 → 09:00)</dt>
                        <dd><Chg v={gap.nq_pct} /></dd>
                      </div>
                      <div>
                        <dt>코스피 시가 갭</dt>
                        <dd><Chg v={gap.kospi_gap_pct} /></dd>
                      </div>
                    </>
                  )}
                </dl>
              )}
              <span className="vr-aside">참고용 — 매매 신호 아님</span>
            </div>
          )}
          {hintOpen && (
            <div className="vr-foot">
              <p className="nc-hint">
                <b>무엇을 보나</b> 오늘 코스피가 오르거나 내린 것 중 미국 기술주 흐름({NQ}, CME E-mini, 한국 장중에도 거래)으로
                설명되는 몫과, 나머지 한국 쪽 요인 몫을 나눠 봅니다.
              </p>
              <p className="nc-hint">
                <b>어떻게 읽나</b> 한국 요인이 크게 마이너스면 미국과 무관하게 국내 쪽(외국인 수급·환율·정책·특정 업종)에서 눌린
                날입니다. 두 몫이 같은 방향이면 글로벌 흐름을 따라간 날입니다.
              </p>
              <p className="nc-hint">
                <b>주의</b> 선물이 먼저 움직이고 코스피가 따라가는 선행 관계는 확인되지 않았습니다 — 예측이 아니라 오늘을 해석하는
                용도입니다. {NQ}는 약 10분 늦게 들어오며, 오늘 같이 움직인 정도(동조)가 약한 날은 분해가 부정확합니다. 계산: 09:00
                이후 5분 수익률로 베타를 구해 '선물 영향 = 베타 × 선물 같은 구간 등락', '한국 요인 = 코스피 등락 − 선물 영향'.
                {hist?.days > 0 && ` 동조 강함/보통/약함은 과거 ${hist.days}거래일 상관 분포의 3등분입니다.`}
              </p>
            </div>
          )}
        </div>
      )}
    </>
  )
}
