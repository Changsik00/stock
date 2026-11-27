import { FLOW_RANK_LOOKBACK_DAYS } from './components/dashboard/shared'
import { errorText, useValueRankQuery } from './queries'

// 거래대금 상위 표 데이터 훅 (PLAN.md Phase 5.110, Phase 5.132에서 react-query로 이전) — 장중엔 라이브,
// 그 외엔 일별 스냅샷. 화면이 열려 있는 동안 7분마다(refetchInterval) 다시 불러와 장중 값이 갱신되고,
// 장이 열리고 닫히는 전환도 따라간다. 로딩 표시는 데이터가 아직 없을 때(첫 조회)만 하고, 시장 필터 변경·
// 주기 갱신은 직전 값을 유지한 채 조용히 교체한다(갱신 실패 시 직전 값 유지). 반환 형태는 이전과 같다.
export default function useValueRankAuto(market) {
  const q = useValueRankQuery(market, FLOW_RANK_LOOKBACK_DAYS)
  const body = q.data
  return {
    date: body ? body.date : null,
    rows: body?.rows || [],
    live: Boolean(body?.live),
    cachedAt: body?.cached_at || null,
    loading: q.isPending,
    error: errorText(q),
  }
}
