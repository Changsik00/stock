import { useCallback, useEffect, useState } from 'react'
import {
  approveNarrativeProposal,
  dismissNarrativeProposal,
  getNarrativeNews,
  getNarrativeProposals,
  refreshNarratives,
} from '../../api'
import Badge from '../Badge'
import Modal from '../Modal'
import NarrativeRow from './NarrativeRow'

// 뉴스 기반 내러티브 제안 피드 (PLAN.md §5.90-2) — 시스템(LLM)이 제안하고 사용자가 읽고
// 승인/수정 후 승인/무시한다. 자동 확정 없음. 참고용, 매매 신호 아님.
// 한국 관행: 강세=빨강(.narr-dir-bullish), 약세=파랑(.narr-dir-bearish).

const DIRECTION = { bullish: '강세', bearish: '약세', neutral: '중립' }
const CONFIDENCE = { low: '낮음', mid: '보통', high: '높음' }
const splitList = (s) =>
  s
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean)
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-')

function EditApproveForm({ p, onDone, onCancel }) {
  const [f, setF] = useState({
    title: p.title,
    summary: p.summary || '',
    direction: p.direction,
    confidence: p.confidence,
    beneficiaries: p.beneficiaries.join(', '),
    victims: p.victims.join(', '),
  })
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault()
    if (!f.title.trim()) return setErr('제목은 필수입니다')
    setBusy(true)
    try {
      await approveNarrativeProposal(p.id, {
        title: f.title.trim(),
        summary: f.summary,
        direction: f.direction,
        confidence: f.confidence,
        beneficiaries: splitList(f.beneficiaries),
        victims: splitList(f.victims),
      })
      onDone()
    } catch (e2) {
      setErr(e2.message)
      setBusy(false)
    }
  }
  return (
    <form className="narr-form" onSubmit={submit}>
      {err && <div className="state">승인 실패: {err}</div>}
      <label>제목<input value={f.title} onChange={set('title')} maxLength={200} /></label>
      <label>요지<textarea value={f.summary} onChange={set('summary')} rows={6} /></label>
      <div className="narr-form-row">
        <label>방향
          <select value={f.direction} onChange={set('direction')}>
            {Object.entries(DIRECTION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>신뢰도
          <select value={f.confidence} onChange={set('confidence')}>
            {Object.entries(CONFIDENCE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <label>수혜 섹터/종목 (쉼표 구분)<input value={f.beneficiaries} onChange={set('beneficiaries')} /></label>
      <label>피해 섹터/종목 (쉼표 구분)<input value={f.victims} onChange={set('victims')} /></label>
      <div className="narr-meta">승인하면 활성 내러티브로 저장되며 만료일은 30일 뒤로 설정됩니다. 근거 기사 링크는 요지 끝에 보존됩니다.</div>
      <div className="narr-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? '저장 중…' : '수정 후 승인'}</button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>취소</button>
      </div>
    </form>
  )
}

// 승인/수정 후 승인/무시는 펼친 본문 안에만 둔다 — 접힌 헤더에 빠른 버튼을 두면 스크롤·클릭 중
// 오조작으로 내러티브가 바로 저장/폐기될 수 있어(되돌리기 번거로움), 내용을 읽은 뒤에만 노출한다.
function ProposalCard({ p, onApprove, onEdit, onDismiss, busy }) {
  return (
    <NarrativeRow
      className="narr-proposal"
      title={p.title}
      titleBadge={<span className={`narr-dir narr-dir-${p.direction}`}>{DIRECTION[p.direction]}</span>}
      meta={<span className="narr-meta narr-row-meta">신뢰도 {CONFIDENCE[p.confidence]} · {fmtTime(p.created_at)} 제안</span>}
    >
      {p.summary && <div className="narr-summary">{p.summary}</div>}
      <div className="narr-chips">
        {p.beneficiaries.map((t) => (
          <Badge key={`b${t}`} kind="info" title="수혜">▲ {t}</Badge>
        ))}
        {p.victims.map((t) => (
          <Badge key={`v${t}`} kind="etf" title="피해">▼ {t}</Badge>
        ))}
      </div>
      {p.sources.length > 0 && (
        <ul className="narr-sources">
          {p.sources.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a>
            </li>
          ))}
        </ul>
      )}
      <div className="narr-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onApprove(p)}>승인</button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => onEdit(p)}>수정 후 승인</button>
        <button type="button" className="btn btn-danger" disabled={busy} onClick={() => onDismiss(p)}>무시</button>
      </div>
    </NarrativeRow>
  )
}

function RecentNews() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!open) return
    getNarrativeNews(60).then((d) => setItems(d.items)).catch((e) => setErr(e.message))
  }, [open])
  return (
    <div className="narr-news">
      <button type="button" className="btn btn-link btn-sm narr-link" onClick={() => setOpen(!open)}>
        {open ? '최근 수집 뉴스 접기' : '최근 수집 뉴스 보기'}
      </button>
      {open && err && <div className="state">뉴스 조회 실패: {err}</div>}
      {open && items && items.length === 0 && <div className="narr-meta">수집된 뉴스가 없습니다. ‘뉴스 수집’을 눌러 보세요.</div>}
      {open && items && (
        <ul className="narr-news-list">
          {items.map((n) => (
            <li key={n.id}>
              <span className="narr-meta">{fmtTime(n.published_at || n.fetched_at)} · {n.source}</span>{' '}
              <a href={n.url} target="_blank" rel="noopener noreferrer">{n.title}</a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// onChanged: 승인으로 활성 내러티브가 늘었을 때 아래 목록을 다시 불러오게 하는 콜백.
export default function NarrativeProposals({ onChanged }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState(null)
  const [editing, setEditing] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await getNarrativeProposals('pending'))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshMsg(null)
    try {
      const r = await refreshNarratives()
      const parts = [`수집 ${r.collected}건(신규 ${r.new_items})`]
      if (r.no_key) parts.push('뉴스만 수집했습니다 — 제안은 Claude Code가 올립니다')
      else if (r.llm_error) parts.push(`LLM 오류: ${r.llm_error}`)
      else parts.push(`새 제안 ${r.proposals_created}건`)
      const failed = Object.keys(r.source_errors || {})
      if (failed.length) parts.push(`실패 소스: ${failed.join(', ')}`)
      setRefreshMsg(parts.join(' · '))
      await load()
    } catch (e) {
      setRefreshMsg(`갱신 실패: ${e.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  const act = async (p, fn) => {
    setBusyId(p.id)
    try {
      await fn()
      await load()
      onChanged?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }
  const handleApprove = (p) => act(p, () => approveNarrativeProposal(p.id))
  const handleDismiss = (p) => act(p, () => dismissNarrativeProposal(p.id))

  const last = data?.last_refresh
  const ext = data?.last_external
  return (
    <>
      <div className="section-title">
        새 제안 <span className="narr-meta">뉴스 기반 · 승인해야만 내러티브로 저장됩니다 · {data?.disclaimer || '참고용, 매매 신호 아님'}</span>
      </div>
      <div className="narr-toolbar">
        <button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={refreshing || data?.running}>
          {refreshing || data?.running ? '수집 중…' : '뉴스 수집'}
        </button>
        <span className="narr-meta">
          {last ? `마지막 갱신 ${fmtTime(last.ran_at)}${last.message ? ` — ${last.message}` : ''}` : '아직 갱신 기록이 없습니다 (평일 08:30·12:00·16:30 자동 수집)'}
        </span>
      </div>
      <div className="narr-meta narr-external">
        마지막 Claude Code 요약: {ext ? `${fmtTime(ext.ran_at)}${ext.message ? ` — ${ext.message}` : ''}` : '아직 없음'}
      </div>
      {data && !data.llm_configured && (
        <div className="narr-meta narr-nokey">
          ‘뉴스 수집’ 버튼은 뉴스만 모읍니다. 제안은 맥의 Claude Code가 평일 하루 3회(08:40·12:10·16:40, 맥이 켜져 있을 때) 요약해 올립니다.
        </div>
      )}
      {refreshMsg && <div className="narr-meta narr-refresh-msg">{refreshMsg}</div>}
      {error && <div className="state">제안 처리 실패: {error}</div>}
      {data && data.items.length > 0 && <div className="narr-meta narr-summary-line">제안 대기 {data.items.length}건</div>}
      {data && data.items.length === 0 && <div className="narr-meta">대기 중인 새 제안이 없습니다.</div>}
      <div className="narr-list">
        {data?.items.map((p) => (
          <ProposalCard key={p.id} p={p} busy={busyId === p.id} onApprove={handleApprove} onEdit={setEditing} onDismiss={handleDismiss} />
        ))}
      </div>
      <RecentNews />
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title="수정 후 승인" maxWidth={560}>
        {editing && (
          <EditApproveForm
            p={editing}
            onCancel={() => setEditing(null)}
            onDone={() => {
              setEditing(null)
              load()
              onChanged?.()
            }}
          />
        )}
      </Modal>
    </>
  )
}
