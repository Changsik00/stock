async function getJson(url) {
  const res = await fetch(url)
  const body = await res.json()
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    throw new Error(detail || `요청 실패 (${res.status})`)
  }
  return body
}

// 레거시 — /api/series (가격만, PLAN.md 마이그레이션 전 형태). App.jsx는 더 이상
// 이 함수를 쓰지 않지만 기존 참조가 남아 있을 수 있어 유지한다.
export async function fetchSeries(market, days) {
  const body = await getJson(`/api/series?market=${market}&days=${days}`)
  return body.series
}

// GET /api/markets/{market}/series?days=N -> { market, days, prices, flows }
export async function fetchMarketSeries(market, days) {
  return getJson(`/api/markets/${market}/series?days=${days}`)
}

// GET /api/macro/series?ids=usdkrw,wti,brent&days=N -> { days, series: { id: [...] } }
export async function fetchMacroSeries(ids, days) {
  const idParam = Array.isArray(ids) ? ids.join(',') : ids
  return getJson(`/api/macro/series?ids=${idParam}&days=${days}`)
}
