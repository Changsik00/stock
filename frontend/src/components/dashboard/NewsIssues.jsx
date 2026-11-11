import { useCallback, useEffect, useState } from 'react'
import {
  getNarrativeIssues,
  hideNarrativeIssue,
  refreshNarratives,
  trackNarrativeIssue,
  unhideNarrativeIssue,
} from '../../api'
import Badge from '../Badge'
import { NarrSkeleton } from './NarrativeRow'
import EvidenceRow from './NarrativeEvidence'
import { CONFIDENCE, DIRECTION, alignSummary, fmtMdHm, fmtMil, fmtRate, isHttpUrl, signCls } from './narrativeShared'

// 오늘의 이슈 (PLAN.md §5.106) — 같은 사건을 다룬 기사를 이슈 하나로 묶고, 이슈가 지목한
// 수혜/피해 업종의 실제 수급·등락과 나란히 보여준다. 읽기에는 승인이 필요 없고,
// 사용자가 '추적하기'를 눌러야만 내러티브로 고정된다. 참고용, 매매 신호 아님.
// LLM·뉴스에서 온 문자열은 전부 React 텍스트로만 렌더한다(HTML 주입 금지).

const STALE_HOURS = 24
const SCHEDULE = '평일 08:40·12:10·16:40'

function SourceLink({ s }) {
  const meta = [s.source, s.published_at ? fmtMdHm(s.published_at) : null].filter(Boolean).join(' · ')
  return (
    <li>
      {isHttpUrl(s.url) ? (
        <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a>
      ) : (
        <span>{s.title}</span>
      )}
      {meta && <span className="narr-meta ni-src-meta"> {meta}</span>}
    </li>
  )
}

