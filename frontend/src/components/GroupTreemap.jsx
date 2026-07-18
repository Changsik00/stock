import { ResponsiveContainer, Tooltip, Treemap } from 'recharts'

// 업종/테마 강약 트리맵 (PLAN.md §4.6 3.6-3, §6 Phase 3.6-3).
//
// 순수 컴포넌트다 — 데이터 페칭 없이 props만 받는다:
//   items: [{ name, change_rate, value, market_sum }, ...]   (GET /api/groups 응답 그대로)
//   sizeBy: 'value' | 'market_sum'                            (박스 크기 기준, 기본 'value')
//
// value(거래대금, 백만원)는 collectors/group_snapshot.py가 그룹 상세 페이지
// (sise_group_detail.naver) 구성 종목 거래대금을 합산해 채운다 — 개별 박스 크기는
// 이 값을 SIZE_EXPONENT로 압축해 쓴다(아래 **멱 스케일 압축** 참고, 사용자 피드백
// "박스 영역이 너무 비슷하다, 원래 비중대로 차이가 있어야 한다"에서 시작했지만
// 실측 후 그대로 쓰면 대형 그룹이 소형 그룹을 픽셀 단위로 지워버려 압축이 필요했다).
// market_sum(시가총액)은 목록/상세 페이지 어디에도 컬럼이 없어 여전히 항상
// null이다(naver_group.py 모듈 docstring 참고) — sizeBy를 'market_sum'으로 쓰면
// 폴백 동작만 남는다.
//
// **부분/전체 결측 폴백**: 상세 페이지 조회가 그룹별로 실패할 수 있어(네트워크
// 오류 등) value가 일부만 null일 수 있다 — 그 그룹만 1로 폴백해 최소 크기로 그린다
// (다른 그룹은 정상적으로 실제 값 크기를 쓴다). 수집 자체가 실패해 그날 데이터가
// 전부 null이면(hasRealSize=false) 전체가 동일 크기로 폴백하고 상단에 안내 문구를
// 보여준다 — 데이터가 비어 있어도 색(등락률)만으로 트리맵이 의미를 갖게 하기 위함.
//
// **멱 스케일 압축**: 반도체(20조원)류와 소형 그룹(0.1조원)류를 그대로 넣으면
// 200:1 면적비가 나와 소형 그룹이 픽셀 단위로 사라진다(사용자 실측). size =
// value^SIZE_EXPONENT로 압축해 큰 박스 우위는 유지하되 격차를 줄인다. 0.5/0.55/0.65
// 세 값을 실데이터(테마 266개·업종 79개, 캡션 접기 이후 트리맵에 남는 20개/20개
// 박스 기준)로 렌더 비교한 결과(스크린샷 3장, exp-0{50,55,65}-{theme,upjong}-v2.png):
//   - 0.55는 예상과 반대로 가장 나빴다 — 테마 화면의 "기타 상승" 상위 10개 중
//     3개(통신/정유/2026 하반기 신규상장)가 라벨은 물론 이름조차 안 보이는 얇은
//     조각으로 밀렸다(squarify 알고리즘이 크기가 제각각인 좁은 조각들을 나쁘게
//     쪼갠 결과 — 최소 면적 하한에 걸린 박스 수가 0.5/0.65보다 적어(9/20) 균일한
//     격자로 안정되지 못했다).
//   - 0.65는 하한(MIN_AREA_RATIO)에 걸리는 박스가 더 많아져(20개 중 12개) 오히려
//     균일한 격자로 안정적으로 배치되지만, 그 중 3개는 이름만 보이고 등락률 줄은
//     높이가 모자라 안 보인다.
//   - 0.5는 테마·업종 두 화면 모두에서 렌더된 20개 박스 전원이 그룹명+등락률을
//     빠짐없이 보여준 유일한 값이었다(하한 적용 5/20, 업종은 4/20 — 가장 적음).
//     큰 박스 우위도 여전히 뚜렷하다(테마: 하락 쪽이 전체 폭의 ~85% 차지). 사용자
//     스펙의 "우위 유지 + 라벨이 읽히는" 두 조건을 동시에 만족하는 값이라 0.5를
//     채택한다.
// value가 NULL/0 이하면 1로 폴백하는데, 1^SIZE_EXPONENT === 1이라 폴백 최소 크기는
// 그대로 유지된다.
const SIZE_EXPONENT = 0.5

