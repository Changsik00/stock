import { useState } from 'react'

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
// 변동폭이 관찰됨".
function describeAlert(alert) {
  if (alert.kind === 'circuit_breaker') {
    const threshold = CIRCUIT_BREAKER_THRESHOLD_PCT[alert.level]
    const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
    return (
      `${marketLabel} ${fmtPct(alert.change_rate)} — 서킷브레이커 ${alert.level}단계 발동 기준` +
      `(-${threshold}%)에 해당하는 변동폭이 관찰됨`
    )
  }
  if (alert.kind === 'sidecar') {
    return (
      `코스피200 선물 ${fmtPct(alert.change_rate)} — 사이드카(프로그램매매 호가효력 정지) 발동 기준` +
      `(±${KOSPI_SIDECAR_THRESHOLD_PCT}%)에 해당하는 변동폭이 관찰됨`
    )
  }
  if (alert.kind === 'volume_surge') {
    const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
    return (
      `${marketLabel} 거래량이 평소(직전 30분) 대비 ${fmtMultiple(alert.multiple)} 수준으로 튀었음 — ` +
      `공식 서킷브레이커/사이드카와 무관한 자체 관찰 지표(참고용)`
    )
  }
  // flow_slope — §5.17 flow_acceleration.py가 계산해 둔 30분 구간 순매수
  // 변화량("기울기")을 재분류한 값. "매도가 계속될 것"이라는 예측이 아니라
  // 방금까지 관찰된 매도 속도 변화만 서술한다(risk_alert.py 모듈 docstring
  // "정직성 원칙"과 동일한 house rule).
  const marketLabel = MARKET_LABEL[alert.market] ?? alert.market
  return (
    `${marketLabel} ${alert.investor} 매도 속도가 직전 30분 대비 ${fmtMultiple(alert.multiple)} 빨라짐(관찰됨) — ` +
    `공식 지표가 아닌 미검증 자체 관찰치이며, 매도가 계속될 것이라는 예측이 아님(참고용)`
  )
}

export default function RiskAlertBanner({ risk }) {
  // 세션 내 닫기 + 악화 시 재노출(PLAN.md §5.36-3): "지금 화면에서 사용자가
  // 마지막으로 닫은 심각도"만 기억한다. 이후 렌더에서 현재 최고 심각도가 이
  // 값보다 높아지면(더 나쁜 상황으로 악화) 다시 노출하고, 그 이하로 유지되거나
  // 완화되면 계속 숨긴다 — 새로고침해도 재노출하고 싶다면 여기에 저장소
  // (localStorage 등)를 붙이면 되지만, 지시대로 세션(컴포넌트 로컬 상태)이면
  // 충분하다.
  const [dismissedSeverity, setDismissedSeverity] = useState(null)

  const alerts = risk?.alerts ?? []
  if (alerts.length === 0) return null

  const [topAlert, ...restAlerts] = alerts
  if (dismissedSeverity !== null && topAlert.severity <= dismissedSeverity) return null

  const tier = tierOf(topAlert)

  return (
    <div className={`risk-banner risk-banner--${tier}`} role="alert">
      <div className="risk-banner-main">
        <span className="risk-banner-icon" aria-hidden="true">
          ⚠
        </span>
        <span className="risk-banner-headline">{describeAlert(topAlert)}</span>
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
          그 외 동시 관찰된 조건: {restAlerts.map(describeAlert).join(' · ')}
        </div>
      )}
      <div className="risk-banner-disclaimer">
        1분봉 종가 기준 임계값 근사 관찰이며, KRX의 실제 공식 발동 선언(정확한 지속시간·시각 판정)을
        직접 수신한 것은 아닙니다.
      </div>
    </div>
  )
}
