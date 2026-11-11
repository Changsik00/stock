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

// 환율 표시값/전일비 기준 선택. 라이브 값이 숫자이고 (장중이거나 라이브 date가 EOD 마지막
// date보다 최신)이면 라이브를 쓴다. prev는 라이브 date가 EOD 마지막보다 최신일 때 EOD 마지막 값
// (=직전 거래일 종가), 같으면(배치가 이미 오늘 값을 넣음) EOD 끝에서 두 번째 값이다.
// 반환: { active, value, prev, isOpen, date } — active=false면 호출 측이 EOD 값으로 폴백한다.
export function pickFxDisplay(fxLive, { eodDate, eodLatest, eodPrev }) {
  const value = fxLive?.usdkrw?.value
  if (typeof value !== 'number') return { active: false }
  const isOpen = fxLive.market_closed === false
  const liveDate = fxLive.usdkrw.date
  const newer = isNewerDate(liveDate, eodDate)
  if (!isOpen && !newer) return { active: false }
  return {
    active: true,
    value,
    prev: newer ? eodLatest : eodPrev,
    isOpen,
    date: liveDate ?? null,
  }
}

// 마감 후 선물수급 라이브 사용 여부 — 거래일 증거(지수 라이브 사용)가 있고, 외국인 net_value가
// 숫자이며, 라이브 date가 EOD 선물수급 date보다 최신일 때만. 반환: { value, date } | null
export function pickClosedFuturesFlow(futuresFlowLive, { closedLiveIndexUsed, eodDate }) {
  if (!closedLiveIndexUsed || futuresFlowLive?.market_closed !== true) return null
  const value = futuresFlowLive.investors?.['외국인']?.net_value
  if (typeof value !== 'number') return null
  if (!isNewerDate(futuresFlowLive.date, eodDate)) return null
  return { value, date: futuresFlowLive.date }
}

// 업종·테마 라이브 rows 사용 여부 — 장중이면 type 일치 시, 마감 후에는 거래일 증거가 있고
// type 일치 + rows 비어있지 않을 때만 true.
export function shouldUseGroupLive(groupLive, groupType, closedLiveIndexUsed) {
  if (!groupLive || groupLive.type !== groupType) return false
  if (groupLive.market_closed === false) return true
  return Boolean(closedLiveIndexUsed) && groupLive.market_closed === true && Array.isArray(groupLive.rows) && groupLive.rows.length > 0
}

// 마감 후 외인 현물 타일용 — 장중이 아니고 마감 후 라이브 수급 요약에 '외국인' 숫자가 있으면
// { value, date }, 아니면 null (호출 측이 EOD 값으로 폴백). 요약 자체의 거래일 증거 판정은
// flowClosedLiveSummary가 이미 수행한다.
export function pickClosedForeignSpot(closedSummary) {
  const value = closedSummary?.values?.['외국인']
  if (typeof value !== 'number') return null
  return { value, date: closedSummary.date }
}

// 베이시스 타일 표시 객체 선택. 장중(market_closed===false)이고 basis가 숫자면 라이브(isOpen),
// 마감 후에는 거래일 증거가 있고 basis가 숫자이며 라이브 date가 EOD보다 최신일 때만 라이브.
// 반환: { basis, backwardation, date, isOpen } | null — null이면 호출 측이 EOD로 폴백한다.
export function pickClosedBasis(basisLive, { closedLiveIndexUsed, eodDate }) {
  if (!basisLive || typeof basisLive.basis !== 'number') return null
  const base = { basis: basisLive.basis, backwardation: basisLive.backwardation, date: basisLive.date ?? null }
  if (basisLive.market_closed === false) return { ...base, isOpen: true }
  if (!closedLiveIndexUsed || basisLive.market_closed !== true) return null
  if (!isNewerDate(basisLive.date, eodDate)) return null
  return { ...base, isOpen: false }
}
