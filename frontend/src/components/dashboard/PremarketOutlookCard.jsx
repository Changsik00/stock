import { useState } from 'react'
import { Skel } from './MetricTile'
import { mmdd } from './shared'

// 프리마켓 예상 시작점 카드(PLAN.md §5.107 방향 확정) — GET /api/markets/premarket-outlook 응답을 그대로 그린다.
// **표시 전용, 매매 신호 아님.** 밤사이 나스닥 선물 변화로 08:00 프리 시가 갭의 방향과 대략의 범위를 보여준다.
// 색 규칙: 방향색(--up/--down)은 ▲▼ 화살표에만, 숫자 자체는 기본색. 나스닥 변화가 작으면(weak) 회색 중립 배지 +
// 종목 숫자 강조를 낮춘다(경고 주황이 아니다). phase=hidden(정규장 중)이면 아무것도 그리지 않는다.

const MINUS = '−'

// 부호 있는 퍼센트 문자열(범위 표기용): +0.4 / −1.2
function signed(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const s = Math.abs(v).toFixed(d)
  if (Number(s) === 0) return s
  return `${v > 0 ? '+' : MINUS}${s}`
}

// ▲1.6% 형태 — 화살표만 방향색, 숫자는 기본색(색만으로 방향을 전달하지 않는다)
function Pct({ v, d = 1, strong }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className="pm-na">-</span>
  const abs = Math.abs(v).toFixed(d)
  const dir = Number(abs) === 0 ? 'flat' : v > 0 ? 'up' : 'down'
  return (
    <span className={`pm-pct${strong ? ' pm-strong' : ''}`}>
      {dir !== 'flat' && (
        <span className={`pm-arrow ${dir}`} aria-hidden="true">
          {dir === 'up' ? '▲' : '▼'}
        </span>
      )}
      <span className="mt-sr">{dir === 'up' ? '상승 ' : dir === 'down' ? '하락 ' : '보합 '}</span>
      {abs}%
    </span>
  )
}

function won(v) {
  return typeof v === 'number' ? `${Math.round(v).toLocaleString('ko-KR')}원` : '-'
}

// ISO 문자열에서 시각만 뽑는다(시간대 변환 없이 서버가 준 표기 그대로).
function hm(iso) {
  return typeof iso === 'string' && iso.length >= 16 ? iso.slice(11, 16) : null
}
function md(iso) {
  return typeof iso === 'string' && iso.length >= 10 ? iso.slice(5, 10) : null
}

function ageText(min) {
  if (typeof min !== 'number' || min < 60) return null
  const h = Math.floor(min / 60)
  return h >= 24 ? `마지막 값 ${Math.floor(h / 24)}일${h % 24 ? ` ${h % 24}시간` : ''} 전(휴장/정비)` : `마지막 값 ${h}시간 전(휴장/정비)`
}

function pctFrac(v) {
  if (typeof v !== 'number') return null
  return Math.round(v <= 1 ? v * 100 : v)
}

function NqHero({ nq }) {
  if (!nq) return <div className="mt-empty">나스닥 선물 값을 가져오지 못했습니다</div>
  const stale = ageText(nq.age_minutes)
  const sameDay = md(nq.base_time) === md(nq.now_time)
  const baseLabel = `${md(nq.base_time) ? `${md(nq.base_time)} ` : ''}${hm(nq.base_time) ?? ''}`.trim()
  const nowLabel = `${sameDay ? '' : `${md(nq.now_time)} `}${hm(nq.now_time) ?? ''}`.trim()
  return (
    <div className="pm-hero-block">
      <div className="pm-hero-row">
        <span className="mt-label pm-hero-label">나스닥 선물</span>
        <span className="pm-hero">
          <Pct v={nq.change_pct} d={2} />
        </span>
        {nq.weak && <span className="badge badge-neutral badge-neutral-dashed">나스닥 변동 작음 — 참고 정보 약함</span>}
      </div>
      <div className="pm-sub">
        {baseLabel} → {nowLabel} 기준{nq.delay_note ? ` · ${nq.delay_note}` : ''}
      </div>
      {stale && <div className="pm-sub">{stale}</div>}
    </div>
  )
}

function Row({ it, opened, weak }) {
  const cls = `pm-row${opened ? ' pm-row-opened' : ''}${weak ? ' pm-weak' : ''}`
  if (it.status !== 'ok') {
    return (
      <li className="pm-row pm-row-na">
        <span className="pm-name">{it.name}</span>
        <span className="pm-na-text">{it.status === 'insufficient' ? '표본 부족' : '데이터 없음'}</span>
      </li>
    )
  }
  const hasActual = opened && typeof it.actual_gap_pct === 'number'
  const err = it.error_pct
  return (
    <li className={cls}>
      <span className="pm-name">{it.name}</span>
      <span className="pm-c pm-exp">
        <span className="pm-lbl">예상 </span>
        <Pct v={it.expected_gap_pct} strong={!opened && !weak} />
      </span>
      {opened && (
        <span className="pm-c pm-act">
          {hasActual ? (
            <>
              <span className="pm-lbl">실제 </span>
              <Pct v={it.actual_gap_pct} strong />
            </>
          ) : (
            <span className="pm-na">시가 대기</span>
          )}
        </span>
      )}
      {opened && (
        <span className="pm-c pm-diff">
          {hasActual && typeof err === 'number' ? `예상보다 ${signed(err)}%p` : ''}
        </span>
      )}
      <span className="pm-c pm-range">
        {signed(it.expected_low_pct)} ~ {signed(it.expected_high_pct)}%
      </span>
      {!opened && <span className="pm-c pm-price">{won(it.expected_price)}</span>}
    </li>
  )
}

