import { useEffect, useState } from 'react'

// 시장 위험 경보 상단 배너(PLAN.md §5.36, 2026-07-28 사용자 요청: "하락폭 혹은
// 볼륨이 크면 뭔가 이슈를 알려줘야 하지 않을까? ... 상단 배너로 말야..").
//
// 순수 프레젠테이션 컴포넌트다 — props로 `risk`(GET /api/markets/index-tiles/live
// 응답의 risk 필드, `app/quant/risk_alert.py::classify_market_risk`의 반환 모양
// 그대로: `{kospi, kosdaq, kospi_sidecar, kosdaq_sidecar, flow_slope, alerts,
// has_data}`)만 받는다. 새 fetch·새 polling 없음 — DashboardPage의 기존 1분
// 티어가 이미 채워 넣는 `indexTilesLive.risk`를 그대로 넘겨받는다(별도
// setInterval 신설 금지 원칙, DashboardPage.jsx loadIndexTilesLive 주석 참고).
// `alerts` 배열에 이미 kind별로 병합·정렬돼 있으므로 이 컴포넌트는 `risk.
// flow_slope`(조합별 원본 판정)를 직접 읽지 않는다 — `describeAlert`/`tierOf`
// 만 새 kind(`flow_slope`)를 처리하도록 확장하면 된다(prop 모양 자체는 안 바뀜).
//
// **관찰형 문구 원칙(절대 단정하지 않는다)**: 서킷브레이커/사이드카는 KRX
// 공식 제도이고 임계값(8/15/20%, ±5%)도 실제 규정 수치지만, 이 프로젝트는
// 1분봉 종가 스냅샷으로 "그 임계값에 도달했는지"를 근사 관찰할 뿐 KRX의 실제
// 발동 선언(정확한 1분 지속 판정)을 수신하는 공식 채널이 아니다. 그래서 이
// 배너는 절대 "발동되었습니다"라고 단정하지 않고 "발동 기준에 해당하는
// 변동폭이 관찰됨"처럼 항상 관찰형으로만 서술한다(`app/quant/risk_alert.py`
// 모듈 docstring과 동일한 house rule, §5 "관찰 사실만 서술" 원칙).
//
// 거래량 급증은 KRX 공식 제도가 아니라 이 프로젝트 자체 휴리스틱
// (`quant/volume_surge.py`, §5.24)이라 서킷브레이커/사이드카와 섞이지 않도록
// 항상 "공식 서킷브레이커/사이드카와 무관한 자체 관찰 지표"라는 문구를 붙이고,
// 톤도 낮춘 info 색(index.css `--risk-info-*`)으로 시각적으로 구분한다.
//
// 수급 가속도(flow_slope) 경보도 같은 급이다(PLAN.md §5.36-4, 2026-07-28 같은
// 대화 중 추가 요청: "평소보다 외인 혹은 기관의 기울기가 낮으면 위험 경고를
// 보내주는것도 추가해봐.."). §5.17 `quant/flow_acceleration.py`가 계산해 둔
// 30분 구간 순매수 변화량("기울기")을 재분류한 것뿐이라 KRX 공식 제도가 아닌
// 자체 관찰치다 — "매도가 계속될 것"이라는 예측이 아니라 "방금까지 매도
// 속도가 빨라졌다"는 과거 사실만 서술해야 한다(`app/quant/risk_alert.py::
// _classify_flow_slope` docstring과 동일한 house rule). 톤도 거래량 급증과
// 동일하게 info로 둔다.

const CIRCUIT_BREAKER_THRESHOLD_PCT = { 1: 8, 2: 15, 3: 20 }
const KOSPI_SIDECAR_THRESHOLD_PCT = 5

const MARKET_LABEL = { kospi: '코스피', kosdaq: '코스닥' }