function sizeValueOf(item, sizeBy) {
  const raw = item?.[sizeBy]
  const base = typeof raw === 'number' && raw > 0 ? raw : 1
  return base ** SIZE_EXPONENT
}

// **최소 면적 하한**: 멱 스케일을 거쳐도 소외 그룹은 여전히 상위 박스 대비
// 1~2%대로 눌릴 수 있다 — 렌더 대상 박스 전체 sizeValue 합의 MIN_AREA_RATIO
// 미만이면 그 값(=합 x 비율)으로 올려서 라벨이 최소한 자리를 잡을 여지를 준다.
// 단일 패스 계산이다 — 올린 뒤 재정규화하지 않는다(합이 소폭 늘어나 다른 박스가
// 상대적으로 살짝 작아 보일 수 있지만, 그 정도 오차보다 "픽셀로 사라지는 박스가
// 없어야 한다"는 요구가 우선한다).
const MIN_AREA_RATIO = 0.012

function applyMinAreaFloor(boxes) {
  const total = boxes.reduce((sum, b) => sum + b.sizeValue, 0)
  if (total <= 0) return boxes
  const floor = total * MIN_AREA_RATIO
  return boxes.map((b) => (b.sizeValue < floor ? { ...b, sizeValue: floor } : b))
}

// 색 스케일: 등락률(%)을 -3%~+3%로 클램프한 뒤, 0%=중립(--surface, 라이트/다크 각각
// 카드 배경색)에서 상승은 --up(빨강), 하락은 --down(파랑) 쪽으로 선형 보간한다.
// recharts Treemap은 실제 DOM/SVG를 그리므로(캔버스가 아님) CSS 변수를 그대로 쓸 수
// 있고, CSS `color-mix()`로 보간하면 라이트/다크 전환 시 이 컴포넌트가 다시 렌더링될
// 필요 없이 브라우저가 알아서 다시 그린다(index.css의 --up/--down/--surface가 이미
// 두 테마 모두 정의돼 있음 — CandleChart.jsx처럼 getComputedStyle로 직접 읽어와야
// 하는 캔버스 기반 차트와 달리 이 컴포넌트는 그럴 필요가 없다).
const CLAMP_PERCENT = 3

// 접기(fold) 규칙 (사용자 피드백: 테마 266개를 전부 그리면 강약이 안 보인다).
//   - |등락률| < NEUTRAL_THRESHOLD_PERCENT% → "보합권"으로 묶는다.
//   - 나머지 중 상승/하락 각각 상위 TOP_N개만 트리맵에 개별 박스로 그린다.
// 보합권/기타 상승/기타 하락은 더 이상 트리맵 박스로 그리지 않는다(사용자 피드백:
// 집계 박스가 화면을 과하게 차지해 정작 봐야 할 상위 그룹들이 묻힌다) — 대신 맵
// 아래 한 줄 캡션으로 강등한다(GroupCaption 참고). 트리맵에는 개별 통과분(최대
// TOP_N*2개)만 그려지므로 상위 그룹의 면적/라벨이 항상 화면을 지배한다.
// 업종(79개)·테마(266개) 모두 이 컴포넌트 하나를 공유하므로 여기서 접으면 두 화면에
// 자동으로 적용된다(항목 수가 적은 업종은 자연히 접히는 항목이 적거나 없다).
const NEUTRAL_THRESHOLD_PERCENT = 1
const TOP_N = 10

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

const joFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

// 큰 박스 안 세 번째 줄용 — 억원 단위로는 자릿수가 너무 커지는 그룹(예: 반도체
// 20.9조원 = "209646.5억원")이 많아 1조원(=100만백만원) 이상이면 조원으로 보여준다
// (MARKET_FUND_SERIES 주석의 "1조원 = 1,000,000백만원" 관례와 동일한 환산).
function bigValueLabel(million) {
  if (typeof million !== 'number') return null
  if (million >= 1_000_000) return `${joFmt.format(million / 1_000_000)}조원`
  return eokLabel(million)
}

