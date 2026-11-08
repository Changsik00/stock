import { useState } from 'react'
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

function MarketPanel({ name, m }) {
  if (!m) return null
  const basis = m.label_basis_minutes ? `${m.label_basis_minutes}분 기준` : null
  return (
    <div className="vol-regime-panel">
      <div className="vol-regime-head">
        <strong>{name}</strong>
        <LabelBadge label={m.label} />
        {basis && <span className="vol-regime-dim">{basis}</span>}
        {m.bar_date && !m.is_today && <span className="vol-regime-dim">{m.bar_date} 값</span>}
      </div>
      <dl className="vol-regime-grid">
        <dt>시초 갭</dt>
        <dd>{fmt(m.gap_pct, 2, true)}%</dd>
        {(m.windows ?? []).map((w) => (
          <div key={w.minutes} className="vol-regime-row">
            <dt>초반 {w.minutes}분 rv</dt>
            <dd>{windowCell(w)}</dd>
          </div>
        ))}
        <dt>오늘 레인지</dt>
        <dd>
          {fmt(m.range_today_pct)}%
          {m.range_ratio !== null && m.range_ratio !== undefined && ` (20일 평균 ${fmt(m.avg20_range_pct)}% 대비 ${fmt(m.range_ratio)}배)`}
        </dd>
      </dl>
      {(m.warnings ?? []).map((w) => (
        <div key={w} className="vol-regime-warning">
          {w}
        </div>
      ))}
    </div>
  )
}

export default function VolRegimeCard({ data }) {
  const [open, setOpen] = useState(false)
  if (!data) return null
  const nDays = data.kospi?.windows?.find((w) => w.n_days)?.n_days ?? data.kosdaq?.windows?.find((w) => w.n_days)?.n_days
  return (
    <>
      <div className="section-title" style={{ marginTop: 16 }}>
        오늘 변동성 레짐{' '}
        <button type="button" className="btn btn-sm vol-regime-info" onClick={() => setOpen(true)}>
          설명
        </button>
      </div>
      <div className="regime-card" style={{ borderColor: 'var(--border)' }}>
        <div className="vol-regime-panels">
          <MarketPanel name="코스피" m={data.kospi} />
          <MarketPanel name="코스닥" m={data.kosdaq} />
        </div>
        <div className="vol-regime-foot">
          {data.disclaimer} 장 시작 후 N분 실현변동성(rv)을 과거 {nDays ?? '약 95'}거래일 같은 윈도우 분포와 비교한 백분위(P) ·
          평온 &lt;P40 · 보통 P40~80 · 고변동 ≥P80 · 기준 {data.as_of?.slice(0, 16).replace('T', ' ')}
        </div>
      </div>
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
