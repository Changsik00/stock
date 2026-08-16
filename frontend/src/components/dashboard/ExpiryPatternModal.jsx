import { useEffect, useState } from 'react'
import { fetchExpiryPattern } from '../../api'
import ExpiryPatternChart from '../ExpiryPatternChart'

// K200 선물 만기 수렴 패턴 상세(PLAN.md §5.42) — "다음 만기" 타일 클릭 시 여는
// 모달. DerivativeEtfModal과 동일한 자기완결 패턴(마운트 시 1회만 fetch, 폴링
// 없음 — index_ohlcv 전체를 매번 다시 집계하는 값싼 DB 읽기라 하루에도 몇 번씩
// 바뀔 데이터가 아니다). 로컬 전용 기능이라 STATIC_DATA 정적 배포에서는 호출하지
// 않는다(fetchScalpHitrate와 동일 이유, api.js 주석 참고) — 대신 "다음 만기"
// 타일의 onClick 자체가 STATIC_DATA일 때는 기존 foreignPosition 모달을 열도록
// 분기해 정적 배포에서도 클릭이 헛돌지 않게 한다.
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
