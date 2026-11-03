// 마감 후 ~ 일일 배치 전 "어제 마감 정보" 문제 대응용 순수 헬퍼.
// 백엔드 *-live 엔드포인트는 마감 후에도 오늘 마지막 값을 market_closed:true로 돌려주지만,
// 휴장일에도 같은 응답(오늘 날짜)이 올 수 있어 날짜만으로 섞으면 baseDate가 부풀려진다.
// 따라서 "지수 라이브 bar의 date가 EOD 마지막 봉 date보다 최신"일 때만(=실제 거래일이
// 열렸고 EOD가 아직 못 따라온 경우) 마감 후 라이브를 쓴다. 휴장일엔 지수 bar date =
// 마지막 거래일 = EOD라 폴백이 유지된다.
import { formatDate } from './format'

// 'YYYYMMDD' | 'YYYY-MM-DD' → 'YYYY-MM-DD' (정규화 실패 시 null)
function normDate(d) {
  const f = formatDate(d)
  return typeof f === 'string' && f.length === 10 ? f : null
}

// 라이브 날짜가 폴백 날짜보다 엄밀히 최신인가 (폴백 날짜가 없으면 라이브 날짜가 유효하면 true)
export function isNewerDate(liveDate, fallbackDate) {
  const l = normDate(liveDate)
  if (!l) return false
  const f = normDate(fallbackDate)
  return f === null || l > f
}

// 마감 후(장중 아님)에도 라이브 지수 row를 쓸지 판정
export function shouldUseClosedLiveIndexRow(liveRow, fallbackRow) {
  return Boolean(liveRow) && isNewerDate(liveRow.date, fallbackRow?.date)
}
