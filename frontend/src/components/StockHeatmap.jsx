import { changeRateMixStrength, colorForChangeRate } from './GroupTreemap'

// 종목 히트맵 — GroupTreemap(박스 크기=거래대금)과 달리 모든 셀이 동일 크기인 CSS
// grid, 색(등락률)만 다르다 (사용자 요청: "트리맵이 아니라 히트맵으로"). recharts
// 없이 순수 CSS/div로 그린다.
//
// 순수 컴포넌트다 — 데이터 페칭 없이 props만 받는다:
//   items: [{ code, name, change_rate, value }, ...]
//   onCellClick?: (code, name) => void   (생략하면 클릭 불가, 순수 시각화)
//   minCellWidth: 셀 최소 폭(px, 기본 100) — 좁게 잡을수록 한 줄에 더 많은 셀이
//     들어간다(호출부가 20종목처럼 항목 수가 많을 때 좁혀서 쓸 수 있다).
//
// 색 스케일은 GroupTreemap.jsx가 export하는 colorForChangeRate/changeRateMixStrength를
// 그대로 재사용한다(재구현 금지 — 두 컴포넌트 간 색 스케일이 항상 일치해야 한다).
export default function StockHeatmap({ items, onCellClick, minCellWidth = 100 }) {
  if (!items || items.length === 0) {
    return <div className="state">표시할 데이터가 없습니다.</div>
  }

  const clickable = typeof onCellClick === 'function'

  return (
    <div
      className="stock-heatmap"
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fill, minmax(${minCellWidth}px, 1fr))`,
        gap: 4,
      }}
    >
      {items.map((item) => {
        const rate = item.change_rate
        // colorForChangeRate와 동일한 강도 판정으로 라벨 색을 정한다(GroupTreemap.jsx의
        // labelColorFor와 동일한 근사 규칙 — 보간 강도가 높으면(진한 배경) 흰 글자,
        // 낮으면(연한 배경) 테마 기본 글자색).
        const textColor = changeRateMixStrength(rate) >= 0.35 ? '#ffffff' : 'var(--text-primary)'
        const sign = typeof rate === 'number' && rate > 0 ? '+' : ''
        const rateText = typeof rate === 'number' && !Number.isNaN(rate) ? `${sign}${rate.toFixed(2)}%` : '-'
        const Tag = clickable ? 'button' : 'div'

        return (
          <Tag
            key={item.code}
            type={clickable ? 'button' : undefined}
            className="stock-heatmap-cell"
            onClick={clickable ? () => onCellClick(item.code, item.name) : undefined}
            style={{
              background: colorForChangeRate(rate),
              color: textColor,
              cursor: clickable ? 'pointer' : 'default',
              border: '1px solid var(--page)',
              borderRadius: 6,
              padding: '8px 6px',
              minHeight: 56,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
              gap: 2,
              textAlign: 'center',
              font: 'inherit',
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>
              {item.name || item.code}
            </span>
            <span style={{ fontSize: 12 }}>{rateText}</span>
          </Tag>
        )
      })}
    </div>
  )
}
