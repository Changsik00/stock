// 모바일형 시범 타일(DESIGN.md '모바일형 방향') — 라벨(작게) → 핵심 숫자(가장 크게) + 같은 줄에 ▲▼ 등락 → 보조 정보.
// 기존 KpiTile은 그대로 두고(다른 타일 영향 없음) 지수·투자자별 수급 요약 타일에만 쓴다.
// 상승=빨강/하락=파랑은 방향 표시(▲▼ 화살표 병기)에만 쓰고, 핵심 숫자 자체는 기본 글자색을 유지한다.

// 방향 → 화살표/클래스/스크린리더 문구. 0·null은 중립(화살표 없음).
const DIR = {
  up: { arrow: '▲', cls: 'up', sr: '상승' },
  down: { arrow: '▼', cls: 'down', sr: '하락' },
}

export function Delta({ dir, children }) {
  const d = DIR[dir]
  return (
    <span className={`mt-delta ${d ? d.cls : 'flat'}`}>
      {d && (
        <>
          <span aria-hidden="true">{d.arrow}</span>
          <span className="mt-sr">{d.sr}</span>
        </>
      )}
      {children}
    </span>
  )
}

// 은은한 스켈레톤(기존 '…' 대체). 움직임은 prefers-reduced-motion에서 꺼진다(CSS).
export function Skel({ w = 64, h = 14 }) {
  return <span className="mt-skel" style={{ width: w, height: h }} aria-hidden="true" />
}

// value: 핵심 숫자 문자열. loading이면 스켈레톤, value가 없으면 '데이터 없음' 빈 상태.
export default function MetricTile({ label, dot, value, dir, delta, sub, loading, onClick, title }) {
  const empty = !loading && (value === null || value === undefined || value === '')
  return (
    <button type="button" className="mt" onClick={onClick} title={title} aria-busy={loading || undefined}>
      <span className="mt-label">
        {dot && <span className="mt-dot" style={{ background: dot }} />}
        {label}
      </span>
      {loading ? (
        <>
          <Skel w={96} h={26} />
          <Skel w={56} h={14} />
        </>
      ) : empty ? (
        <span className="mt-empty">데이터 없음</span>
      ) : (
        <>
          <span className="mt-main">
            <span className="mt-value">{value}</span>
            {delta !== undefined && <Delta dir={dir}>{delta}</Delta>}
          </span>
          {sub && <span className="mt-sub">{sub}</span>}
        </>
      )}
    </button>
  )
}

// 순매수 금액 표기 통일(시범): 입력은 백만원. 1조(10,000억) 이상은 'N.NN조', 미만은 정수 '억'.
// 부호는 U+2212(−)로 쓰고 숫자는 tabular-nums라 자릿수가 맞는다. 화살표(▲▼)는 호출부가 붙인다.
const intFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const joFmt2 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export function heroEok(million) {
  if (typeof million !== 'number') return null
  const eok = Math.abs(million) / 100
  const sign = million < 0 ? '\u2212' : million > 0 ? '+' : ''
  return eok >= 10000 ? `${sign}${joFmt2.format(eok / 10000)}조` : `${sign}${intFmt.format(Math.round(eok))}억`
}
