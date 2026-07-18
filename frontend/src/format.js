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

export default formatDate
