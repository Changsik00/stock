import { useEffect, useState } from 'react'
import { fetchShortSellingMarket } from '../../api'
import MacroChart from '../MacroChart'
import PeriodPicker from '../PeriodPicker'

// 공매도(PLAN.md §5.32) 모달 차트 기본 기간 — 자금 지표(180일)와 동일하게 추세를
// 보기에 넉넉한 창으로 시작한다.
const DEFAULT_SHORT_SELLING_DAYS = 180

// 공매도 비중(코스피/코스닥, PLAN.md §5.32) — KRX 정보데이터시스템 실측(거래대금
// 비중, value_ratio). FundModal과 동일한 구조(PeriodPicker + fetch)이되 소스가
// 전용 테이블(/api/markets/{market}/short-selling)이라 macro_series 기반
// fetchMacroSeries 대신 fetchShortSellingMarket을 시장별로 부른다. MacroChart를
// 그대로 재사용해 새 차트 컴포넌트를 만들지 않는다(points: [{date, value}] 모양에
// 맞춰 value_ratio를 value로 매핑).
export default function ShortSellingModal() {
  const [days, setDays] = useState(DEFAULT_SHORT_SELLING_DAYS)
  const [kospiPoints, setKospiPoints] = useState([])
  const [kosdaqPoints, setKosdaqPoints] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([fetchShortSellingMarket('kospi', days), fetchShortSellingMarket('kosdaq', days)])
      .then(([kospiBody, kosdaqBody]) => {
        if (cancelled) return
        setKospiPoints((kospiBody.series || []).map((r) => ({ date: r.date, value: r.value_ratio })))
        setKosdaqPoints((kosdaqBody.series || []).map((r) => ({ date: r.date, value: r.value_ratio })))
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
  }, [days])

  return (
    <div>
      <PeriodPicker value={days} onChange={setDays} />
      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}
      {!loading && !error && (
        <>
          <div className="toggle-hint">
            공매도 거래대금 비중(%, KRX 정보데이터시스템 실측) · 위 대시보드의 "대차잔고" 타일(KOFIA, 빌린 주식
            잔고 시장 전체 합계)과는 별개 소스·별개 지표입니다.
          </div>
          <div className="chart-stack">
            <MacroChart label="코스피 공매도 비중" unit="%" points={kospiPoints} />
            <MacroChart label="코스닥 공매도 비중" unit="%" points={kosdaqPoints} />
          </div>
        </>
      )}
    </div>
  )
}