function Detail({ items, nDays }) {
  const oks = items.filter((i) => i.status === 'ok')
  return (
    <div className="pm-detail">
      <ul className="pm-dl">
        {oks.map((i) => {
          const t = i.track
          const rec = t && t.n >= 5 ? `실제 기록 ${t.n}일: 방향 일치 ${pctFrac(t.hit_rate) ?? '-'}% · 평균 오차 ${t.mae_pct?.toFixed(2) ?? '-'}%p` : t && t.n > 0 ? `실제 기록 ${t.n}일(5일 미만)` : '실제 기록 없음'
          return (
            <li key={i.code}>
              <span className="pm-dl-name">{i.name}</span>
              <span>
                beta {i.beta?.toFixed(2)} · 표본 {i.n}일 · 오차 중앙값 {i.resid_mad_pct?.toFixed(2)}%p
              </span>
              <span>{rec}</span>
            </li>
          )
        })}
      </ul>
      <ul className="pm-notes">
        <li>갭 = 전일 종가 대비 08:00 프리마켓 시가</li>
        <li>나스닥 변화 = 전일 15:30 ~ 지금(약 10분 지연)</li>
        <li>과거 {nDays ?? 60}거래일의 선형 관계로 추정, 범위는 과거 오차 표준편차</li>
      </ul>
      <ul className="pm-notes pm-limits">
        <li>한계: 크기 오차는 약 ±1%p로 작지 않습니다.</li>
        <li>한계: 나스닥이 조금 움직인 날은 정보가 거의 없습니다.</li>
      </ul>
    </div>
  )
}

// data: undefined/null=로딩, {error:true}=실패, 그 외 API 응답
export default function PremarketOutlookCard({ data }) {
  const [more, setMore] = useState(false)
  if (data && data.phase === 'hidden') return null
  if (data && !data.error && (!Array.isArray(data.items) || data.items.length === 0)) return null

  const title = data?.phase === 'opened' ? '프리마켓 시작 — 예상 대비' : '프리마켓 예상 시작점'
  const head = (
    <div className="section-title mt-title pm-title">
      <span>{title}</span>
      {data?.target_date && !data.error && (
        <span className="pm-meta">
          {mmdd(data.target_date)}
          {data.target_date_assumed ? ' (다음 거래일 가정)' : ''}
        </span>
      )}
      {data?.disclaimer && !data.error && <span className="pm-meta">{data.disclaimer}</span>}
    </div>
  )

  if (!data) {
    return (
      <div className="m-font">
        {head}
        <div className="pm-card" aria-busy="true">
          <div className="pm-hero-block">
            <Skel w={96} h={14} />
            <Skel w={140} h={30} />
            <Skel w={200} h={14} />
          </div>
          <div className="pm-list-wrap">
            <Skel w={260} h={20} />
          </div>
        </div>
      </div>
    )
  }
  if (data.error) {
    return (
      <div className="m-font">
        {head}
        <div className="pm-card pm-state" role="status">
          예상 시작점을 불러오지 못했습니다. 잠시 뒤 자동으로 다시 시도합니다.
        </div>
      </div>
    )
  }

  const opened = data.phase === 'opened'
  const weak = Boolean(data.nq?.weak)
  const nDays = data.items.find((i) => i.status === 'ok')?.n
  return (
    <div className="m-font">
      {head}
      <section className="pm-card" aria-label={title}>
        <NqHero nq={data.nq} />
        <ul className={`pm-list${opened ? ' pm-list-opened' : ''}`}>
          <li className="pm-head" aria-hidden="true">
            <span>종목</span>
            <span className="pm-c">예상 갭</span>
            {opened && <span className="pm-c">실제 갭</span>}
            {opened && <span className="pm-c">차이</span>}
            <span className="pm-c">범위</span>
            {!opened && <span className="pm-c">예상가</span>}
          </li>
          {data.items.map((it) => (
            <Row key={it.code} it={it} opened={opened} weak={weak} />
          ))}
        </ul>
        <div className="pm-foot">
          최근 {nDays ?? '약 60'}거래일 관계로 추정 · 오차 범위는 과거 표준편차
          <button type="button" className="btn btn-link btn-sm pm-toggle" aria-expanded={more} onClick={() => setMore(!more)}>
            {more ? '접기' : '자세히'}
          </button>
        </div>
        {more && <Detail items={data.items} nDays={nDays} />}
      </section>
    </div>
  )
}
