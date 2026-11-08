import { useEffect, useState } from 'react'
import { Skel } from './MetricTile'
import Badge from '../Badge'
import Modal from '../Modal'

// 오늘 변동성 레짐 카드(PLAN.md §5.95) — GET /api/markets/vol-regime 응답을 그대로 그린다.
// **표시 전용**: 매매 판정/자동매매 규칙과 연결되지 않는다. 색은 방향색(상승=빨강/하락=파랑)과
// 혼동되지 않게 평온/보통=회색 계열, 고변동=주황(기존 badge-warn 재사용)만 쓴다.

const LABELS = {
  calm: { text: '평온', cls: 'badge-neutral' },
  normal: { text: '보통', cls: 'badge-neutral badge-neutral-strong' },
  high: { text: '고변동', kind: 'warn' },
  wait: { text: '대기', cls: 'badge-neutral badge-neutral-dashed' },
  partial: { text: '부분계산', cls: 'badge-neutral badge-neutral-dashed' },
  nodata: { text: '데이터 없음', cls: 'badge-neutral badge-neutral-dashed' },
}

function fmt(v, d = 2, sign = false) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  return `${sign && v > 0 ? '+' : ''}${v.toFixed(d)}`
}

function LabelBadge({ label }) {
  const l = LABELS[label] ?? LABELS.nodata
  if (l.kind) return <Badge kind={l.kind}>{l.text}</Badge>
  return <span className={`badge ${l.cls}`}>{l.text}</span>
}

function windowCell(w) {
  if (w.status === 'pending') return <span className="vol-regime-dim">대기</span>
  return (
    <span className={w.status === 'partial' ? 'vol-regime-partial' : undefined}>
      {fmt(w.rv_pct)}%
      {w.percentile !== null && w.percentile !== undefined && ` (P${Math.round(w.percentile)})`}
      {w.status === 'partial' && ` · 부분 ${w.bars}/${w.expected}봉`}
    </span>
  )
}

// 요약에 올릴 대표 윈도우 — 라벨 기준(가장 긴 완전 윈도우) > 완전 윈도우 중 가장 긴 것 > 부분계산 중 가장 긴 것.
// 계산 로직이 아니라 "어느 값을 크게 보여줄지" 선택일 뿐이며 값 자체는 API 응답 그대로다.
function pickWindow(m) {
  const ws = m.windows ?? []
  const basis = ws.find((w) => w.minutes === m.label_basis_minutes && w.status === 'ok')
  if (basis) return basis
  const rev = [...ws].reverse()
  return rev.find((w) => w.status === 'ok') ?? rev.find((w) => w.status === 'partial') ?? null
}

// 요약(라벨 배지 + 핵심 숫자 하나 + 보조 한 줄) → 상세(갭/윈도우별 rv/경고)는 접기.
function MarketPanel({ name, m }) {
  const [more, setMore] = useState(false)
  if (!m) return null
  const w = pickWindow(m)
  const basis = m.label_basis_minutes ? `${m.label_basis_minutes}분 기준` : null
  const warnCount = (m.warnings ?? []).length
  return (
    <section className="vr-panel" aria-label={`${name} 변동성 레짐`}>
      <div className="vr-head">
        <h3 className="vr-name">{name}</h3>
        <LabelBadge label={m.label} />
        {basis && <span className="vr-aside">{basis}</span>}
        {m.bar_date && !m.is_today && <span className="vr-aside">{m.bar_date} 값</span>}
      </div>
      {w ? (
        <div className="vr-main">
          <span className="vr-hero">{fmt(w.rv_pct)}%</span>
          <span className="vr-cap">
            초반 {w.minutes}분 변동성
            {w.percentile !== null && w.percentile !== undefined && ` · 과거 대비 P${Math.round(w.percentile)}`}
            {w.status === 'partial' && ' · 부분계산'}
          </span>
        </div>
      ) : (
        <div className="vr-main">
          <span className="vr-hero vr-hero-empty">-</span>
          <span className="vr-cap">장 시작 후 5분이 지나면 표시됩니다</span>
        </div>
      )}
      <div className="vr-sub">
        오늘 레인지 {fmt(m.range_today_pct)}%
        {m.range_ratio !== null && m.range_ratio !== undefined && ` · 20일 평균의 ${fmt(m.range_ratio)}배`}
      </div>
      {warnCount > 0 && !more && <div className="vr-warn-line">주의 {warnCount}건 — 자세히에서 확인</div>}
      <button type="button" className="btn btn-link btn-sm vr-toggle" aria-expanded={more} onClick={() => setMore(!more)}>
        {more ? '접기' : '자세히'}
      </button>
      {more && (
        <>
          <dl className="vol-regime-grid">
            <dt>시초 갭</dt>
            <dd>{fmt(m.gap_pct, 2, true)}%</dd>
            {(m.windows ?? []).map((x) => (
              <div key={x.minutes} className="vol-regime-row">
                <dt>초반 {x.minutes}분 rv</dt>
                <dd>{windowCell(x)}</dd>
              </div>
            ))}
            <dt>20일 평균 레인지</dt>
            <dd>{fmt(m.avg20_range_pct)}%</dd>
          </dl>
          {(m.warnings ?? []).map((x) => (
            <div key={x} className="vol-regime-warning">
              {x}
            </div>
          ))}
        </>
      )}
    </section>
  )
}