const MIN_LABEL_WIDTH = 46
const MIN_LABEL_HEIGHT = 26
const MIN_RATE_LABEL_HEIGHT = 40
// 세 번째 줄(거래대금)은 박스가 한층 더 커야 겹치지 않는다.
const MIN_VALUE_LABEL_WIDTH = 70
const MIN_VALUE_LABEL_HEIGHT = 56

function TreemapCell(props) {
  // tradeValue(원본 거래대금)를 쓴다 — recharts Treemap이 내부적으로 크기 계산에
  // 쓴 값을 항상 `value`라는 필드명으로 노드에 덮어써서(node_modules/recharts
  // Treemap.js의 NODE_VALUE_KEY='value') content로 넘어오는 props.value는 우리
  // 데이터의 원본 거래대금이 아니라 sizeValue(압축된 크기)로 뒤바뀐다. 원본
  // API 필드명이 우연히 둘 다 'value'라 충돌하므로, GroupTreemap 하단에서 미리
  // tradeValue로 별칭을 만들어 이 충돌을 피한다.
  const { x, y, width, height, name, change_rate: changeRate, tradeValue } = props
  if (width <= 0 || height <= 0) return null

  const showName = width >= MIN_LABEL_WIDTH && height >= MIN_LABEL_HEIGHT
  const showSubLabel = showName && height >= MIN_RATE_LABEL_HEIGHT
  const showValueLabel = showSubLabel && width >= MIN_VALUE_LABEL_WIDTH && height >= MIN_VALUE_LABEL_HEIGHT
  const textColor = labelColorFor(changeRate)

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        style={{
          fill: colorForChangeRate(changeRate),
          stroke: 'var(--page)',
          strokeWidth: 1.5,
        }}
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
      {showSubLabel && (
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
      {showValueLabel && (
        <text
          x={x + 6}
          y={y + 48}
          fontSize={11}
          style={{ fill: textColor, pointerEvents: 'none', opacity: 0.85 }}
        >
          {bigValueLabel(tradeValue)}
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
        <strong>{bigValueLabel(node.tradeValue) ?? '-'}</strong>
      </div>
      <div className="tooltip-row">
        <span>시가총액</span>
        <strong>{bigValueLabel(node.market_sum) ?? '-'}</strong>
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

function isFiniteRate(rate) {
  return typeof rate === 'number' && !Number.isNaN(rate)
}

// 캡션 그룹 하나를 만든다(보합권/기타 상승/기타 하락 — 더 이상 트리맵 박스가
// 아니라 GroupCaption의 한 항목이다): 개수, 평균 등락률(색 점 판정용), 캡션 요약
// 문구, hover 시 보여줄 |등락률| 상위 5개 멤버 타이틀 문자열을 계산한다. 상위 5개
// 로직은 기존 집계 박스 툴팁(topMembers)과 동일하게 재사용한다.
function buildCaptionGroup({ name, members, isNeutral, colorKind }) {
  const count = members.length
  const rates = members.map((m) => m.change_rate).filter(isFiniteRate)
  const avgRate = rates.length > 0 ? rates.reduce((sum, r) => sum + r, 0) / rates.length : 0
  const topMembers = [...members]
    .sort((a, b) => Math.abs(b.change_rate ?? 0) - Math.abs(a.change_rate ?? 0))
    .slice(0, 5)
    .map((m) => ({ name: m.name, change_rate: m.change_rate }))

  const summary = isNeutral ? `${name} ${count}개` : `${name} ${count}개 (평균 ${rateLabel(avgRate)})`
  const hoverTitle = topMembers.map((m) => `${m.name} ${rateLabel(m.change_rate)}`).join('\n')

  return { name, count, colorKind, summary, hoverTitle }
}

// 접기 본체: |등락률| < NEUTRAL_THRESHOLD_PERCENT는 보합권으로 캡션에 묶고, 나머지는
// 상승/하락 상위 TOP_N개만 트리맵 박스로 남기고 그 밖은 기타 상승/기타 하락 캡션
// 그룹으로 묶는다. 트리맵에 그릴 박스(boxes)와 캡션에 보여줄 그룹(captionGroups)을
// 분리해서 반환한다.
function foldItems(data) {
  const neutral = []
  const nonNeutral = []
  for (const item of data) {
    if (!isFiniteRate(item.change_rate) || Math.abs(item.change_rate) < NEUTRAL_THRESHOLD_PERCENT) {
      neutral.push(item)
    } else {
      nonNeutral.push(item)
    }
  }

  const up = nonNeutral.filter((d) => d.change_rate > 0).sort((a, b) => b.change_rate - a.change_rate)
  const down = nonNeutral.filter((d) => d.change_rate < 0).sort((a, b) => a.change_rate - b.change_rate)

  const boxes = applyMinAreaFloor([...up.slice(0, TOP_N), ...down.slice(0, TOP_N)])

  const captionGroups = []
  if (neutral.length > 0) {
    captionGroups.push(buildCaptionGroup({ name: '보합권', members: neutral, isNeutral: true, colorKind: 'neutral' }))
  }
  const upRest = up.slice(TOP_N)
  if (upRest.length > 0) {
    captionGroups.push(buildCaptionGroup({ name: '기타 상승', members: upRest, isNeutral: false, colorKind: 'up' }))
  }
  const downRest = down.slice(TOP_N)
  if (downRest.length > 0) {
    captionGroups.push(buildCaptionGroup({ name: '기타 하락', members: downRest, isNeutral: false, colorKind: 'down' }))
  }
  return { boxes, captionGroups }
}

const CAPTION_DOT_COLOR = {
  neutral: 'var(--text-muted)',
  up: 'var(--up)',
  down: 'var(--down)',
}

// 맵 바로 아래 한 줄 캡션 — 보합권/기타 상승/기타 하락을 색 점 + "이름 N개 (평균
// x.x%)" 요약으로 보여준다. hover(title 속성)하면 그 그룹에서 |등락률| 상위 5개
// 그룹명+등락률이 줄바꿈으로 뜬다(네이티브 title, 별도 팝오버 구현 불필요).
function GroupCaption({ groups }) {
  if (!groups || groups.length === 0) return null
  return (
    <div
      className="group-treemap-caption"
      style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: 12, color: 'var(--text-secondary)' }}
    >
      {groups.map((group, idx) => (
        <span key={group.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {idx > 0 && <span aria-hidden="true" style={{ color: 'var(--text-muted)' }}>·</span>}
          <span
            title={group.hoverTitle || undefined}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: group.hoverTitle ? 'help' : 'default' }}
          >
            <span
              aria-hidden="true"
              style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: CAPTION_DOT_COLOR[group.colorKind] }}
            />
            {group.summary}
          </span>
        </span>
      ))}
    </div>
  )
}