function IssueDetail({ it }) {
  const [more, setMore] = useState(false)
  const sources = it.sources || []
  const shown = sources.slice(0, 2)
  const rest = sources.slice(2)
  return (
    <>
      {it.facts?.length > 0 && (
        <ul className="ni-facts">
          {it.facts.slice(0, 3).map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
      {it.counter && (
        <div className="ni-counter"><b>반대 해석</b> {it.counter}</div>
      )}
      {sources.length > 0 && (
        <div className="ni-sources">
          <ul>
            {shown.map((s, i) => <SourceLink key={`${s.url}${i}`} s={s} />)}
            {more && rest.map((s, i) => <SourceLink key={`r${s.url}${i}`} s={s} />)}
          </ul>
          {rest.length > 0 && (
            <button type="button" className="btn btn-link btn-sm" aria-expanded={more} onClick={() => setMore(!more)}>
              {more ? '기사 접기' : `기사 ${rest.length}건 더 보기`}
            </button>
          )}
        </div>
      )}
    </>
  )
}

// 값이 전부 0이거나 없는 수급 대조 항목은 의미가 없어 숨긴다.
const hasValue = (e) =>
  e.source === 'sector_flow'
    ? [e.foreign_net, e.institution_net, e.individual_net].some((v) => v)
    : Boolean(e.change_rate)

// 칩(수혜/피해 용어)에 붙일 대표 evidence: role+term이 맞는 것 중 이름이 term과 같은 항목 우선, 없으면 첫 항목.
function pickRep(ev, role, term) {
  const m = ev.filter((e) => e.role === role && (e.term ?? e.name) === term)
  return m.find((e) => e.name === term) || m[0] || null
}

// 칩 안의 한 값: 등락률이 있으면 등락률, 없고 수급이면 외·기 합계(백만원 -> 억).
function ChipValue({ e }) {
  if (e.change_rate) {
    return <b className={signCls(e.change_rate)}>{e.change_rate > 0 ? '▲' : '▼'}{fmtRate(e.change_rate)}</b>
  }
  const net = (e.foreign_net || 0) + (e.institution_net || 0)
  return <b className={signCls(net)}>외·기 {net > 0 ? '▲' : net < 0 ? '▼' : ''}{fmtMil(net)}</b>
}

function TermChip({ role, term, rep }) {
  const verdict = rep?.verdict === 'aligned' ? '일치' : rep?.verdict === 'divergent' ? '불일치' : null
  return (
    <li className={`ni-chip${rep ? '' : ' ni-chip-none'}`}>
      <i>{role}</i> {term}
      {rep && <ChipValue e={rep} />}
      {verdict && <span className={`ni-chip-v ${rep.verdict === 'divergent' ? 'ni-chip-bad' : ''}`}>{verdict}</span>}
    </li>
  )
}

function IssueCard({ it, open, onToggle, busy, onTrack, onHide, onUnhide }) {
  const [evMore, setEvMore] = useState(false)
  const ev = (Array.isArray(it.evidence) ? it.evidence : []).filter(hasValue)
  const a = alignSummary(ev, it.alignment)
  const hidden = it.status === 'hidden'
  const tracked = it.status === 'tracked'
  const names = [
    ...(it.beneficiaries || []).map((t) => ({ t, role: '수혜', key: 'beneficiary' })),
    ...(it.victims || []).map((t) => ({ t, role: '피해', key: 'victim' })),
  ]
  return (
    <article className={`ni-card${hidden ? ' ni-hidden' : ''}`}>
      {/* 제목 줄 전체가 접기/펼치기 버튼(NarrativeRow 접근성 관례). 접힌 상태에서도 칩·해석은 보인다 */}
      <h3 className="ni-h">
        <button type="button" className="ni-head" aria-expanded={open} onClick={() => onToggle(it.id)}>
          <span className="ni-rank" aria-label={`${it.rank}번째 이슈`}>{it.rank}</span>
          <span className="ni-head-main">
            <span className="ni-tags">
              <span className={`narr-dir narr-dir-${it.direction}`}>{DIRECTION[it.direction] || '중립'}</span>
              {it.continuing && <span className="ni-tag">이어지는 이슈</span>}
              {tracked && <span className="ni-tag ni-tag-on">추적 중</span>}
              {hidden && <span className="ni-tag">숨김</span>}
              <span className="narr-meta">신뢰도 {CONFIDENCE[it.confidence] || '-'} · 기사 {it.article_count ?? (it.sources || []).length}건</span>
            </span>
            <span className="ni-title">{it.title}</span>
          </span>
          <span className="narr-chevron" aria-hidden="true" />
        </button>
      </h3>

      {names.length > 0 && (
        <div className="ni-evidence">
          <ul className="ni-chips" aria-label="수혜·피해 업종과 실제 수급·등락">
            {names.map((n, i) => (
              <TermChip key={`${n.role}${n.t}${i}`} role={n.role} term={n.t} rep={pickRep(ev, n.key, n.t)} />
            ))}
          </ul>
          {ev.length > 0 ? (
            <div className="ni-ev-sum">
              <span className="narr-meta">실제 수급·등락 대조 {a.label}</span>
              {open && (
                <button type="button" className="btn btn-link btn-sm" aria-expanded={evMore} onClick={() => setEvMore(!evMore)}>
                  {evMore ? '수급 대조 접기' : `수급 대조 자세히 ${ev.length}건`}
                </button>
              )}
            </div>
          ) : (
            <div className="narr-meta ni-noev">수급 매칭 없음</div>
          )}
          {open && evMore && (
            <div className="narr-evidence ni-evrows">
              {ev.map((e, i) => <EvidenceRow key={i} e={e} />)}
            </div>
          )}
        </div>
      )}

      {it.interpretation && <p className="ni-interp">{it.interpretation}</p>}

      {open && <IssueDetail it={it} />}

      {open && (
      <div className="narr-actions">
        {hidden ? (
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => onUnhide(it)}>숨김 해제</button>
        ) : (
          <>
            {tracked ? (
              <span className="narr-meta">추적 중인 내러티브로 고정됨</span>
            ) : (
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => onTrack(it)}>추적하기</button>
            )}
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onHide(it)}>숨기기</button>
          </>
        )}
      </div>
      )}
    </article>
  )
}

