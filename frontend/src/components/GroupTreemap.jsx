import { ResponsiveContainer, Tooltip, Treemap } from 'recharts'

// 업종/테마 강약 트리맵 (PLAN.md §4.6 3.6-3, §6 Phase 3.6-3).
//
// 순수 컴포넌트다 — 데이터 페칭 없이 props만 받는다:
//   items: [{ name, change_rate, value, market_sum }, ...]   (GET /api/groups 응답 그대로)
//   sizeBy: 'value' | 'market_sum'                            (박스 크기 기준, 기본 'value')
//
// **현재 데이터 한계**: collectors/group_snapshot.py가 쓰는 네이버 sise_group.naver
// 목록 페이지에는 그룹별 거래대금·시가총액 컬럼이 없어(clients/naver_group.py 모듈
// docstring 참고) value/market_sum이 지금은 항상 null이다. 그래서 이 컴포넌트는
// sizeBy로 고른 값이 (일부라도, 혹은 전부) 없는 항목에는 1로 폴백해 "동일 크기"로
// 표시한다 — 그래야 데이터가 비어 있어도 색(등락률)만으로 트리맵이 의미를 갖는다.
// 실제 거래대금/시총이 채워지면(추후 그룹 상세 페이지 합산 등) 자동으로 크기 차이가
// 반영된다.
function sizeValueOf(item, sizeBy) {
  const raw = item?.[sizeBy]
  return typeof raw === 'number' && raw > 0 ? raw : 1
}

// 색 스케일: 등락률(%)을 -3%~+3%로 클램프한 뒤, 0%=중립(--surface, 라이트/다크 각각
// 카드 배경색)에서 상승은 --up(빨강), 하락은 --down(파랑) 쪽으로 선형 보간한다.
// recharts Treemap은 실제 DOM/SVG를 그리므로(캔버스가 아님) CSS 변수를 그대로 쓸 수
// 있고, CSS `color-mix()`로 보간하면 라이트/다크 전환 시 이 컴포넌트가 다시 렌더링될
// 필요 없이 브라우저가 알아서 다시 그린다(index.css의 --up/--down/--surface가 이미
// 두 테마 모두 정의돼 있음 — CandleChart.jsx처럼 getComputedStyle로 직접 읽어와야
// 하는 캔버스 기반 차트와 달리 이 컴포넌트는 그럴 필요가 없다).
const CLAMP_PERCENT = 3

export function changeRateMixStrength(changeRate) {
  if (typeof changeRate !== 'number' || Number.isNaN(changeRate)) return 0
  const clamped = Math.max(-CLAMP_PERCENT, Math.min(CLAMP_PERCENT, changeRate))
  return Math.abs(clamped) / CLAMP_PERCENT // 0..1
}

export function colorForChangeRate(changeRate) {
  const t = changeRateMixStrength(changeRate)
  const pct = Math.round(t * 100)
  if (pct === 0) return 'var(--surface)'
  const base = typeof changeRate === 'number' && changeRate < 0 ? '--down' : '--up'
  return `color-mix(in srgb, var(${base}) ${pct}%, var(--surface))`
}

// 보간 강도가 높을수록(진한 빨강/파랑) 흰 글자가 잘 읽히고, 낮을수록(연한 배경, 거의
// --surface) 테마 기본 글자색이 더 잘 읽힌다 — CSS 변수의 실제 색값을 JS로 읽지 않는
// 순수 컴포넌트 제약 안에서 택한 근사 규칙(실제 럭스 대비가 아니라 보간 비율 기준).
function labelColorFor(changeRate) {
  return changeRateMixStrength(changeRate) >= 0.35 ? '#ffffff' : 'var(--text-primary)'
}

function rateLabel(changeRate) {
  if (typeof changeRate !== 'number' || Number.isNaN(changeRate)) return '-'
  const sign = changeRate > 0 ? '+' : ''
  return `${sign}${changeRate.toFixed(2)}%`
}

const eokFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

// value/market_sum은 백만원 단위로 온다(models.py GroupSnapshot docstring) — 기존
// FlowRankTable.jsx의 억원 환산 관례(/100)를 그대로 따른다.
function eokLabel(million) {
  if (typeof million !== 'number') return '-'
  return `${eokFmt.format(million / 100)}억원`
}

