// 시장 등락 종목수(breadth) 배지 — "코스피 512↑ 40— 380↓" + 상승/하락 비율 가로 막대
// (PLAN.md §3.5/§4.6 3.6-2).
//
// 순수 컴포넌트다 — 데이터 페칭이나 라우팅에 관여하지 않고 props로 받은 값만 그린다.
// 통합(useEffect로 GET /api/markets/breadth/live 또는 /{market}/breadth 호출, MarketPage
// 배치)은 이후 단계 작업이라 이 파일은 그 호출부를 만들지 않는다(이번 작업 지시사항 —
// MarketPage.jsx/api.js/index.css는 병렬 작업 중이라 건드리지 않음).
//
// props 스키마(케밥이 아니라 camelCase — 백엔드 GET /api/markets/breadth/live·
// /{market}/breadth는 snake_case(adv/dec/flat/limit_up/limit_down)로 응답하므로, 통합
// 시 호출부가 { adv, dec, flat, limitUp: limit_up, limitDown: limit_down }로 변환해서
// 넘겨야 한다):
//
//   <BreadthBadge
//     date="2026-07-18"
//     kospi={{ adv: 384, flat: 40, dec: 488, limitUp: 6, limitDown: 0 }}
//     kosdaq={{ adv: 501, flat: 56, dec: 1182, limitUp: 11, limitDown: 1 }}
//   />
//
// kospi/kosdaq 각각은 값이 없으면(아직 못 불러왔거나 그 시장만 실패) 생략 가능 — 그
// 시장 행은 건너뛴다. 값이 전혀 없으면(둘 다 undefined) "데이터 없음" 상태를 보여준다.
//
// 색상 규칙(§5.4 한국 증시 관행): 상승 = 빨강(--up), 하락 = 파랑(--down), 보합은
// 중립(--text-muted). index.css를 건드리지 않고도 전역 테마(라이트/다크)를 그대로
// 타도록 이미 :root에 정의된 CSS 커스텀 프로퍼티(--up/--down/--text-*/--border)를
// 재사용한다 — 이 파일 안의 <style> 블록은 이 컴포넌트 전용 클래스(breadth-badge*)만
// 정의하고 전역 규칙은 추가하지 않는다.

const MARKET_LABEL = { kospi: '코스피', kosdaq: '코스닥' }

const countFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })

function fmt(n) {
  return n === null || n === undefined ? '-' : countFmt.format(n)
}

// 상승 쪽 합 = 상승(adv) + 상한(limitUp), 하락 쪽 합 = 하락(dec) + 하한(limitDown).
// 네이버 소스는 상한/하한을 상승/하락과 별개 버킷으로 준다(둘을 더해야 시장 전체
// 종목수에 맞음 — clients/naver_breadth.py 모듈 docstring의 실측 sanity 참고).
function splitTotals(row) {
  const adv = row?.adv ?? 0
  const dec = row?.dec ?? 0
  const flat = row?.flat ?? 0
  const limitUp = row?.limitUp ?? 0
  const limitDown = row?.limitDown ?? 0
  const up = adv + limitUp
  const down = dec + limitDown
  const total = up + flat + down
  return { up, flat, down, total }
}

function BreadthRow({ market, row }) {
  if (!row) return null
  const label = MARKET_LABEL[market] || market
  const { up, flat, down, total } = splitTotals(row)
  const upPct = total > 0 ? (up / total) * 100 : 0
  const flatPct = total > 0 ? (flat / total) * 100 : 0
  const downPct = total > 0 ? (down / total) * 100 : 0

  return (
    <div className="breadth-badge-row">
      <div className="breadth-badge-line">
        <span className="breadth-badge-market">{label}</span>
        <span className="breadth-badge-counts">
          <span className="breadth-badge-up">{fmt(row.adv)}↑</span>{' '}
          <span className="breadth-badge-flat">{fmt(row.flat)}—</span>{' '}
          <span className="breadth-badge-down">{fmt(row.dec)}↓</span>
        </span>
        {(row.limitUp ?? 0) > 0 || (row.limitDown ?? 0) > 0 ? (
          <span className="breadth-badge-limit">
            {(row.limitUp ?? 0) > 0 && (
              <span className="breadth-badge-up">상한 {fmt(row.limitUp)}</span>
            )}
            {(row.limitUp ?? 0) > 0 && (row.limitDown ?? 0) > 0 && ' · '}
            {(row.limitDown ?? 0) > 0 && (
              <span className="breadth-badge-down">하한 {fmt(row.limitDown)}</span>
            )}
          </span>
        ) : null}
      </div>
      <div
        className="breadth-badge-bar"
        role="img"
        aria-label={`${label} 상승 ${fmt(up)}종목, 보합 ${fmt(flat)}종목, 하락 ${fmt(down)}종목`}
      >
        {upPct > 0 && <div className="breadth-badge-bar-up" style={{ width: `${upPct}%` }} />}
        {flatPct > 0 && <div className="breadth-badge-bar-flat" style={{ width: `${flatPct}%` }} />}
        {downPct > 0 && <div className="breadth-badge-bar-down" style={{ width: `${downPct}%` }} />}
      </div>
    </div>
  )
}

export default function BreadthBadge({ kospi, kosdaq, date }) {
  const hasAny = Boolean(kospi) || Boolean(kosdaq)

  return (
    <div className="breadth-badge">
      <style>{`
        .breadth-badge {
          display: flex;
          flex-direction: column;
          gap: 10px;
          font-family: inherit;
        }
        .breadth-badge-date {
          font-size: 11px;
          color: var(--text-muted);
        }
        .breadth-badge-row {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .breadth-badge-line {
          display: flex;
          align-items: baseline;
          flex-wrap: wrap;
          gap: 8px;
          font-size: 13px;
        }
        .breadth-badge-market {
          font-weight: 600;
          color: var(--text-primary);
          min-width: 44px;
        }
        .breadth-badge-counts {
          font-variant-numeric: tabular-nums;
          color: var(--text-secondary);
        }
        .breadth-badge-limit {
          font-size: 11px;
          color: var(--text-muted);
          font-variant-numeric: tabular-nums;
        }
        .breadth-badge-up { color: var(--up); font-weight: 600; }
        .breadth-badge-down { color: var(--down); font-weight: 600; }
        .breadth-badge-flat { color: var(--text-muted); }
        .breadth-badge-bar {
          display: flex;
          width: 100%;
          height: 6px;
          border-radius: 999px;
          overflow: hidden;
          background: var(--border);
        }
        .breadth-badge-bar-up { background: var(--up); height: 100%; }
        .breadth-badge-bar-down { background: var(--down); height: 100%; }
        .breadth-badge-bar-flat { background: var(--text-muted); opacity: 0.5; height: 100%; }
        .breadth-badge-empty {
          font-size: 13px;
          color: var(--text-muted);
        }
      `}</style>

      {date && <div className="breadth-badge-date">{date} 기준</div>}

      {!hasAny && <div className="breadth-badge-empty">등락 종목수 데이터가 없습니다.</div>}

      <BreadthRow market="kospi" row={kospi} />
      <BreadthRow market="kosdaq" row={kosdaq} />
    </div>
  )
}
