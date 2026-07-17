async function getJson(url) {
  const res = await fetch(url)
  const body = await res.json()
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    throw new Error(detail || `요청 실패 (${res.status})`)
  }
  return body
}

// GitHub Pages 정적 배포 모드 — VITE_STATIC_DATA=1로 빌드하면 /api/* 대신
// public/data/*.json 스냅샷을 fetch해서 클라이언트에서 슬라이싱한다. 스냅샷은
// 항상 최대 1095일 창을 담고 있으므로 (수집 스크립트, PLAN.md 참고) 요청받은
// days만큼 잘라내면 라이브 API와 동일한 응답 모양이 된다.
const STATIC_DATA = import.meta.env.VITE_STATIC_DATA === '1'

// URL별로 파싱된 JSON을 캐싱해 탭/기간 전환마다 같은 스냅샷 파일을 다시 받지 않는다.
const staticJsonCache = new Map()

function fetchStaticJson(path) {
  const url = import.meta.env.BASE_URL + path
  if (!staticJsonCache.has(url)) {
    staticJsonCache.set(
      url,
      fetch(url).then((res) => {
        if (!res.ok) throw new Error(`정적 데이터 로드 실패 (${res.status}): ${url}`)
        return res.json()
      })
    )
  }
  return staticJsonCache.get(url)
}

// 오늘(실제 로컬 날짜) 기준 "days일 전" ISO 날짜 문자열 — 라이브 라우터의
// `dt.date.today() - dt.timedelta(days=days)` 계산과 동일한 의미다.
function isoCutoffDate(days) {
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  const y = cutoff.getFullYear()
  const m = String(cutoff.getMonth() + 1).padStart(2, '0')
  const d = String(cutoff.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function filterFlowsSince(flows, days) {
  const cutoff = isoCutoffDate(days)
  const filtered = {}
  for (const [investor, entries] of Object.entries(flows || {})) {
    filtered[investor] = entries.filter((e) => e.date >= cutoff)
  }
  return filtered
}

// 레거시 — /api/series (가격만, PLAN.md 마이그레이션 전 형태). App.jsx는 더 이상
// 이 함수를 쓰지 않지만 기존 참조가 남아 있을 수 있어 유지한다.
export async function fetchSeries(market, days) {
  const body = await getJson(`/api/series?market=${market}&days=${days}`)
  return body.series
}

// GET /api/markets/{market}/series?days=N -> { market, days, prices, flows }
export async function fetchMarketSeries(market, days) {
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson(`data/markets-${market}.json`)
    return {
      market,
      days,
      prices: snapshot.prices.slice(-days),
      flows: filterFlowsSince(snapshot.flows, days),
    }
  }
  return getJson(`/api/markets/${market}/series?days=${days}`)
}

// GET /api/markets/flow-rank?investor=foreign&days=N -> { investor, days, dates: [{date, rows}] }
// (PLAN.md §4.5) — dates는 최근 날짜가 먼저 온다. flow_rank는 소스 제약상(백엔드
// collectors/flow_rank.py 참고) 배치를 반복 실행한 날짜만 누적되므로, days는 "정확히
// N개 날짜"가 아니라 "N일 이내에 존재하는 날짜만" 필터한다 — 정적 스냅샷도 동일하게
// cutoff 필터를 적용해 라이브 API와 동작을 맞춘다.
export async function fetchFlowRank(investor, days = 7) {
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson(`data/flow-rank-${investor}.json`)
    const cutoff = isoCutoffDate(days)
    return {
      investor,
      days,
      dates: (snapshot.dates || []).filter((d) => d.date >= cutoff),
    }
  }
  return getJson(`/api/markets/flow-rank?investor=${investor}&days=${days}`)
}

// GET /api/macro/series?ids=usdkrw,wti,brent&days=N -> { days, series: { id: [...] } }
export async function fetchMacroSeries(ids, days) {
  const idParam = Array.isArray(ids) ? ids.join(',') : ids
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson('data/macro.json')
    const cutoff = isoCutoffDate(days)
    const series = {}
    for (const id of idParam.split(',')) {
      const trimmed = id.trim()
      if (!trimmed) continue
      series[trimmed] = (snapshot.series[trimmed] || []).filter((entry) => entry.date >= cutoff)
    }
    return { days, series }
  }
  return getJson(`/api/macro/series?ids=${idParam}&days=${days}`)
}