// PLAN.md §5.84(2026-09-10, 사용자 지적 — "x로 닫지 않으면 언제부터 표시
// 했는지 알 수 없어") — 백엔드는 이 배너 값을 매 폴링(60초, `DashboardPage.
// jsx`의 `BREADTH_LIVE_POLL_MS`) 그때그때 새로 계산할 뿐 저장하지 않는다
// (`app/quant/risk_alert.py`도 순수 함수, 어떤 스냅샷도 알림별 발생 시각을
// 남기지 않음). 그래서 "이 알림이 정확히 언제부터 활성이었는지"는 서버에
// 물어도 알 수 없다 — 이 컴포넌트가 폴링을 받는 동안 직접 관찰해서 기억해
// 두는 수밖에 없다. `localStorage`에 알림 종류별 최초 관측 시각을 남겨서
// 새로고침해도 유지되게 한다(§5.36-3의 "닫기" 기억은 세션이면 충분하다고
// 지시받았지만, 이건 다른 목적 — 닫아도/새로고침해도 "언제부터"는 안
// 잃어버려야 의미가 있다).
const FIRST_SEEN_STORAGE_KEY = 'riskAlertFirstSeenV1'
// 탭이 이 시간 이상 닫혀 있다 다시 열려서 우연히 같은 알림이 활성이면
// "계속 이어진 것"이 아니라 "새로 다시 뜬 것"으로 본다(60초 폴링 간격보다
// 훨씬 여유 있게 잡음 — 정상적인 연속 관측에서는 절대 안 걸림).
const FIRST_SEEN_STALE_GAP_MS = 5 * 60 * 1000

function alertIdentity(alert) {
  if (alert.kind === 'flow_slope') return `flow_slope:${alert.market}:${alert.investor}`
  return `${alert.kind}:${alert.market ?? ''}`
}

