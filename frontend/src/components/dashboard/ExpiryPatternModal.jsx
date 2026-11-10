import { useEffect, useState } from 'react'
import { fetchExpiryCumulative, fetchExpiryPattern } from '../../api'
import ExpiryCumulativeChart from '../ExpiryCumulativeChart'
import ExpiryPatternChart from '../ExpiryPatternChart'
import { signedEokJoLabel } from '../../format'

// K200 선물 만기 수렴 패턴 상세(PLAN.md §5.42) — "다음 만기" 타일 클릭 시 여는
// 모달. DerivativeEtfModal과 동일한 자기완결 패턴(마운트 시 1회만 fetch, 폴링
// 없음 — index_ohlcv 전체를 매번 다시 집계하는 값싼 DB 읽기라 하루에도 몇 번씩
// 바뀔 데이터가 아니다). 로컬 전용 기능이라 STATIC_DATA 정적 배포에서는 호출하지
// 않는다(fetchScalpHitrate와 동일 이유, api.js 주석 참고) — 대신 "다음 만기"
// 타일의 onClick 자체가 STATIC_DATA일 때는 기존 foreignPosition 모달을 열도록
// 분기해 정적 배포에서도 클릭이 헛돌지 않게 한다.
// 누적 수급 섹션(Phase 5.100/5.101) — 선물(옵션 아님)·현물 순매수 누적, 관찰 통계.
// 기간: 사이클 / 최근 20·60·120거래일 / 고점 이후. 시장 토글(코스피/코스닥).
const PERIODS = [
  { key: 'cycle', label: '사이클', mode: 'cycle', days: 60 },
  { key: 'r20', label: '20일', mode: 'recent', days: 20 },
  { key: 'r60', label: '60일', mode: 'recent', days: 60 },
  { key: 'r120', label: '120일', mode: 'recent', days: 120 },
  { key: 'peak', label: '고점 이후', mode: 'peak', days: 60 },
]
const MARKETS = [
  { key: 'kospi', label: '코스피' },
  { key: 'kosdaq', label: '코스닥' },
]

const dirCls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const pct = (v) => (typeof v === 'number' ? `${v > 0 ? '+' : v < 0 ? '\u2212' : ''}${Math.abs(v).toFixed(1)}%` : '-')

// 현물·선물 외국인 방향이 엇갈리면 관찰형 문구(권유 아님). 한쪽이 없으면 null.
function divergenceNote(item) {
  const s = item?.spot_totals?.foreign
  const f = item?.totals?.foreign
  if (typeof s !== 'number' || typeof f !== 'number' || s === 0 || f === 0) return null
  if (s > 0 === f > 0) return '외국인 현물·선물 누적 방향이 같습니다.'
  return `외국인 현물은 순${s > 0 ? '매수' : '매도'}, 선물은 순${f > 0 ? '매수' : '매도'}로 누적 방향이 엇갈립니다.`
}

const absLabel = (v) => signedEokJoLabel(Math.abs(v)).replace('+', '')
const pctPlain = (v) => (typeof v === 'number' ? `${v < 0 ? '\u2212' : ''}${Math.abs(v).toFixed(0)}%` : '-')

// 외인 현물 순매도 구간의 흡수율 요약(Phase 5.103) — 관찰형. 외인이 순매수면 해당없음.
function AbsorptionSummary({ item, stats }) {
  const a = item?.absorption
  const f = item?.spot_totals?.foreign
  if (typeof f !== 'number') return null
  return (
    <div className="toggle-hint" style={{ margin: '0 0 8px' }}>
      {a ? (
        <>
          외인 현물 {absLabel(a.foreign)} 순매도 중 기관·개인이 <strong>{pctPlain(a.pct)}</strong> 받아냄
          (기관 {pctPlain(a.institution_pct)} · 개인 {pctPlain(a.individual_pct)}).
        </>
      ) : (
        <>외인 현물이 순매수{f > 0 ? '' : '·0'}인 구간이라 흡수율은 해당없음입니다.</>
      )}
      {stats?.n > 0 && (
        <>
          <br />
          과거 완료 사이클(외인 순매도 {stats.n}개) 흡수율 최소 {pctPlain(stats.min)} · 중앙값 {pctPlain(stats.median)} · 최대{' '}
          {pctPlain(stats.max)}. 외인 매도 규모가 작으면 비율이 크게 튑니다.
        </>
      )}
    </div>
  )
}

