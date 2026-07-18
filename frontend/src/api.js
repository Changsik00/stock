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
export const STATIC_DATA = import.meta.env.VITE_STATIC_DATA === '1'

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

// GET /api/markets/flow-rank?investor=foreign&side=buy&days=N ->
// { investor, side, days, dates: [{date, rows}] } (PLAN.md §4.5/§6 3.5-2b) — dates는
// 최근 날짜가 먼저 온다. flow_rank는 소스 제약상(백엔드 collectors/flow_rank.py 참고)
// 배치를 반복 실행한 날짜만 누적되므로, days는 "정확히 N개 날짜"가 아니라 "N일 이내에
// 존재하는 날짜만" 필터한다 — 정적 스냅샷도 동일하게 cutoff 필터를 적용해 라이브
// API와 동작을 맞춘다. side=buy가 기본값(하위호환) — 정적 스냅샷 파일명은 buy일 때
// 접미사 없이 기존 이름을 그대로 쓴다(export_static.py와 짝).
export async function fetchFlowRank(investor, side = 'buy', days = 7) {
  if (STATIC_DATA) {
    const suffix = side === 'buy' ? '' : `-${side}`
    const snapshot = await fetchStaticJson(`data/flow-rank-${investor}${suffix}.json`)
    const cutoff = isoCutoffDate(days)
    return {
      investor,
      side,
      days,
      dates: (snapshot.dates || []).filter((d) => d.date >= cutoff),
    }
  }
  return getJson(`/api/markets/flow-rank?investor=${investor}&side=${side}&days=${days}`)
}

// GET /api/markets/flow-path?days=N&limit=M&direction=in|out -> { date, days,
// direction, rows: [{code, name, direct_net, via_etf_net, top_etfs}] } (PLAN.md
// §4.5/§6 3.5-3, direction 확장은 §4.6 3.6-4) — ETF look-through 상위: 백엔드가 days
// 창 안의 가장 최근 flow_path.date 하나만 골라 반환한다(flow-rank처럼 날짜별로 묶지
// 않음 — 화면도 항상 최신 1개 날짜만 보여줌). direction="in"(기본값)은 via_etf_net
// 내림차순 유입 상위, "out"은 via_etf_net<0인 유출 상위(오름차순, 가장 큰 유출이
// 1등).
export async function fetchFlowPath(days = 7, limit = 30, direction = 'in') {
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson(direction === 'out' ? 'data/flow-path-out.json' : 'data/flow-path.json')
    return snapshot
  }
  return getJson(`/api/markets/flow-path?days=${days}&limit=${limit}&direction=${direction}`)
}

// GET /api/markets/sentiment -> { score, approx, components: { breadth, flow, etf } }
// (PLAN.md §4.6 3.6-4) — 시장 종합 매수세/매도세 게이지(-100~+100). 요소별 score/
// weight/date와 원재료(adv/dec/flat, buy_sum/sell_sum, net_inflow_sum/aum_sum)를
// components에 그대로 담아 내려준다(백엔드 routers/flow_rank.py market_sentiment 참고).
export async function fetchSentiment() {
  if (STATIC_DATA) {
    return fetchStaticJson('data/sentiment.json')
  }
  return getJson('/api/markets/sentiment')
}

// GET /api/markets/value-rank?market=all&days=N -> { market, date, days, rows: [{rank,
// market, code, name, value, change_rate, is_etf, turnover}] } (PLAN.md §4.6 3.6-1) —
// 백엔드가 days 창 안의 가장 최근 날짜 하나만 골라 반환한다. 정적 스냅샷은
// market=all 파일 하나만 덤프하므로(export_static.py), kospi/kosdaq 필터는
// 클라이언트에서 rows를 걸러내고 rank를 1..N으로 다시 매긴다(라이브 라우터가
// 시장별 원본 rank를 쓰는 것과 순위 번호가 다를 수 있으나, all에서 거래대금
// 내림차순으로 이미 정렬돼 있어 표시 순서는 동일하다).
export async function fetchValueRank(market = 'all', days = 7) {
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson('data/value-rank.json')
    if (market === 'all') return snapshot
    const rows = (snapshot.rows || [])
      .filter((r) => r.market === market)
      .map((r, i) => ({ ...r, rank: i + 1 }))
    return { ...snapshot, market, rows }
  }
  return getJson(`/api/markets/value-rank?market=${market}&days=${days}`)
}

// GET /api/markets/{market}/breadth?days=N -> { market, days, series: [{date, adv, dec,
// flat, limit_up, limit_down}] } (PLAN.md §3.5/§4.6 3.6-2) — 일별 확정치 시계열.
export async function fetchBreadth(market, days = 30) {
  if (STATIC_DATA) {
    const snapshot = await fetchStaticJson(`data/breadth-${market}.json`)
    const cutoff = isoCutoffDate(days)
    return {
      market,
      days,
      series: (snapshot.series || []).filter((e) => e.date >= cutoff),
    }
  }
  return getJson(`/api/markets/${market}/breadth?days=${days}`)
}

// GET /api/markets/breadth/live -> { kospi: {...}|null, kosdaq: {...}|null, cached_at }
// — 장중 온디맨드(60초 서버 캐시). 정적 모드에서는 라이브 소스를 호출할 수 없으므로
// 일별 스냅샷(breadth-{market}.json)의 최신 행으로 대체한다 — 호출부(MarketPage)는
// live 응답이 아닌 것을 `live: false`로 구분해 "장중 잠정치" 라벨을 뗀다.
export async function fetchBreadthLive() {
  if (STATIC_DATA) {
    const [kospi, kosdaq] = await Promise.all([
      fetchStaticJson('data/breadth-kospi.json').catch(() => null),
      fetchStaticJson('data/breadth-kosdaq.json').catch(() => null),
    ])
    const latest = (snap) => {
      const series = snap?.series
      return series && series.length > 0 ? series[series.length - 1] : null
    }
    return { kospi: latest(kospi), kosdaq: latest(kosdaq), cached_at: null, live: false }
  }
  const body = await getJson('/api/markets/breadth/live')
  return { ...body, live: true }
}

// GET /api/groups?type=upjong|theme -> [{name, change_rate, value, market_sum}] —
// 해당 group_type의 최신 날짜 스냅샷 (PLAN.md §4.6 3.6-3 트리맵).
export async function fetchGroups(type = 'upjong') {
  if (STATIC_DATA) {
    return fetchStaticJson(`data/groups-${type}.json`)
  }
  return getJson(`/api/groups?type=${type}`)
}

// GET /api/stocks/search?q=...&limit=15 -> [{code, name, market, is_etf}] — 온디맨드
// API라 정적 스냅샷 대상이 아니다(PLAN.md §6 3.7-2: "온디맨드 API라 스냅샷 대상
// 아님") — STATIC_DATA 분기가 없다. 정적 배포 모드에서는 호출부(StockSearch)가 이
// 함수를 아예 호출하지 않고 검색창을 비활성화된 채로 둔다.
export async function fetchStockSearch(q, limit = 15) {
  return getJson(`/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`)
}

// GET /api/stocks/{code}/series?days=N -> {code, name, market, is_etf, days, prices,
// flows, meta} — 마찬가지로 온디맨드 전용(정적 스냅샷 없음).
export async function fetchStockSeries(code, days = 180) {
  return getJson(`/api/stocks/${code}/series?days=${days}`)
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
