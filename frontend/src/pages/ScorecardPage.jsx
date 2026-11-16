import { useCallback, useEffect, useState } from 'react'
import { fetchScorecard } from '../api'
import { NarrSkeleton } from '../components/dashboard/NarrativeRow'

// 지표 성적표 탭 — 대시보드 신호를 같은 기준으로 채점한 기록(표시 전용, 매매 지시 아님).
// 로컬 DB 기반이라 정적 배포에서는 App.jsx에서 탭 자체를 노출하지 않는다.
// 색 규칙: --up/--down은 가격 방향 전용이므로 적중률·차이에는 쓰지 않고, 굵기·회색으로만 강조한다.

const DASH = '–'
const MIN_N = 30

function fmtPct(v) {
  return v == null ? DASH : `${v.toFixed(1)}%`
}

// 차이(%p): 부호는 +/−(U+2212), 0은 부호 없이
function fmtEdge(v) {
  if (v == null) return DASH
  const abs = Math.abs(v).toFixed(1)
  if (v > 0) return `+${abs}%p`
  if (v < 0) return `−${abs}%p`
  return `${abs}%p`
}

function fmtAvg(v) {
  if (v == null) return DASH
  const abs = Math.abs(v).toFixed(2)
  if (v > 0) return `+${abs}`
  if (v < 0) return `−${abs}`
  return abs
}

function fmtRecent(r) {
  if (!r || r.hit_rate == null || r.hits == null) return DASH
  return `${r.hits}/${r.n} · ${Math.round(r.hit_rate)}%`
}

function fmtGenerated(iso) {
  if (!iso) return ''
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso)
  return m ? `${m[1]} ${m[2]}` : iso
}

// 셀: 좁은 화면에서만 보이는 라벨(.sc-cl)과 값(.sc-cv)
function Cell({ label, className = '', children }) {
  return (
    <div className={`sc-cell ${className}`}>
      <span className="sc-cl">{label}</span>
      <span className="sc-cv">{children}</span>
    </div>
  )
}

function GroupRow({ g }) {
  const insufficient = g.status === 'insufficient'
  let edgeCls = ''
  if (!insufficient && g.edge != null) {
    if (g.edge >= 5) edgeCls = 'sc-edge-strong'
    else if (g.edge <= 0) edgeCls = 'sc-edge-flat'
  }
  return (
    <div className="sc-row" role="row">
      <div className="sc-cell sc-group" role="rowheader">
        <span className="sc-gname">{g.label}</span>
        <span className="sc-grule">{g.rule}</span>
      </div>
      <Cell label="표본 수" className="sc-num">{g.n.toLocaleString()}</Cell>
      {insufficient ? (
        <div className="sc-cell sc-insuff" role="cell">
          표본 부족 <span className="sc-grule">(최소 {MIN_N}회 필요)</span>
        </div>
      ) : (
        <>
          <Cell label="적중률" className="sc-num">{fmtPct(g.hit_rate)}</Cell>
          <Cell label="기준선" className="sc-num">{fmtPct(g.baseline_rate)}</Cell>
          <Cell label="차이" className={`sc-num ${edgeCls}`}>{fmtEdge(g.edge)}</Cell>
          <Cell label="최근 20회" className="sc-num">{fmtRecent(g.recent)}</Cell>
        </>
      )}
      <Cell label="평균 결과" className="sc-num sc-avg">
        {fmtAvg(g.avg_outcome)}
        {g.avg_outcome != null && g.avg_outcome_label && (
          <span className="sc-grule" title={g.avg_outcome_label}>{g.avg_outcome_label}</span>
        )}
      </Cell>
    </div>
  )
}

function SignalSection({ s }) {
  const [open, setOpen] = useState(false)
  const notes = s.notes || []
  const p = s.period
  return (
    <section className="sc-section">
      <h3 className="mt-title">{s.label}</h3>
      <dl className="sc-def">
        <div><dt>신호가 말하는 것</dt><dd>{s.claim}</dd></div>
        <div><dt>맞았다의 기준</dt><dd>{s.outcome}</dd></div>
        {p && (
          <div>
            <dt>기간</dt>
            <dd>{p.start} ~ {p.end} · {p.trading_days.toLocaleString()}거래일</dd>
          </div>
        )}
      </dl>
      {s.error ? (
        <div className="narr-error" role="alert"><span>계산 실패: {s.error}</span></div>
      ) : (
        <div className="sc-table" role="table" aria-label={`${s.label} 그룹별 성적`}>
          <div className="sc-head" role="row">
            <div className="sc-cell" role="columnheader">그룹</div>
            <div className="sc-cell sc-num" role="columnheader">표본 수</div>
            <div className="sc-cell sc-num" role="columnheader">적중률</div>
            <div className="sc-cell sc-num" role="columnheader">기준선</div>
            <div className="sc-cell sc-num" role="columnheader">차이</div>
            <div className="sc-cell sc-num" role="columnheader">최근 20회</div>
            <div className="sc-cell sc-num" role="columnheader">평균 결과</div>
          </div>
          {s.groups.map((g) => <GroupRow key={g.key} g={g} />)}
        </div>
      )}
      {notes.length > 0 && (
        <div className="sc-notes">
          <button type="button" className="btn btn-link btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? '참고 사항 접기' : `참고 사항 ${notes.length}개`}
          </button>
          {open && <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
        </div>
      )}
    </section>
  )
}

export default function ScorecardPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setData(await fetchScorecard())
    } catch (e) {
      setError(e.message || '요청 실패')
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="narr-page sc-page m-font">
      <h2 className="mt-title">지표 성적표</h2>
      <p className="sc-lead">
        대시보드 신호가 실제로 맞았는지 같은 기준으로 채점한 기록입니다. 관찰용이며 매매 지시가 아닙니다.
      </p>
      {data?.generated_at && <p className="narr-meta">생성 {fmtGenerated(data.generated_at)}</p>}

      <div className="sc-guide">
        <b>읽는 법</b>
        <ul>
          <li>적중률은 기준선(신호와 무관하게 그 결과가 나온 비율)과 비교해서 봅니다. 차이가 클수록 신호가 보탠 몫입니다.</li>
          <li>표본이 {MIN_N}회 미만이면 &quot;표본 부족&quot;으로 두고 수치를 내지 않습니다.</li>
          <li>과거 성적은 그 시점까지의 데이터만으로 다시 계산한 값입니다.</li>
        </ul>
      </div>

      {error && (
        <div className="narr-error" role="alert">
          <span>불러오지 못했습니다: {error}</span>
          <button type="button" className="btn btn-sm" onClick={load}>다시 시도</button>
        </div>
      )}
      {!data && !error && <NarrSkeleton rows={3} />}
      {data && data.signals.length === 0 && <div className="mt-empty">표시할 데이터가 없습니다.</div>}
      {data && data.signals.map((s) => <SignalSection key={s.key} s={s} />)}
    </div>
  )
}