function loadFirstSeenStore() {
  try {
    const raw = localStorage.getItem(FIRST_SEEN_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {} // 프라이빗 모드 등으로 접근 불가해도 배너 자체는 정상 동작해야 함
  }
}

function saveFirstSeenStore(store) {
  try {
    localStorage.setItem(FIRST_SEEN_STORAGE_KEY, JSON.stringify(store))
  } catch {
    // 저장 실패는 무시 — "언제부터"를 못 남길 뿐 배너 표시 자체는 계속됨
  }
}

function fmtFirstSeen(ms) {
  if (!ms) return null
  return new Date(ms).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
}

function fmtPct(value) {
  if (value === null || value === undefined) return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function fmtMultiple(value) {
  if (value === null || value === undefined) return '-'
  return `${value.toFixed(1)}배`
}

// 심각도(alert.severity, risk_alert.py가 이미 정렬해 준 값)를 배너 색 등급
// 3단계로 매핑한다 — 서킷브레이커 2·3단계만 danger(가장 강한 톤), 1단계와
// 사이드카는 KRX 공식 제도이긴 하나 상대적으로 덜 급박해 warn, 거래량 급증·
// 수급 가속도(flow_slope)는 둘 다 KRX 공식 제도가 아닌 자체 관찰 지표라
// 항상 info(모듈 상단 주석 "관찰형 문구 원칙" 참고).
function tierOf(alert) {
  if (alert.kind === 'circuit_breaker') return alert.level >= 2 ? 'danger' : 'warn'
  if (alert.kind === 'sidecar') return 'warn'
  return 'info'
}

// 각 alert 항목(circuit_breaker/sidecar/volume_surge/flow_slope)을 관찰형
// 한국어 문장으로 옮긴다 — "발동되었습니다" 단정 대신 "발동 기준에 해당하는
// 변동폭이 관찰됨". `firstSeenMs`(선택, PLAN.md §5.84)가 있으면 이 브라우저가
// 언제부터 이 알림을 계속 관측해 왔는지 뒤에 덧붙인다.
function describeAlert(alert, firstSeenMs) {
  let text
  if (alert.kind === 'circuit_breaker') {
    const threshold = CIRCUIT_BREAKER_THRESHOLD_PCT[alert.level]
    const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
    text =
      `${marketLabel} ${fmtPct(alert.change_rate)} — 서킷브레이커 ${alert.level}단계 발동 기준` +
      `(-${threshold}%)에 해당하는 변동폭이 관찰됨`
  } else if (alert.kind === 'sidecar') {
    text =
      `코스피200 선물 ${fmtPct(alert.change_rate)} — 사이드카(프로그램매매 호가효력 정지) 발동 기준` +
      `(±${KOSPI_SIDECAR_THRESHOLD_PCT}%)에 해당하는 변동폭이 관찰됨`
  } else if (alert.kind === 'volume_surge') {
    const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
    text =
      `${marketLabel} 거래량이 평소(직전 30분) 대비 ${fmtMultiple(alert.multiple)} 수준으로 튀었음 — ` +
      `공식 서킷브레이커/사이드카와 무관한 자체 관찰 지표(참고용)`
  } else {
    // flow_slope — §5.17 flow_acceleration.py가 계산해 둔 30분 구간 순매수
    // 변화량("기울기")을 재분류한 값. "매도가 계속될 것"이라는 예측이 아니라
    // 방금까지 관찰된 매도 속도 변화만 서술한다(risk_alert.py 모듈 docstring
    // "정직성 원칙"과 동일한 house rule).
    const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
    text =
      `${marketLabel} ${alert.investor} 매도 속도가 직전 30분 대비 ${fmtMultiple(alert.multiple)} 빨라짐(관찰됨) — ` +
      `공식 지표가 아닌 미검증 자체 관찰치이며, 매도가 계속될 것이라는 예측이 아님(참고용)`
  }

  const firstSeenLabel = fmtFirstSeen(firstSeenMs)
  // "이 브라우저에서" — 서버가 발생 시각을 기록해 둔 게 아니라 이 탭이 폴링
  // 중 직접 관측해 기억해 둔 값이라는 걸 분명히 한다(다른 기기/새 탭에서는
  // 다시 처음부터 관측 시작).
  return firstSeenLabel ? `${text} — 이 브라우저에서 ${firstSeenLabel}부터 관측` : text
}

export default function RiskAlertBanner({ risk }) {
  // 세션 내 닫기 + 악화 시 재노출(PLAN.md §5.36-3): "지금 화면에서 사용자가
  // 마지막으로 닫은 심각도"만 기억한다. 이후 렌더에서 현재 최고 심각도가 이
  // 값보다 높아지면(더 나쁜 상황으로 악화) 다시 노출하고, 그 이하로 유지되거나
  // 완화되면 계속 숨긴다 — 새로고침해도 재노출하고 싶다면 여기에 저장소
  // (localStorage 등)를 붙이면 되지만, 지시대로 세션(컴포넌트 로컬 상태)이면
  // 충분하다.
  const [dismissedSeverity, setDismissedSeverity] = useState(null)
  // PLAN.md §5.84 — 알림별(kind/market/investor) 최초 관측 시각. 서버는 이
  // 값을 어디에도 기록하지 않으므로(위 "FIRST_SEEN_STORAGE_KEY" 주석 참고)
  // 이 브라우저가 폴링으로 직접 지켜본 것만 기억한다.
  const [firstSeenStore, setFirstSeenStore] = useState(loadFirstSeenStore)

  const alerts = risk?.alerts ?? []

  useEffect(() => {
    const now = Date.now()
    setFirstSeenStore((prev) => {
      const next = {}
      let changed = alerts.length !== Object.keys(prev).length
      for (const alert of alerts) {
        const key = alertIdentity(alert)
        const prevEntry = prev[key]
        const isFresh = !prevEntry || now - prevEntry.lastSeen > FIRST_SEEN_STALE_GAP_MS
        next[key] = { firstSeen: isFresh ? now : prevEntry.firstSeen, lastSeen: now }
        if (isFresh) changed = true
      }
      if (!changed) return prev
      saveFirstSeenStore(next)
      return next
    })
  }, [alerts])

  if (alerts.length === 0) return null

  const [topAlert, ...restAlerts] = alerts
  if (dismissedSeverity !== null && topAlert.severity <= dismissedSeverity) return null

  const tier = tierOf(topAlert)
  const firstSeenOf = (alert) => firstSeenStore[alertIdentity(alert)]?.firstSeen

  return (
    <div className={`risk-banner risk-banner--${tier}`} role="alert">
      <div className="risk-banner-main">
        <span className="risk-banner-icon" aria-hidden="true">
          ⚠
        </span>
        <span className="risk-banner-headline">{describeAlert(topAlert, firstSeenOf(topAlert))}</span>
        <button
          type="button"
          className="risk-banner-dismiss"
          onClick={() => setDismissedSeverity(topAlert.severity)}
          aria-label="위험 경보 배너 닫기"
          title="닫기 (더 심각한 상황으로 바뀌면 다시 표시됩니다)"
        >
          ×
        </button>
      </div>
      {restAlerts.length > 0 && (
        <div className="risk-banner-secondary">
          그 외 동시 관찰된 조건: {restAlerts.map((a) => describeAlert(a, firstSeenOf(a))).join(' · ')}
        </div>
      )}
      <div className="risk-banner-disclaimer">
        1분봉 종가 기준 임계값 근사 관찰이며, KRX의 실제 공식 발동 선언(정확한 지속시간·시각 판정)을
        직접 수신한 것은 아닙니다.
      </div>
    </div>
  )
}
