// 날짜 표기 통일 유틸 (사용자 지적: MarketPage 상단 기준일이 "20260716"처럼 원시
// 형태로 노출됨). 화면에 날짜를 보여주는 모든 지점이 이 함수를 거치게 해 표기
// 형식을 한 곳에서만 관리한다 — 백엔드는 필드마다 'YYYYMMDD'/'YYYY-MM-DD'가
// 섞여 오므로(collectors마다 관례가 다름) 프런트에서 흡수한다.
//
// 입력: 'YYYYMMDD' | 'YYYY-MM-DD' | Date 객체
// 출력: 'YYYY-MM-DD' 문자열. 8자리 숫자로 환원되지 않는 값(빈 문자열, 잘못된 형식,
// 파싱 불가한 Date 등)은 원문을 그대로 반환한다 — 화면에서 값이 통째로 사라지는
// 것보다 원문 노출이 디버깅에 낫다는 판단.
export function formatDate(input) {
  if (input === null || input === undefined || input === '') return input

  if (input instanceof Date) {
    if (Number.isNaN(input.getTime())) return input
    const y = input.getFullYear()
    const m = String(input.getMonth() + 1).padStart(2, '0')
    const d = String(input.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
  }

  const digits = String(input).replaceAll('-', '')
  if (/^\d{8}$/.test(digits)) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`
  }
  return input
}

// 억원 표기 통일 유틸 (사용자 피드백: "기관이 0으로 나온다" — 종목 상세 모달에서
// 백만원/100=억원을 정수로 반올림해 표시하다 보니, 중소형주처럼 |값|이 0.5억원
// 미만인 실제 소액 순매수/순매도가 전부 "0억원"으로 뭉개져 "데이터가 0인 것"처럼
// 오해를 샀다. |백만원 값|이 100(=1억원) 미만이면 소수 1자리로 보여 "0.3억원"처럼
// 실제 크기가 드러나게 하고, 값이 정확히 0이면 "0원"(억원이 아님 — 진짜 무거래와
// 반올림 손실을 구분), 그 외(1억원 이상)에는 기존처럼 정수로 반올림한다.
//
// 입력: 백만원 단위 숫자(net_value/cum_net_value 등, market_flow·flow_rank 관례).
// 출력: '0.3억원' | '-0.3억원' | '0원' | '12억원' 형태 문자열. null/undefined는 '-'.
const eokIntFmt = new Intl.NumberFormat('ko-KR')
const eokDecFmt = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

export function formatEok(million) {
  if (million === null || million === undefined) return '-'
  if (million === 0) return '0원'
  if (Math.abs(million) < 100) {
    const scaled = million / 100
    // -0.0 방지: 예를 들어 -4백만원(-0.04억원)은 소수 1자리로 반올림하면 "-0.0억원"처럼
    // 음의 0으로 보인다 — 부호는 있는데 값은 0으로 읽혀 오히려 "0억원" 문제를 다른
    // 모양으로 재현한다. 반올림 결과가 0이면 부호를 버리고 "0.0억원"으로 통일한다
    // (0억원과는 다르게 "반올림하면 0에 가깝지만 실제로는 0이 아니다"를 전달한다).
    const rounded = Math.round(scaled * 10) / 10
    return `${eokDecFmt.format(rounded === 0 ? 0 : scaled)}억원`
  }
  return `${eokIntFmt.format(Math.round(million / 100))}억원`
}

export default formatDate