// onTracked: 추적하기로 내러티브가 늘었을 때 아래 목록을 다시 불러오게 하는 콜백.
export default function NewsIssues({ onTracked }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [batchAt, setBatchAt] = useState(null) // null = 최신 배치
  const [showHidden, setShowHidden] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState(null)
  const [now] = useState(() => Date.now())
  const [openIds, setOpenIds] = useState(() => new Set()) // 펼친 이슈 id (목록 재조회에도 유지)

  const load = useCallback(async () => {
    try {
      setData(await getNarrativeIssues({ batchAt, includeHidden: showHidden }))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [batchAt, showHidden])

  useEffect(() => {
    load()
  }, [load])

  const act = async (it, fn, after) => {
    setBusyId(it.id)
    try {
      await fn(it.id)
      await load()
      after?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  const toggleOpen = (id) =>
    setOpenIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshMsg(null)
    try {
      const r = await refreshNarratives()
      // 생성 일정 안내는 바로 위 고정 문구에 이미 있으므로 여기서는 수집 결과만 알린다.
      const parts = [`뉴스 수집 ${r.collected}건(신규 ${r.new_items})`]
      const failed = Object.keys(r.source_errors || {})
      if (failed.length) parts.push(`실패 소스: ${failed.join(', ')}`)
      setRefreshMsg(parts.join(' · '))
      await load()
    } catch (e) {
      setRefreshMsg(`수집 실패: ${e.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  const items = data?.items || []
  const batches = data?.batches || []
  const stats = data?.stats
  const ageH = data?.batch_at ? Math.floor((now - new Date(data.batch_at).getTime()) / 3600000) : null
  const stale = ageH != null && ageH >= STALE_HOURS
  const hiddenCount = items.filter((i) => i.status === 'hidden').length
  const ingest = data?.last_ingest
  const allOpen = items.length > 0 && items.every((i) => openIds.has(i.id))

  return (
    <>
      <div className="section-title mt-title ni-sec-title">
        오늘의 이슈 <span className="narr-meta">{data?.disclaimer || '참고용, 매매 신호 아님'}</span>
      </div>

      {/* 헤더 묶음: 안쪽은 좁게(8~12px), 아래 카드 목록과는 넓게(16px) 띄워 그룹 경계를 만든다 */}
      <div className="ni-header">
      {data?.batch_at && (
        <div className="ni-meta">
          <span className="narr-meta">{fmtMdHm(data.batch_at)} 생성</span>
          {stats && (
            <span className="narr-meta">
              {[
                stats.grouped != null && `기사 ${stats.grouped}건을 이슈 ${items.length - hiddenCount}개로 묶음`,
                stats.articles_in_window != null && `전체 ${stats.articles_in_window}건`,
              ].filter(Boolean).join(' · ')}
            </span>
          )}
          {stale && <Badge kind="warn">{ageH}시간 전 생성 — 자동 생성이 멈췄을 수 있음</Badge>}
        </div>
      )}

      <div className="ni-controls">
      <div className="narr-toolbar">
        <button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? '수집 중…' : '뉴스 수집'}
        </button>
        {batches.length > 1 && (
          <label className="ni-batch">
            <span className="narr-meta">배치</span>
            <select
              value={batchAt || data?.batch_at || ''}
              onChange={(e) => setBatchAt(e.target.value === batches[0].batch_at ? null : e.target.value)}
              aria-label="이슈 생성 배치 선택"
            >
              {batches.map((b) => (
                <option key={b.batch_at} value={b.batch_at}>{fmtMdHm(b.batch_at)} · 이슈 {b.count}개</option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className={`toggle-chip${showHidden ? ' active' : ''}`} aria-pressed={showHidden} onClick={() => setShowHidden(!showHidden)}>
          {showHidden ? `숨긴 이슈 포함 중${hiddenCount ? ` (${hiddenCount})` : ''}` : '숨긴 이슈 보기'}
        </button>
        {items.length > 0 && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setOpenIds(allOpen ? new Set() : new Set(items.map((i) => i.id)))}
          >
            {allOpen ? '모두 접기' : '모두 펼치기'}
          </button>
        )}
      </div>
      <div className="narr-meta ni-sched">이슈는 {SCHEDULE}에 자동 생성됩니다. ‘뉴스 수집’은 기사만 모읍니다.</div>
      {refreshMsg && <div className="narr-meta ni-sched" role="status">{refreshMsg}</div>}
      </div>
      </div>

      {error && (
        <div className="narr-error" role="alert">
          <span>이슈 처리 실패: {error}</span>
          <button type="button" className="btn btn-sm" onClick={load}>다시 시도</button>
        </div>
      )}
      {!data && !error && <NarrSkeleton rows={3} />}
      {data && items.length === 0 && (
        <div className="narr-empty">
          <div>아직 생성된 이슈가 없습니다.</div>
          <div className="narr-meta">{SCHEDULE}에 맥의 Claude Code가 최근 기사를 이슈로 묶어 올립니다(맥이 켜져 있을 때).</div>
          {ingest && (
            <div className="narr-meta">
              마지막 처리 {fmtMdHm(ingest.ran_at)}{ingest.status ? ` · ${ingest.status}` : ''}{ingest.message ? ` — ${ingest.message}` : ''}
            </div>
          )}
        </div>
      )}
      {items.length > 0 && (
        <div className="ni-list">
          {items.map((it) => (
            <IssueCard
              key={it.id}
              it={it}
              open={openIds.has(it.id)}
              onToggle={toggleOpen}
              busy={busyId === it.id}
              onTrack={(x) => act(x, trackNarrativeIssue, onTracked)}
              onHide={(x) => act(x, hideNarrativeIssue)}
              onUnhide={(x) => act(x, unhideNarrativeIssue)}
            />
          ))}
        </div>
      )}
    </>
  )
}