// 로딩/실패 표시용 — 응답이 오기 전엔 스켈레톤, 8초가 지나도 값이 없으면 실패 안내(값이 오면 자동 복구).
function useStalled(active, ms = 8000) {
  const [stalled, setStalled] = useState(false)
  useEffect(() => {
    if (!active) return undefined
    const t = setTimeout(() => setStalled(true), ms)
    return () => {
      clearTimeout(t)
      setStalled(false)
    }
  }, [active, ms])
  return stalled
}

export default function VolRegimeCard({ data }) {
  const [open, setOpen] = useState(false)
  const stalled = useStalled(!data)
  const nDays = data?.kospi?.windows?.find((w) => w.n_days)?.n_days ?? data?.kosdaq?.windows?.find((w) => w.n_days)?.n_days
  return (
    <>
      <div className="section-title mt-title vr-title">
        <span>오늘 변동성 레짐</span>
        <button type="button" className="btn btn-sm vol-regime-info" onClick={() => setOpen(true)}>
          설명
        </button>
      </div>
      {!data ? (
        stalled ? (
          <div className="vr-card vr-state" role="status">
            변동성 데이터를 아직 불러오지 못했어요. 잠시 뒤 자동으로 다시 시도합니다.
          </div>
        ) : (
          <div className="vr-card vr-panels" aria-busy="true">
            {[0, 1].map((i) => (
              <div key={i} className="vr-panel">
                <Skel w={120} h={20} />
                <Skel w={140} h={30} />
                <Skel w={180} h={14} />
              </div>
            ))}
          </div>
        )
      ) : (
        <div className="vr-card">
          <div className="vr-panels">
            <MarketPanel name="코스피" m={data.kospi} />
            <MarketPanel name="코스닥" m={data.kosdaq} />
          </div>
          <div className="vr-foot">
            {data.disclaimer} 과거 {nDays ?? '약 95'}거래일 같은 윈도우와 비교 · 평온 &lt;P40 · 보통 P40~80 · 고변동 ≥P80 · 기준{' '}
            {data.as_of?.slice(0, 16).replace('T', ' ')}
          </div>
        </div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="오늘 변동성 레짐이란?" maxWidth={640}>
        <div className="vol-regime-explain">
          <p>
            <b>정의</b> — 장 시작(09:00) 후 5/15/30분 동안의 1분 로그수익률 제곱합의 제곱근(실현변동성 rv)을, 과거 약 95거래일의 같은 윈도우 rv 분포에서
            몇 번째 백분위인지로 보여줍니다. 가장 긴 &quot;완전한&quot; 윈도우(30→15→5분)의 백분위로 평온(&lt;40) / 보통(40~80) / 고변동(≥80, 연구의 상위 20%)을 매깁니다.
          </p>
          <p>
            <b>근거 연구(PLAN.md §5.94)</b> — 시초 갭만으로는 그날 나머지 변동성을 거의 설명하지 못했지만(교차검증 R² ≈ 0), 장 시작 후 5분만 지나도 초반 rv가 크게 설명했습니다
            (코스피 R² 0.47, 30분 0.61 / 코스닥 5분 0.53, 30분 0.75).
          </p>
          <p>
            <b>한계</b> — 약 95거래일·단일 국면(급등락기) 표본이라 일반화할 수 없습니다. 슬리피지·수수료는 반영하지 않았습니다. 11-05 코스피처럼 장중에 서서히 누적되는 급락은
            시초로 잡히지 않습니다. 분봉 중간 공백(서킷브레이커·수집 공백)이 초반 윈도우를 침범하면 라벨을 만들지 않고 &quot;부분계산&quot;으로만 표시합니다.
          </p>
          <p>
            <b>참고용이며 매매 신호가 아닙니다.</b> 자동매매 판정과 연결되어 있지 않습니다.
          </p>
        </div>
      </Modal>
    </>
  )
}
