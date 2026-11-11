// 내러티브 탭 공용 — 방향/신뢰도 라벨, 수급 대조(evidence) 표시, 시각·URL 헬퍼.
// NarrativeSection(추적 중 내러티브)과 NewsIssues(오늘의 이슈)가 함께 쓴다.
// 한국 관행: 강세/순매수(+)=빨강(.up), 약세/순매도(-)=파랑(.down). 참고용, 매매 신호 아님.

export const DIRECTION = { bullish: '강세', bearish: '약세', neutral: '중립' }
export const CONFIDENCE = { low: '낮음', mid: '보통', high: '높음' }
export const ALIGN = {
  aligned: { label: '수급 일치', cls: 'narr-aligned' },
  divergent: { label: '수급 불일치', cls: 'narr-divergent' },
  mixed: { label: '일부 불일치', cls: 'narr-mixed' },
  unknown: { label: '판정 불가', cls: 'narr-none' },
  none: { label: '매칭 섹터 없음', cls: 'narr-none' },
}

const numFmt = new Intl.NumberFormat('ko-KR')
export const signCls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
// 방향은 ▲▼(CSS ::before, .up/.down)로 표시하므로 값은 부호 없는 절댓값으로 쓴다(숫자는 억 정수 · % 소수 2자리로 통일).
export const fmtMil = (v) => (v == null ? '-' : `${numFmt.format(Math.abs(Math.round(v / 100)))}억`) // 백만원 -> 억원
export const fmtRate = (v) => (v == null ? '-' : `${Math.abs(v).toFixed(2)}%`)

export const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'

// "11-12 08:42" 형식(좁은 곳용)
export function fmtMdHm(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 외부(LLM·뉴스)에서 온 URL은 http(s)만 링크로 허용한다.
export const isHttpUrl = (u) => typeof u === 'string' && /^https?:\/\//i.test(u)

// 수급 일치 요약 배지: 일치/불일치 건수를 한 덩어리로 (판정 가능한 evidence만 집계).
// alignment가 없으면(이슈 응답) evidence에서 같은 규칙으로 유도한다.
export function alignSummary(evidence, alignment) {
  const ev = Array.isArray(evidence) ? evidence : []
  const cnt = (v) => ev.filter((e) => e.verdict === v).length
  const ok = cnt('aligned')
  const bad = cnt('divergent')
  const key = alignment || (ev.length === 0 ? 'none' : ok + bad === 0 ? 'unknown' : bad === 0 ? 'aligned' : ok === 0 ? 'divergent' : 'mixed')
  const a = ALIGN[key] || ALIGN.none
  if (ok + bad === 0) return { label: a.label, cls: a.cls }
  return { label: `일치 ${ok}${bad ? ` · 불일치 ${bad}` : ''}`, cls: a.cls }
}