function ExpiryCumulativeSection() {
  const [market, setMarket] = useState('kospi')
  const [period, setPeriod] = useState('cycle')
  const [showInst, setShowInst] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [sel, setSel] = useState('current')

  useEffect(() => {
    let cancelled = false
    const p = PERIODS.find((x) => x.key === period)
    setData(null)
    setError(null)
    fetchExpiryCumulative(6, { market, mode: p.mode, days: p.days })
      .then((body) => {
        if (!cancelled) setData(body)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [market, period])

  const controls = (
    <>
      <div className="toggle-row" style={{ marginBottom: 6 }}>
        {MARKETS.map((m) => (
          <button key={m.key} type="button" className={`toggle-chip ${market === m.key ? 'active' : ''}`} onClick={() => setMarket(m.key)}>
            {m.label}
          </button>
        ))}
        <button type="button" className={`toggle-chip ${showInst ? 'active' : ''}`} onClick={() => setShowInst((v) => !v)}>
          기관 포함
        </button>
      </div>
      <div className="toggle-row" style={{ marginBottom: 8 }}>
        {PERIODS.map((p) => (
          <button key={p.key} type="button" className={`toggle-chip ${period === p.key ? 'active' : ''}`} onClick={() => setPeriod(p.key)}>
            {p.label}
          </button>
        ))}
      </div>
    </>
  )

  const hint = (
    <div className="toggle-hint" style={{ marginBottom: 8 }}>
      현물(키움 {market === 'kospi' ? '코스피' : '코스닥'} 시장 합계)·K200 선물(옵션 수급 아님) 순매수를 같은 기간에
      누적하고, 같은 기간 지수 등락률과 나란히 둔 관찰치입니다. 수급과 지수의 동시기간 비교일 뿐
      인과나 이후 방향을 뜻하지 않으며 매매 신호가 아닙니다. 표본기간 {data?.date_from ?? '-'} ~ {data?.date_to ?? '-'}.
    </div>
  )

  let body = null
  if (error) body = <div className="state error">{error}</div>
  else if (!data) body = <div className="state">불러오는 중…</div>
  else if (period === 'cycle') body = <CycleBody data={data} sel={sel} setSel={setSel} showInst={showInst} />
  else body = <WindowBody data={data} period={period} showInst={showInst} />

  return (
    <div style={{ marginBottom: 16 }}>
      {hint}
      {controls}
      {body}
    </div>
  )
}

function CycleBody({ data, sel, setSel, showInst }) {
  const cycles = [
    ...(data.current ? [{ key: 'current', label: '현재', item: data.current }] : []),
    ...[...data.completed].reverse().map((c) => ({ key: c.expiry, label: c.expiry.slice(2), item: c })),
  ]
  const picked = cycles.find((c) => c.key === sel) ?? cycles[0]
  if (!picked) return <div className="state">표시할 데이터가 없습니다.</div>
  const cell = (v) => (
    <td className={dirCls(v)} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
      {typeof v === 'number' ? `${v > 0 ? '▲ ' : v < 0 ? '▼ ' : ''}${signedEokJoLabel(v)}` : '-'}
    </td>
  )
  const note = divergenceNote(picked.item)
  return (
    <>
      <div className="toggle-row">
        {cycles.map((c) => (
          <button key={c.key} type="button" className={`toggle-chip ${picked.key === c.key ? 'active' : ''}`} onClick={() => setSel(c.key)}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="toggle-hint" style={{ margin: '8px 0' }}>
        {picked.item.start} ~ {picked.item.end} · {picked.item.trading_days}거래일 · 지수 {pct(picked.item.index_pct)}
        {picked.key === 'current' ? ` · 다음 만기 ${data.next_expiry}` : ` · 만기 ${picked.item.expiry}`}
        {note && <><br />{note}</>}
      </div>
      <AbsorptionSummary item={picked.item} stats={data.absorption_stats} />
      <ExpiryCumulativeChart curve={picked.item.curve} showInst={showInst} />
      <div className="table-scroll">
        <table className="flow-rank-table">
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>사이클(만기)</th>
              <th style={{ textAlign: 'right' }}>외인 현물</th>
              <th style={{ textAlign: 'right' }}>외인 선물</th>
              <th style={{ textAlign: 'right' }}>기관 현물</th>
              <th style={{ textAlign: 'right' }}>기관 선물</th>
              <th style={{ textAlign: 'right' }}>지수</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((c) => (
              <tr key={c.key}>
                <td>{c.key === 'current' ? `현재(~${data.next_expiry.slice(5)})` : c.item.expiry}</td>
                {cell(c.item.spot_totals?.foreign)}
                {cell(c.item.totals.foreign)}
                {cell(c.item.spot_totals?.institution)}
                {cell(c.item.totals.institution)}
                <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{pct(c.item.index_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* 흡수율·라벨은 열이 많아 별도 표로 둔다(390px에서 가로 스크롤 없이 줄바꿈) */}
      <table className="flow-rank-table absorb-table" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>사이클</th>
            <th style={{ textAlign: 'right' }}>흡수율</th>
            <th style={{ textAlign: 'left' }}>외인 현·선</th>
            <th style={{ textAlign: 'left' }}>기관 현물</th>
          </tr>
        </thead>
        <tbody>
          {cycles.map((c) => (
            <tr key={c.key}>
              <td>{c.key === 'current' ? '현재' : c.item.expiry.slice(2)}</td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {c.item.absorption ? pctPlain(c.item.absorption.pct) : '해당없음'}
              </td>
              <td>{c.item.pair_label ?? '-'}</td>
              <td>{c.item.inst_flip?.label.replace('기관 현물 누적 ', '') ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function WindowBody({ data, period, showInst }) {
  const w = data.window
  const peak = data.peak
  if (period === 'peak' && !peak) return <div className="state">지수 종가 데이터가 없습니다.</div>
  if (!w) {
    return (
      <div className="state">
        {period === 'peak' ? `최근 ${peak.lookback}거래일 고점(${peak.date})이 마지막 거래일이라 고점 이후 구간이 없습니다.` : '표시할 데이터가 없습니다.'}
      </div>
    )
  }
  const note = divergenceNote(w)
  const row = (label, v) => (
    <span style={{ marginRight: 12 }}>
      {label}{' '}
      <strong className={dirCls(v)}>{typeof v === 'number' ? `${v > 0 ? '▲ ' : v < 0 ? '▼ ' : ''}${signedEokJoLabel(v)}` : '-'}</strong>
    </span>
  )
  return (
    <>
      <div className="toggle-hint" style={{ margin: '8px 0' }}>
        {period === 'peak' && (
          <>
            최근 {peak.lookback}거래일 지수 고점 {peak.date} · 고점 대비 {pct(peak.index_pct)}
            <br />
          </>
        )}
        {w.start} ~ {w.end} · {w.trading_days}거래일{period === 'peak' ? '' : ` · 지수 ${pct(w.index_pct)}`}
        {note && <><br />{note}</>}
      </div>
      <div className="toggle-hint" style={{ margin: '0 0 8px' }}>
        {row('외인 현물', w.spot_totals?.foreign)}
        {row('외인 선물', w.totals.foreign)}
        {row('기관 현물', w.spot_totals?.institution)}
        {row('기관 선물', w.totals.institution)}
        {row('개인 현물', w.spot_totals?.individual)}
      </div>
      <AbsorptionSummary item={w} stats={data.absorption_stats} />
      <ExpiryCumulativeChart curve={w.curve} showInst={showInst} />
    </>
  )
}

export default function ExpiryPatternModal() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  // pt/% 토글(PLAN.md §5.42-5) — §5.41 flowInvestor 토글과 동일한
  // toggle-row/toggle-chip 패턴. 기본값을 '%'로 둔 이유: 이 필드 자체가
  // "3년 구간 동안 지수 수준이 크게 바뀌어 pt만으로는 사이클 간 비교가
  // 왜곡된다"는 사용자 지적에서 나왔으므로, 처음 여는 화면이 그 문제를
  // 이미 해결한 뷰(%)를 보여주는 게 자연스럽다. pt는 원 지표라 토글로 한
  // 클릭이면 바로 볼 수 있어 잃는 것도 없다.
  const [unitMode, setUnitMode] = useState('pct')

  useEffect(() => {
    let cancelled = false
    fetchExpiryPattern()
      .then((body) => {
        if (!cancelled) setData(body)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) return <div className="state">불러오는 중…</div>
  if (error) return <div className="state error">{error}</div>
  if (!data || data.reason) {
    return <div className="state">{data?.reason || '표시할 데이터가 없습니다.'}</div>
  }

  return (
    <div>
      <ExpiryCumulativeSection />
      {/* PLAN.md §5.42 "표본 수(사이클 개수)를 항상 그래프에 함께 노출한다" —
          툴팁 안에 숨기지 않고 그래프 바로 위에 상시 노출. 관찰형 문구만 쓰고
          "이번에도 이렇게 수렴할 것"이라는 예측형 표현은 쓰지 않는다(§5.15/
          §5.23/§5.33/§5.40과 동일한 house rule). */}
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        과거 {data.cycle_count}회 만기 사이클({data.date_from} ~ {data.date_to}) 평균 — 이번
        사이클도 같은 패턴을 보인다는 보장은 없는 과거 관찰 통계입니다.
      </div>
      {/* PLAN.md §5.46-3 — 사용자가 "그래프가 뭘 의미하는지 모르겠다"고 지적해
          도입한 설명 패널. 기본으로 펼쳐 놓는다(처음 보는 사람이 접힌 걸 눌러야
          내용을 알 수 있으면 "있어도 도움이 안 된다"는 지적을 그대로 반복하게
          되므로) — 두 번째 방문부터는 <details>가 사용자의 마지막 열림/닫힘
          상태를 브라우저가 세션 내에서 기억하지 않으므로 매번 펼쳐진 채로
          보이는데, 이건 의도된 트레이드오프다(가려서 놓치는 것보다 매번
          보이는 게 낫다는 판단). */}
      <details className="expiry-help" open>
        <summary>이 그래프 보는 법</summary>
        <div className="expiry-help-item">
          <strong>뭘 보여주나요</strong>
          선물(코스피200 선물) 가격에서 현물(코스피200 지수) 가격을 뺀 차이(베이시스)가
          과거 만기 사이클들에서 만기 며칠 전(D-day)마다 평균적으로 어떻게 움직였는지
          보여줍니다.
        </div>
        <div className="expiry-help-item">
          <strong>0보다 위 / 아래</strong>
          0보다 위 = 선물이 현물보다 비쌈(콘탱고 — 평상시 흔한 정상 상태). 0보다 아래 =
          선물이 현물보다 쌈(백워데이션 — 시장이 불안하거나 급락 국면일 때 자주 나타남).
        </div>
        <div className="expiry-help-item">
          <strong>D-0(만기)에 가까워질수록 0에 붙는 건 예측이 아니에요</strong>
          만기일엔 선물이 그날 현물 가격으로 정산되도록 규칙으로 정해져 있어서, 그래프가
          오른쪽 끝(D-0)으로 갈수록 0에 가까워지는 건 당연한 구조적 결과입니다. 진짜 볼
          부분은 수렴 자체가 아니라, 그 전 구간(D-14~D-3 근처)에서 선이 0보다 얼마나
          위/아래에 있었는지, 사이클마다 그 폭이 얼마나 들쭉날쭉했는지입니다.
        </div>
        <div className="expiry-help-item">
          <strong>평균선-중앙값선 간격</strong>
          두 선이 벌어져 있을수록 특정 사이클 한둘에 튄 값(급등락)이 있어 평균이 그쪽으로
          끌려갔다는 뜻입니다. 두 선이 붙어 있으면 사이클들이 서로 고르게 비슷했다는
          뜻이고요.
        </div>
        <div className="expiry-help-item">
          <strong>선이 오르든 내리든 좋고 나쁨이 아니에요</strong>
          매매 신호가 아니라 순수 과거 관찰치입니다. "이번 사이클도 같은 패턴을 보일
          것"이라는 보장은 전혀 없습니다.
        </div>
        <div className="expiry-help-item">
          <strong>pt vs %</strong>
          pt는 원래 포인트 차이 그대로, %는 그 시점 지수 대비 비율입니다. 3년간 지수
          레벨 자체가 많이 바뀌어서 사이클끼리 비교할 땐 %가 더 공정합니다.
        </div>
        <div className="expiry-help-item">
          <strong>표본수(n)</strong>
          지점마다 사이클 수가 다를 수 있어요. 만기에서 먼 날짜(예: D-14)일수록 데이터가
          짧은 초기 사이클이 빠지면서 표본이 줄어 신뢰도가 낮을 수 있습니다(툴팁에서 지점별
          n을 확인할 수 있습니다).
        </div>
      </details>
      <div className="toggle-row">
        {[
          { key: 'pct', label: '%(지수 대비)' },
          { key: 'pt', label: 'pt(포인트)' },
        ].map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`toggle-chip ${unitMode === opt.key ? 'active' : ''}`}
            onClick={() => setUnitMode(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <ExpiryPatternChart points={data.points ?? []} mode={unitMode} />
    </div>
  )
}
