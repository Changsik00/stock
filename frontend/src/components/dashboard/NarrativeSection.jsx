import { useCallback, useEffect, useState } from 'react'
import { createNarrative, deleteNarrative, getNarratives, updateNarrative } from '../../api'
import Badge from '../Badge'
import Modal from '../Modal'
import NarrativeRow, { NarrSkeleton } from './NarrativeRow'
import EvidenceRow from './NarrativeEvidence'
import { CONFIDENCE, DIRECTION, alignSummary } from './narrativeShared'

// 추적 중인 내러티브 (PLAN.md §5.90-1, 5.106) — 직접 기록하거나 오늘의 이슈에서 추적으로 고정한 서사를 실제 섹터
// 수급/등락과 나란히 보여준다. 참고용이며 매매 신호가 아니다(자동 생성·확정 없음).
// 한국 관행: 상승/순매수(+)=빨강(.up), 하락/순매도(-)=파랑(.down).

function NarrativeCard({ n, onEdit, onDelete, defaultOpen }) {
  const a = alignSummary(n.evidence, n.alignment)
  return (
    <NarrativeRow
      defaultOpen={defaultOpen}
      className={n.active ? '' : 'narr-inactive'}
      title={n.title}
      titleBadge={<span className={`narr-dir narr-dir-${n.direction}`}>{DIRECTION[n.direction]}</span>}
      meta={
        <span className="narr-meta narr-row-meta">
          신뢰도 {CONFIDENCE[n.confidence]}
          {n.expires_on && ` · ~${n.expires_on.slice(5)}`}
          {!n.active && ' · 비활성'}
        </span>
      }
      badge={<span className={`narr-ev-verdict ${a.cls}`}>{a.label}</span>}
    >
      {n.summary && <div className="narr-summary">{n.summary}</div>}
      <div className="narr-chips">
        {n.beneficiaries.map((t) => (
          <Badge key={`b${t}`} kind="info" title="수혜">
            ▲ {t}
          </Badge>
        ))}
        {n.victims.map((t) => (
          <Badge key={`v${t}`} kind="etf" title="피해">
            ▼ {t}
          </Badge>
        ))}
      </div>
      {n.evidence.length > 0 && (
        <div className="narr-evidence">
          {n.evidence.map((e, i) => <EvidenceRow key={i} e={e} />)}
        </div>
      )}
      <div className="narr-actions">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => onEdit(n)}>수정</button>
        <button type="button" className="btn btn-danger btn-sm" onClick={() => onDelete(n)}>삭제</button>
      </div>
    </NarrativeRow>
  )
}

const EMPTY = {
  title: '',
  summary: '',
  direction: 'neutral',
  beneficiaries: '',
  victims: '',
  confidence: 'mid',
  expires_on: '',
  active: true,
}
const splitList = (s) =>
  s
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean)

function NarrativeForm({ initial, onSaved, onCancel }) {
  const [f, setF] = useState(
    initial
      ? { ...initial, summary: initial.summary || '', expires_on: initial.expires_on || '', beneficiaries: initial.beneficiaries.join(', '), victims: initial.victims.join(', ') }
      : EMPTY
  )
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    if (!f.title.trim()) return setErr('제목은 필수입니다')
    const body = {
      title: f.title.trim(),
      summary: f.summary.trim() || null,
      direction: f.direction,
      beneficiaries: splitList(f.beneficiaries),
      victims: splitList(f.victims),
      confidence: f.confidence,
      expires_on: f.expires_on || null,
      active: f.active,
    }
    setBusy(true)
    try {
      if (initial) await updateNarrative(initial.id, body)
      else await createNarrative(body)
      onSaved()
    } catch (e2) {
      setErr(e2.message)
      setBusy(false)
    }
  }

  return (
    <form className="narr-form" onSubmit={submit}>
      {err && <div className="state">저장 실패: {err}</div>}
      <label>제목<input value={f.title} onChange={set('title')} maxLength={200} /></label>
      <label>요지<textarea value={f.summary} onChange={set('summary')} rows={3} /></label>
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
        <label>만료일<input type="date" value={f.expires_on} onChange={set('expires_on')} /></label>
      </div>
      <label>수혜 섹터/종목 (쉼표 구분)<input value={f.beneficiaries} onChange={set('beneficiaries')} placeholder="예: 방산, 조선" /></label>
      <label>피해 섹터/종목 (쉼표 구분)<input value={f.victims} onChange={set('victims')} placeholder="예: 반도체" /></label>
      <label className="narr-check"><input type="checkbox" checked={f.active} onChange={set('active')} /> 활성</label>
      <div className="narr-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? '저장 중…' : '저장'}</button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>취소</button>
      </div>
    </form>
  )
}

export default function NarrativeSection({ reloadKey = 0 }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [showInactive, setShowInactive] = useState(false)
  const [editing, setEditing] = useState(null) // null | 'new' | narrative

  const load = useCallback(async () => {
    try {
      setData(await getNarratives(showInactive))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [showInactive])

  // reloadKey: 이슈 추적 후 상위에서 올려 활성 목록을 다시 불러오게 한다.
  useEffect(() => {
    load()
  }, [load, reloadKey])

  const handleDelete = async (n) => {
    if (!window.confirm(`"${n.title}" 내러티브를 삭제할까요?`)) return
    try {
      await deleteNarrative(n.id)
      await load()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <>
      <div className="section-title mt-title">
        추적 중인 내러티브{' '}
        <span className="narr-meta">{data?.disclaimer || '참고용, 매매 신호 아님'}</span>
      </div>
      <div className="narr-toolbar">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing('new')}>+ 내러티브 추가</button>
        <label className="narr-check">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> 비활성/만료 포함
        </label>
        {data && <span className="narr-meta narr-count">활성 {data.items.filter((n) => n.active).length}건{showInactive ? ` · 전체 ${data.items.length}건` : ''}</span>}
      </div>
      {error && (
        <div className="narr-error" role="alert">
          <span>내러티브 처리 실패: {error}</span>
          <button type="button" className="btn btn-sm" onClick={load}>다시 시도</button>
        </div>
      )}
      {!data && !error && <NarrSkeleton rows={2} />}
      {data && data.items.length === 0 && (
        <div className="narr-empty">기록된 내러티브가 없습니다. 직접 추가해 실제 수급과 대조해 보세요.</div>
      )}
      {data && data.items.length > 0 && (
        <div className="narr-list">
          {data.items.map((n) => (
            <NarrativeCard key={n.id} n={n} defaultOpen={data.items.length <= 3} onEdit={setEditing} onDelete={handleDelete} />
          ))}
        </div>
      )}
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title={editing === 'new' ? '내러티브 추가' : '내러티브 수정'} maxWidth={560}>
        {editing && (
          <NarrativeForm
            initial={editing === 'new' ? null : editing}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null)
              load()
            }}
          />
        )}
      </Modal>
    </>
  )
}