export default function GroupTreemap({ items, sizeBy = 'value' }) {
  // tradeValue: 원본 거래대금을 recharts Treemap의 'value' 필드 덮어쓰기(위 TreemapCell
  // 주석 참고)로부터 지키기 위한 별칭이다 — content/Tooltip 어느 쪽도 props.value를
  // 원본 거래대금으로 신뢰할 수 없으므로 여기서 한 번만 복제해 둔다.
  const rawData = (items || []).map((item) => ({
    ...item,
    tradeValue: item.value,
    sizeValue: sizeValueOf(item, sizeBy),
  }))

  const hasRealSize = rawData.some((d) => typeof d[sizeBy] === 'number' && d[sizeBy] > 0)

  if (rawData.length === 0) {
    return <div className="state">표시할 데이터가 없습니다.</div>
  }

  const { boxes, captionGroups } = foldItems(rawData)

  return (
    <div className="group-treemap">
      <ColorScaleLegend />
      {!hasRealSize && (
        <div className="toggle-hint treemap-size-note" style={{ marginBottom: 8 }}>
          거래대금·시가총액 데이터가 없어 모든 박스를 동일 크기로 표시합니다.
        </div>
      )}
      <GroupCaption groups={captionGroups} />
      <ResponsiveContainer width="100%" height={420}>
        <Treemap
          data={boxes}
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