const MIN_LABEL_WIDTH = 46
const MIN_LABEL_HEIGHT = 26
const MIN_RATE_LABEL_HEIGHT = 40

function TreemapCell(props) {
  const { x, y, width, height, name, change_rate: changeRate } = props
  if (width <= 0 || height <= 0) return null

  const showName = width >= MIN_LABEL_WIDTH && height >= MIN_LABEL_HEIGHT
  const showRate = showName && height >= MIN_RATE_LABEL_HEIGHT
  const textColor = labelColorFor(changeRate)

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        style={{ fill: colorForChangeRate(changeRate), stroke: 'var(--page)', strokeWidth: 1.5 }}
      />
      {showName && (
        <text
          x={x + 6}
          y={y + 16}
          fontSize={12}
          style={{ fill: textColor, pointerEvents: 'none' }}
        >
          {width < 90 && name.length > 8 ? `${name.slice(0, 7)}…` : name}
        </text>
      )}
      {showRate && (
        <text
          x={x + 6}
          y={y + 32}
          fontSize={12}
          fontWeight={600}
          style={{ fill: textColor, pointerEvents: 'none' }}
        >
          {rateLabel(changeRate)}
        </text>
      )}
    </g>
  )
}

function GroupTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const node = payload[0].payload
  return (
    <div className="tooltip">
      <div className="tooltip-date">{node.name}</div>
      <div className="tooltip-row">
        <span>등락률</span>
        <strong className={node.change_rate > 0 ? 'up' : node.change_rate < 0 ? 'down' : ''}>
          {rateLabel(node.change_rate)}
        </strong>
      </div>
      <div className="tooltip-row">
        <span>거래대금</span>
        <strong>{eokLabel(node.value)}</strong>
      </div>
      <div className="tooltip-row">
        <span>시가총액</span>
        <strong>{eokLabel(node.market_sum)}</strong>
      </div>
    </div>
  )
}

// 범례: -3% ~ +3% 클램프 색 스케일을 5단계로 보여주는 작은 그라디언트 바.
// index.css를 건드리지 않는 제약(작업 지시) 때문에 새 클래스(treemap-legend 등)에는
// 최소 레이아웃을 인라인 style로 직접 준다 — className은 이후 통합 단계에서 index.css가
// 같은 이름으로 더 다듬을 수 있도록 남겨둔다.
function ColorScaleLegend() {
  const stops = [-3, -1.5, 0, 1.5, 3]
  return (
    <div
      className="treemap-legend"
      style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 12 }}
    >
      <span className="treemap-legend-label down" style={{ color: 'var(--down)' }}>
        -3%↓
      </span>
      <div
        className="treemap-legend-bar"
        style={{ display: 'flex', flex: 1, height: 10, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}
      >
        {stops.map((rate) => (
          <div key={rate} style={{ background: colorForChangeRate(rate), flex: 1 }} />
        ))}
      </div>
      <span className="treemap-legend-label up" style={{ color: 'var(--up)' }}>
        +3%↑
      </span>
    </div>
  )
}

export default function GroupTreemap({ items, sizeBy = 'value' }) {
  const data = (items || []).map((item) => ({
    ...item,
    sizeValue: sizeValueOf(item, sizeBy),
  }))

  const hasRealSize = data.some((d) => typeof d[sizeBy] === 'number' && d[sizeBy] > 0)

  if (data.length === 0) {
    return <div className="state">표시할 데이터가 없습니다.</div>
  }

  return (
    <div className="group-treemap">
      <ColorScaleLegend />
      {!hasRealSize && (
        <div className="toggle-hint treemap-size-note" style={{ marginBottom: 8 }}>
          거래대금·시가총액 데이터가 없어 모든 박스를 동일 크기로 표시합니다.
        </div>
      )}
      <ResponsiveContainer width="100%" height={420}>
        <Treemap
          data={data}
          dataKey="sizeValue"
          nameKey="name"
          nodeGap={2}
          aspectRatio={4 / 3}
          isAnimationActive={false}
          content={<TreemapCell />}
        >
          <Tooltip content={<GroupTooltip />} />
        </Treemap>
      </ResponsiveContainer>
    </div>
  )
}
