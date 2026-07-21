# 수급 분석 대시보드 구축 계획

> 2026-07-15 조사 기준. 키움증권 REST API 공식 문서, 한국투자증권(KIS) 공식 GitHub,
> ECOS 실호출 검증 결과를 바탕으로 작성.

## 0. 목표

로그인 없이 브라우저만 열면 바로 보이는 **나만의 수급 분석 대시보드**.
(증권사 API 키는 서버에 한 번만 심어두고, 사용자는 로그인 절차 없이 차트만 봄)

요구 기능:

1. 코스피 / 코스닥 / 선물 **투자자별 수급** (개인·외국인·기관 순매수) — 일별 시계열 차트
2. **ETF 수급 분석**
3. **원하는 종목** 분석 (검색 → 가격 + 수급 차트)
4. **주포(세력) 매집/분산 감지** 퀀트 시그널
5. **환율(USD/KRW), 유가(WTI 등)** 차트
6. 전부 **차트**로 — 상승/하락 추세가 한눈에 보이게
7. **시장 등락 현황(breadth)** — 코스피/코스닥 전 종목 중 지금 오르는/내리는 종목 수 (장중 + 일별 시계열) *(2026-07-17 추가)*
8. **시장 자금·대차 지표** — 투자자예탁금, 신용융자 잔고, **대차잔고** (시장 전체 수급의 힘) *(2026-07-17 추가)*

---

## 1. 핵심 질문: 키움증권으로 되는가?

**부분적으로 된다.** 키움 REST API(2025-03 출시, openapi.kiwoom.com)는 기존 OCX 방식과
달리 순수 HTTP/WebSocket이라 **Mac에서도 동작**하고, 실계좌 + 앱키 발급만 하면 무료다.
그러나 전부 커버하지는 못해서 **소스 조합**이 필요하다.

### 키움 REST API로 되는 것 ✅

| 기능 | TR ID |
|---|---|
| 종목별 투자자·기관별 수급 (일별) | `ka10059` 종목별투자자기관별, `ka10061` 합계, `ka10060` 차트용 |
| 외국인/기관 종목별 매매동향 | `ka10008`, `ka10009`, `ka10131` 기관외국인연속매매현황 |
| 프로그램 매매 (주포 감지 핵심 재료) | `ka90004` 종목별, `ka90010` 일자별 추이, `ka90013` 종목일별 |
| 업종별 투자자 순매수 (시장구분 입력) | `ka10051` |
| 장중/장마감 투자자별 매매 | `ka10063`, `ka10066` |
| ETF 시세/NAV/추이 | `ka40001`~`ka40010`, 실시간 `0G`(NAV) |
| 실시간 시세 (WebSocket) | `0B` 체결, `0D` 호가잔량, `0F` 당일거래원, `0w` 프로그램매매 |
| 차트 데이터 (일/분봉) | 차트 카테고리 TR |

### 키움 REST API로 안 되는 것 ❌

| 기능 | 대안 |
|---|---|
| **선물(K200) 투자자별 수급** — REST API에 선물옵션 도메인 자체가 없음 | KIS API 또는 KRX 크롤링 (아래 §2) |
| 환율/유가 | ECOS / yfinance (§3) |

> **2026-07-19 갱신**: "시장 전체(코스피/코스닥) 투자자별 순매수 일별 시계열
> 전용 TR 없음" 판단을 재검증 — `ka10051`(업종별투자자순매수,
> `/api/dostk/sect`)을 실호출 확정한 결과 **"비효율한 우회"가 아니라 유력한
> pykrx 대체 후보**로 재평가됨. 첫 번째 응답 행이 `inds_cd="001_AL"/"101_AL"`
> 종합(시장 전체) 집계이고, 13종 투자자 분류(개인/외국인/기관 세부 —
> 보험·투신·은행·연기금·사모펀드 등)를 제공하며, `base_dt` 파라미터로 과거
> 임의 일자 조회가 가능함을 확인(`backend/app/clients/kiwoom.py` docstring
> "ka10051 추가 검증" 절 참고). 날짜당 1콜이지만 rate limit(1 req/s) 기준
> 3년 백필(~750영업일)도 약 12분이면 끝남 — `KRX_ID`/`PW` 로그인 의존 없이
> 시장 수급을 확보할 수 있는 경로. **1-4(pykrx 기반 market_flow)의 소스
> 교체 여부는 별도 의사결정 필요**(pykrx는 13분류가 이미 있어 당장 급하지
> 않음 — 아래 §7 리스크 표 참고).

### 이용 조건
- 키움증권 **실계좌 필요** (비대면 개설 가능), 모의투자 지원(국내주식만)
- 포털에서 서비스 신청 → IP 등록 → 앱키/시크릿 발급. 토큰 유효 24시간
- Rate limit 공식 미공개 (커뮤니티 관측치 TR당 약 1건/초 ~ 20건/초로 상충 → 실측 필요, 보수적으로 설계)

---

## 2. 타 기관(대안 소스) 비교 — 조사 결과

| 소스 | 시장 수급 | 종목 수급 | 선물 수급 | ETF 수급 | 비고 |
|---|---|---|---|---|---|
| **키움 REST** | △ (우회) | ✅ 상세 | ❌ | 시세만 (수급은 종목 TR로) | 프로그램매매·거래원·실시간 강점 |
| **KIS (한국투자증권)** | ✅ `FHPTJ04040000` (KSP/KSQ 일별) | ✅ `FHKST01010900` (개인/외인/기관 3분류) | 전용 TR 없음 (`FHPTJ04030000` 원본 화면이 선물 포함 — 시장구분 코드 확인 필요) | ✅ (시장코드 J에 ETF 포함) | 실계좌 필요. **20건/초**로 제한 명확. 공식 GitHub 예제 풍부 |
| **pykrx** (data.krx.co.kr 크롤링) | ✅ **13개 투자자 분류** (연기금·금융투자·사모 등) | ✅ | ❌ | ✅ | 무인증·무료지만 비공식 크롤링. 2026-02 KRX 개편으로 전면 장애 이력, IP 차단 위험 → **보조/검증용** |
| KRX 공식 Open API | ❌ 시세만 | ❌ | ❌ | ❌ | 현재 프로젝트가 쓰는 것. 수급 데이터 자체가 없음 (README에 기록된 그대로) |
| 공공데이터포털 (금융위) | ❌ 시세만 | ❌ | ❌ | ❌ | 탈락 |
| 네이버 금융 크롤링 | ✅ (일별 10분류) | △ | △ (비공식 페이지) | ❌ | 백업용으로만 |

### 소스 조합 결정 (권장)

- **시장(코스피/코스닥) 수급**: KIS `FHPTJ04040000` (공식·일별) + pykrx로 13분류 상세(연기금 따로 보기) 보강
- **선물 수급**: KIS `FHPTJ04030000` 시장구분 코드로 1차 시도 → 안 되면 pykrx 아닌 KRX 파생 통계 페이지 파싱 또는 네이버 백업
- **종목·ETF 수급 + 프로그램매매 + 실시간**: **키움 REST** (TR이 가장 풍부)
- **환율/유가**: §3

> 계좌 두 개(키움 + 한투)를 파는 게 부담이면: **키움 단독 + pykrx 보조**로도 시장/선물
> 수급을 메꿀 수 있다 (pykrx가 시장 13분류를 제공하므로). 단 pykrx 장애 리스크를 안고 가는 트레이드오프.

---

## 3. 환율·유가 (조사 검증 완료)

| 데이터 | 1순위 | 백업 |
|---|---|---|
| USD/KRW 일별 | **네이버 marketIndex API** (무키) — `m.stock.naver.com/front-api/marketIndex/prices?category=exchange&reutersCode=FX_USDKRW` (User-Agent 필요, pageSize 최대 60, 페이징으로 과거 소급 — 3년 백필 실측 완료 2026-07-19, clients/naver_fx.py). 실패 시 FRED `DEXKOUS`(무키 CSV) 자동 폴백 | 한국은행 ECOS API `731Y001/D/0000001` (키 필요 — 정밀 공식 소스 옵션, clients/ecos.py 유지), 한국수출입은행 API |
| WTI/브렌트 일별 | **yfinance** `CL=F`, `BZ=F` — 현재 정상 동작 확인. 단 비공식이라 429 사태 전력(2024~2025) → **하루 1회 배치 + DB 캐싱 필수** | FRED 공식 CSV `DCOILWTICO`, `DCOILBRENTEU` (무키) |
| 두바이유 일별 | 무료 공식 API 없음. 오피넷 웹 파싱(T+1) 또는 월별(ECOS `902Y003`)로 타협 | — |

---

## 3.5 시장 자금·대차잔고 + 등락 종목수 (2026-07-17 추가)

### KOFIA freesis (freesis.kofia.or.kr) — 시장 자금·대차 통계

| 데이터 | 의미 | 비고 |
|---|---|---|
| 투자자예탁금 | 증시 대기 자금 → 유동성 방향 | 일별, T+1 |
| 신용융자 잔고 | 개인 레버리지 → 과열/투매 판단 | 일별, 코스피/코스닥 구분 |
| **대차거래 잔고** | 공매도 대기 물량 — **시장 전체 수급의 힘** | 일별, 체결/상환/잔고(주수·금액) |

- 무인증·무료. 통계 화면이 POST로 데이터를 내려주는 구조라 파싱 부담이 작다.
  단 비공식이므로 pykrx처럼 개편 리스크 있음 → collect_log로 실패 감지 (§7)
- 적재: `macro_series` 테이블 재사용 — series: `investor_deposit`, `credit_loan_kospi`,
  `credit_loan_kosdaq`, `lending_balance`(대차잔고 금액) 등, source=`kofia`
- **종목별** 대차·공매도는 별도로 키움 대차거래 TR 사용 (§4 시그널 6 — 변경 없음).
  freesis는 시장 전체 잔고, 키움은 종목 단위로 역할 분담

### 등락 종목수 (breadth) — 키움 ka20001

- `ka20001` 업종현재가 (업종코드 001=종합KOSPI, 101=종합KOSDAQ) 요청 1건으로
  상승/상한/보합/하락/하한 종목수를 받는 방식 — **2026-07-19 실전 키로 실호출
  확정**(`backend/app/clients/kiwoom.py` docstring "Phase 1.5-1 probe 실측
  확정" 참고). 필드명 `rising`(상승)/`stdns`(보합)/`fall`(하락)/`upl`(상한)/
  `lst`(하한). 같은 날 장중 실측값이 네이버 breadth와 **완전 일치**:
  KOSPI `rising=384, stdns=40, fall=488, upl=6` ↔ 네이버 384↑/40—/488↓/상한6,
  KOSDAQ `rising=501, stdns=56, fall=1182` ↔ 네이버 501↑/56—/1182↓.
- 결론: `ka20001`이 breadth 정밀 소스로 채택 가능함이 확정됨. 다만 §1.5-3/
  §3.6-2가 이미 네이버 임시 소스로 동작 중이라 교체는 급하지 않은 정밀화
  작업으로 남겨둠(값이 일치하므로 교체 효과는 "공식 소스로의 신뢰성 강화"
  수준 — 데이터 자체는 이미 맞음).
- 적재: `market_breadth` (market, date) — adv/dec/flat/limit_up/limit_down.
  장중 값은 DB에 쌓지 않고 온디맨드 프록시(짧은 캐시)로 제공

---

## 4. 주포(세력) 감지 퀀트 — 시그널 설계

"세력이 지금 사는가/파는가"를 단일 지표로 알 수는 없으므로, **여러 신호를 점수화**해서
매집(Accumulation) / 분산(Distribution) 게이지로 보여준다.

### 재료 (전부 키움 REST로 수집 가능)

1. **기관·외국인 연속 순매수** (`ka10131`, `ka10059`) — N일 연속 순매수 + 순매수 금액 증가
2. **프로그램 매매 추이** (`ka90013` 종목일별) — 비차익 순매수 누적이 우상향인가
3. **누적 순매수 vs 주가 다이버전스** — 주가는 횡보/하락인데 기관+외인 누적 순매수가
   증가하면 매집 의심 (전형적 매집 패턴)
4. **거래원 분석** (실시간 `0F` 당일거래원) — 특정 창구 집중 매수
5. **OBV / 거래량 급증일의 양봉·음봉 비율** — 차트 데이터에서 계산
6. **대차거래·공매도 추이** (키움 대차거래 카테고리) — 공매도 감소 + 순매수 증가 조합

### 산출물

- 종목 페이지 상단에 **매집/분산 스코어** (-100 ~ +100 게이지)
- 근거 시그널 목록 (예: "외국인 7일 연속 순매수", "프로그램 비차익 20일 누적 +320억")
- 캔들차트 아래 **누적 순매수 라인 오버레이** — 다이버전스를 눈으로 확인

> 주의: 이 스코어는 참고 지표다. 데이터가 일별(T+0 장마감 후 확정)이라
> "지금 이 순간"은 장중 잠정치(`ka10063`, 실시간 `0w`)로 보완한다.

---

## 4.5 수급 경로 분석 — ETF look-through (2026-07-18 추가)

**질문**: 요즘 수급이 ETF로 몰린다. (a) 오늘 순매수가 몰린 상위 종목은 무엇인가(개별주냐 ETF냐),
(b) ETF로 들어온 돈은 결국 어떤 개별 종목을 사는 것인가, (c) 특정 종목 기준으로
"직접 매수 + ETF 경유 매수"를 합쳐 누가(개인/외인/기관) 얼마나 사는지 — **수급의 경로**를 본다.

### 방법론

1. **수급 상위 랭킹**: 투자자별(외인/기관) 순매수 상위 N 종목을 일별 수집, `stocks.is_etf`로 개별주/ETF 태깅
2. **ETF 구성 매핑**: ETF 목록 + 구성종목·비중(PDF)을 일별 적재
3. **경로 분해(look-through)**: 종목 S의 ETF 경유 유입 ≈ Σ_E [ ETF E의 순유입 × E 내 S 비중 ]
   - 1차 근사: ETF **유통시장 순매수 금액** × 비중 (즉시 가능, 과대추정 위험)
   - 2차 정밀화: ETF **설정/환매(좌수 증감 × NAV)** × 비중 — 실제 실물 바스켓 유입에 근접.
     네이버가 ETF별 누적 순유입(`cumulativeNetInflowList`)을 제공하는 것 실확인(2026-07-18) → 이걸 우선 활용
4. **산출 화면**: 시장 탭 "수급 상위" 테이블(개별/ETF 배지) · 종목 페이지 "직접 vs ETF 경유" 스택 차트 + 기여 상위 ETF 목록 · 시장 단위 "ETF로 들어온 수급 총량 vs 직접 수급" 비교

### 소스 (2026-07-18 실호출 검증)

| 데이터 | 1순위 | 대안 | 검증 |
|---|---|---|---|
| ETF 목록·AUM·NAV | 네이버 `api/sise/etfItemList.nhn` (1,146종목, 무키) | 키움 ka40004 | ✅ 실확인 |
| ETF 구성종목 비중 | 네이버 `m.stock.naver.com/api/stock/{code}/etfAnalysis` — **상위 10개 + 비중** | 전체 구성: KIS `ETF 구성종목시세` TR(무료 앱키) 또는 Seibro 파싱(Playwright 방식 확보) 또는 키움 ETF TR probe | ✅ top10 실확인 |
| ETF 순유입(설정/환매 기반) | 네이버 etfAnalysis `cumulativeNetInflowList` | 좌수 증감 자체 계산(KRX/운용사) | ✅ 필드 실확인(스키마 상세 미분석) |
| 순매수 상위 종목 | 네이버 `sise_deal_rank.naver` (외인/기관, 무키) | 키움 순위정보 TR(키 해결 후 교체 — 투자자 분류 상세) | ✅ 페이지 200 |
| 종목별 투자자 수급(직접분) | 키움 ka10059 (Phase 2-3) | 네이버 종목 투자자별 페이지 | 키움 키 대기 |

### 한계·주의

- **ETF 유통시장 순매수 ≠ 구성종목 실매수**: 유통시장 손바뀜만으로는 바스켓 매수가 없고, LP가
  선물로 헤지할 수도 있음 → 유통 순매수 기반(1차)과 순유입 기반(2차)을 **병기**하고 차이를 표시
- 상위 10개 비중으로 시작(대형 ETF는 top10이 비중 50~60% 커버) → 전체 구성은 KIS/Seibro로 정밀화
- 대상 ETF 유니버스(2026-07-18 개정): **etfTabCode 1/2/3/7(국내 시총식·업종테마·국내파생·혼합) 거래대금 상위 300개**.
  이름 기반 제외(레버리지/인버스 등)는 하지 않는다 — "보유 종목이 말하게 한다" 원칙.
  구 기준(tab 1,2 + 이름 필터 + 상위 100)은 실물 주식을 90%+ 보유하는 단일종목 레버리지
  (KODEX SK하이닉스단일종목레버리지 = 거래대금 전체 1위)까지 배제해 via_etf_net을 심각하게
  과소집계했다. 해외주식(4)/원자재(5)/채권(6)만 후보에서 제외
- **인버스/선물형은 look-through 기여 0이 정상**: top10이 현금·선물뿐이라 etf_holdings에
  행이 안 생기고(주식코드 있는 행만 적재) 자연 탈락한다. 이들의 자금 유입 자체는 추후
  '파생형 ETF 자금' 지표로 별도 표시(§6 3.5-4)
- 알려진 한계: (a) 일부 채권혼합형(예: RISE 삼성전자SK하이닉스채권혼합50)은 주식 행에
  비중이 "-"로 와서 계산 불가 → 제외됨. (b) 파생형 ETF가 다른 ETF를 보유하는 경우
  (KODEX 레버리지 → KODEX 200 20.86%) **1단계 재귀 분해로 해결(2026-07-18)** —
  그 보유를 최종 목적지로 두지 않고 내부 ETF 자신의 구성종목까지 한 번 더
  분해한다(collectors/flow_path.py compute_flow_path). **2단계 이상은 드롭**
  (무한 재귀 방지, collect_log message에 드롭 건수 기록) — 실무상 관측된 체인은
  1단계뿐이라 영향 미미. 최종 flow_path 행에는 어떤 경우에도 ETF 코드가 남지
  않는다(코드 자신이 ETF면 결과에서 제외)
- 비중은 T-1 PDF 기준 — 리밸런싱 당일 오차 존재

### 스키마 추가

| 테이블 | PK | 컬럼 |
|---|---|---|
| `etf_holdings` | (etf_code, date, stock_code) | weight, shares — 일별 구성 스냅샷 |
| `etf_stats` | (code, date) | nav, aum, net_inflow — 순유입 시계열 |
| `flow_rank` | (date, investor, rank) | code, net_value, is_etf — 순매수 상위 스냅샷 |
| `flow_path` | (code, date) | direct_net, via_etf_net, top_etfs JSONB — 배치 계산 캐시 |

## 4.6 시황·자금 집중 대시보드 (2026-07-18 추가)

**요구 (사용자)**: ① 매수+매도 합쳐 **총 거래대금이 큰 종목**(돈이 모이는 곳)을 봐야 하고
② 돈이 몰린 종목이 결국 **올랐는지 내렸는지**, ③ **코스피/코스닥 어느 시장**인지,
④ ETF 경유 수급은 **매수세와 매도세 중 어디가 쌘지**, ⑤ 시장 전체로도 매수세 vs 매도세,
⑥ 시황 분위기 — **전체 종목 중 오른/내린 개수**, ⑦ **업종·테마별 강약을 박스차트(트리맵)**로.

### 구성 (전부 무키 네이버 소스로 가능 — 2026-07-18 1차 검증)

| # | 화면 | 데이터 | 소스 |
|---|---|---|---|
| 3.6-1 | **거래대금 상위 표** — 시장 배지(코스피/코스닥) + ETF 배지 + **등락률(빨/파)** + 거래대금·회전율 | `value_rank` (date, market, rank) — code/name/value/change_rate/is_etf 신설. 겸사겸사 `flow_rank`에도 market 컬럼 추가(수급 상위 표의 시장 구분 — 요구 ③) | 네이버 거래대금 상위 (PC sise 계열 vs 모바일 랭킹 API — 구현 시 실확정) |
| 3.6-2 | **등락 종목수(breadth) 배지** — "코스피 512↑ 40— 380↓" + 등락 비율 미니 막대. 장중은 짧은 캐시 프록시 | `market_breadth` (기존 §3.5 테이블) — 네이버를 임시 소스로 선(先)구현, 키움 키 해결 시 ka20001로 교체(1.5-3과 통합) | 네이버 시장 요약 (후보 복수 — 구현 시 실확정. 최후 폴백: 전 종목 리스트 카운트) |
| 3.6-3 | **업종·테마 트리맵** — 크기=거래대금(시총 토글), 색=등락률 연속 스케일(빨강↔파랑, 한국 관행) | `group_snapshot` (date, type[upjong/theme], name) — change_rate/value/market_sum 신설 | 네이버 sise_group (업종 79·테마 266개 실확인) |
| 3.6-4 | **수급 방향 종합** — (a) ETF 경유 **유출(음수) 상위** 병기 + ETF별 설정(+)/환매(−) 방향 배지, (b) ETF별 외인/기관 매수·매도 크기 비교(flow_rank buy vs sell), (c) 시장 종합 **매수세/매도세 게이지**: 등락 비율 + 외인·기관 순매수 합 + ETF 순유입 합 가중 → -100~+100 | flow_path(음수 포함 — 이미 계산됨, UI만), flow_rank, etf_stats, market_breadth 조합 — 신규 테이블 불필요 | 기존 적재분 |

### 한계·주의

- 네이버 랭킹은 상위 N만 제공 → "시장 전체" 순매수 합계는 근사치. 정밀값은 market_flow
  (KRX 로그인 또는 KIS `FHPTJ04040000`) 확보 후 게이지 입력을 교체
- 개인 투자자 수급은 랭킹 소스에 없음 — 근사로 개인 ≈ −(외인+기관) 표기, 정밀화는 키움/KIS
- 게이지·트리맵 모두 "근사/일별 스냅샷"임을 UI에 명시

## 5. 아키텍처

기존 구조(FastAPI + React/recharts)를 그대로 확장한다. KRX 클라이언트는 시세용으로 유지.

### 5.1 디렉터리 구조

```
docker-compose.yml        timescale/timescaledb:latest-pg16 (포트 5433, 볼륨 pgdata)
backend/
  app/
    main.py               FastAPI 앱 생성, 라우터 등록, lifespan에서 스케줄러 기동
    config.py             pydantic-settings — .env 로드 (DATABASE_URL, 각종 API 키)
    db.py                 SQLAlchemy 2.0 async engine + session, Base
    models.py             ORM 모델 (§5.2 스키마)
    routers/
      markets.py          /api/markets/*
      stocks.py           /api/stocks/*
      etf.py              /api/etf/*
      macro.py            /api/macro/*
      admin.py            /api/admin/* (배치 수동 트리거, 수집 상태)
    clients/
      kiwoom.py           키움 REST (토큰 24h 캐시, token-bucket rate limiter, TR 호출 공통 래퍼)
      kis.py              한국투자증권 REST (토큰 캐시, 시장/선물 수급 TR)
      ecos.py             한국은행 ECOS (환율)
      commodities.py      yfinance 호출 + 실패 시 FRED CSV 자동 폴백 (유가)
      kofia.py            KOFIA freesis POST 파싱 — 예탁금·신용융자·대차잔고 (§3.5)
      krx.py              기존 KRX 시세 (유지)
    collectors/
      scheduler.py        APScheduler AsyncIOScheduler — 평일 18:00 KST 일별 배치
      base.py             공통: 수집 → upsert → collect_log 기록, 재시도(3회, 지수 백오프)
      market_flow.py      시장별 투자자 순매수 (KIS/pykrx)
      stock_flow.py       관심종목별 투자자 수급 + 프로그램 매매 (키움)
      ohlcv.py            지수/종목 일봉 (키움 차트 TR)
      macro.py            환율(ECOS) + 유가(commodities) + 시장자금·대차잔고(kofia)
      breadth.py          등락 종목수 일별 확정치 (키움 ka20001, 장마감 후)
    quant/
      indicators.py       누적순매수, OBV, N일 연속 순매수, 이동평균 (pandas)
      whale_score.py      §4 시그널 → -100~+100 스코어 + 근거 목록(JSON)
  scripts/
    backfill.py           과거 N년 시계열 초기 적재 (rate limit 준수 순차 실행)
    smoke_test.py         (기존 유지)
frontend/
  src/
    api.js                fetch 래퍼 (기존 확장)
    App.jsx               탭 네비게이션 (시장 / 종목 / ETF / 매크로)
    pages/
      MarketPage.jsx      코스피·코스닥·선물: 지수 **캔들 + 거래량 바**(lightweight-charts, CandleChart 재사용)
                          + 투자자별 순매수 막대(스택) + 누적 라인
                          + 등락 종목수(장중 실시간 + 일별) + 예탁금·신용융자·대차잔고 보조 차트
      StockPage.jsx       종목 검색 → 캔들 + 수급 오버레이 + WhaleGauge + 시그널 목록
      EtfPage.jsx         ETF 테이블(수익률·NAV·괴리율) → 클릭 시 StockPage 재사용
      MacroPage.jsx       USD/KRW, WTI, 브렌트 라인차트 (기간 선택 공유)
    components/
      CandleChart.jsx     lightweight-charts — 캔들 + 거래량 + 누적순매수 오버레이 라인
      FlowChart.jsx       recharts — 투자자별 순매수 막대/누적 라인 (시장·종목 공용)
      WhaleGauge.jsx      매집/분산 게이지 (-100~+100) + 근거 배지
      PeriodPicker.jsx    1M/3M/6M/1Y/3Y 기간 선택 (전 차트 공용)
```

### 5.2 DB 스키마 (PostgreSQL, 전부 날짜 기준 시계열)

공통 원칙: 금액 단위는 백만 원 `BIGINT`, 날짜는 `DATE`, upsert는 `ON CONFLICT DO UPDATE`.

| 테이블 | PK | 컬럼 |
|---|---|---|
| `stocks` | code | name, market(KOSPI/KOSDAQ), is_etf, updated_at — 종목 마스터 |
| `index_ohlcv` | (market, date) | open, high, low, close, volume, value — market: kospi/kosdaq/k200_futures |
| `stock_ohlcv` | (code, date) | open, high, low, close, volume, value |
| `market_flow` | (market, date, investor) | net_value, net_volume — investor: 개인/외국인/기관계/금융투자/보험/투신/사모/은행/기타금융/연기금/기타법인/기타외국인 (KIS 3분류 + pykrx 13분류 겸용, 소스 컬럼으로 구분) |
| `stock_flow` | (code, date, investor) | net_value, net_volume — 키움 ka10059 (개인/외인/기관 세부) |
| `program_trade` | (code, date) | arb_net, non_arb_net, total_net — 키움 ka90013 |
| `macro_series` | (series, date) | value — series: usdkrw/wti/brent/investor_deposit/credit_loan_*/lending_balance, source 컬럼(ecos/yfinance/fred/kofia) |
| `market_breadth` | (market, date) | adv, dec, flat, limit_up, limit_down — 키움 ka20001 일별 확정치 |
| `whale_score` | (code, date) | score SMALLINT, signals JSONB — 재계산 가능하므로 캐시 성격 |
| `watchlist` | code | added_at — 일별 수집 대상 종목 |
| `collect_log` | (job, target_date) | status(ok/fail), rows, message, ran_at — 배치 모니터링·중복 방지 |

TimescaleDB 전환점: `stock_ohlcv`·`stock_flow`가 수백만 행을 넘거나 분봉을 쌓기 시작하면
`create_hypertable('stock_ohlcv', 'date')` 적용 (스키마 변경 불필요).

**배포**: GitHub Pages 정적 스냅샷(CI에서 일일 수집→JSON→빌드, `.github/workflows/deploy-pages.yml`,
`backend/scripts/export_static.py`) — https://changsik00.github.io/stock/ . 실시간/종목 검색 등
DB 상시 접근이 필요한 동적 기능은 대상 밖이며 추후 실서버가 필요하다.

### 5.3 백엔드 API 계약

| 엔드포인트 | 응답 요지 |
|---|---|
| `GET /api/markets/{market}/series?days=90` | 지수 OHLCV + 투자자별 일별 순매수 (프런트 한 화면 = 요청 1개) |
| `GET /api/stocks/search?q=삼성` | code/name/market/is_etf 목록 (DB `stocks` LIKE 검색) |
| `GET /api/stocks/{code}/series?days=180` | OHLCV + 투자자별 수급 + 프로그램 + 누적순매수 (한 번에) |
| `GET /api/stocks/{code}/whale` | 최신 score, signals[], 산출일 |
| `GET /api/etf/list` | ETF 목록 + 수익률/NAV (키움 ka40004, 15분 메모리 캐시) |
| `GET /api/macro/series?ids=usdkrw,wti,brent&days=365` | 매크로 라인차트용 시계열 묶음 (kofia 시리즈도 같은 엔드포인트로) |
| `GET /api/markets/{market}/breadth?days=90` | 일별 상승/하락/보합 종목수 시계열 |
| `GET /api/markets/{market}/breadth/live` | 장중 실시간 등락 현황 — 키움 ka20001 프록시, 60초 메모리 캐시 |
| `GET /api/watchlist` / `POST·DELETE /api/watchlist/{code}` | 수집 대상 관리 |
| `POST /api/admin/collect/{job}?date=` | 배치 수동 실행 (job: market_flow/stock_flow/ohlcv/macro) |
| `GET /api/admin/status` | collect_log 최근 상태 (수집 실패 감지용) |

에러 규약: 외부 API 실패는 502 + `{source, detail}` (기존 KRX 방식 유지), 데이터 없음은 빈 배열.

### 5.4 외부 클라이언트 공통 규칙

- **rate limiter**: 클라이언트별 token-bucket. 키움 기본 1 req/s (실측 후 상향), KIS 15 req/s(공식 20의 여유분), ECOS/FRED 제한 없음에 준함
- **토큰 관리**: 키움/KIS 접근토큰을 DB 또는 파일에 캐시, 만료 30분 전 자동 재발급. 발급 API 자체가 rate limit 대상(KIS 1건/초)이므로 절대 매 요청 발급 금지
- **재시도**: HTTP 429/5xx → 지수 백오프 3회. 최종 실패는 collect_log에 fail 기록하고 다음 잡 진행 (배치 전체 중단 금지)
- **환경변수**(.env): `DATABASE_URL`, `KIWOOM_APP_KEY/SECRET`, `KIS_APP_KEY/SECRET`, `ECOS_API_KEY`, 기존 `KRX_OPENAPI_KEY`

설계 원칙:

- **DB 캐싱 우선**: 모든 외부 API는 배치로 하루 1회(장마감 후 18시경) 수집해 PostgreSQL에
  적재. 프런트는 항상 DB만 조회 → rate limit·외부 장애로부터 격리, 과거 데이터 축적
- **PostgreSQL + 시계열 확장 대비**: 수급/시세 테이블은 `(종목코드, 날짜)` 복합키의
  시계열 구조로 설계. 데이터가 커지거나 분봉·실시간 틱을 쌓기 시작하면 **TimescaleDB
  확장**(하이퍼테이블·자동 파티셔닝·연속 집계)을 그대로 얹을 수 있음 — 스키마 변경 없이
  `create_hypertable()`만 적용하면 되도록 처음부터 날짜 컬럼 기준으로 테이블을 나눔.
  개발 환경은 Docker Compose로 `timescale/timescaledb` 이미지 사용 권장
- **온디맨드 보강**: 종목 검색처럼 미리 수집 못 하는 것만 실시간 API 호출 + 캐시
- **토큰/키 관리**: `.env`에 키움·KIS 앱키 저장(기존 `KRX_OPENAPI_KEY` 방식과 동일).
  토큰 24시간 자동 갱신 로직
- **차트**: 캔들·거래량·오버레이는 `lightweight-charts`(무료, TradingView제)가 recharts보다
  적합. 수급 막대/매크로 라인차트는 기존 recharts 유지
- **색상 규칙 (한국 증시 관행, 전 차트 공통)**: 전일 대비 **상승=빨간색, 하락=파란색**.
  캔들 양봉/음봉, 거래량 바, 등락 종목수, 순매수(+/-) 막대 등 등락을 표현하는 모든 요소에 적용

---

## 6. 단계별 로드맵 (작업 단위 = Sonnet 에이전트에 위임 가능한 자기완결 스펙)

> **진행 현황 (2026-07-17)**: Phase 1 다섯 작업 전부 코드 구현 완료(✅). 단 실제 백필
> 실행은 키 대기 — `.env`에 `ECOS_API_KEY`(환율), `KRX_ID/PW`(pykrx 시장 수급)가 아직
> 비어 있음. Phase 2-1(키움 클라이언트)도 구현·단위테스트 통과, **키움 앱키는 `.env`에
> 있으나 1.5-1 probe 결과 실전/모의 양쪽 호스트 모두 토큰 발급 단계에서
> `return_code=3`(8001: App Key/Secret Key 검증 실패)로 거부됨 — 재발급 또는 IP
> 등록 상태 확인 필요(`backend/app/clients/kiwoom.py` docstring 참고)**.
> 다음 착수 순서: **키움 앱키 재확인/재발급 → Phase 1.5-1 재실행 → 1.5(나머지) →
> 키 준비되는 대로 1-2/1-4 백필 → Phase 2-2**.

### Phase 0 — 사용자 준비물 (코딩과 무관, 병행 진행)
- [x] 키움 계좌 + openapi.kiwoom.com 서비스 신청 → 앱키 발급 완료 (`.env` 반영됨)
- [x] ~~ECOS 인증키 신청~~ — **불필요, 대체 소스 확정(2026-07-19)**: 환율은 무키 네이버
  marketIndex API 1차 + FRED DEXKOUS 폴백으로 전환(clients/naver_fx.py, §3). ECOS는
  키 확보 시 쓸 수 있는 정밀 소스 옵션으로만 유지(clients/ecos.py 보존)
- [x] ~~data.krx.co.kr 무료 회원가입 (KRX_ID/PW)~~ — **불필요, 대체 소스 확정(2026-07-19)**:
  시장 수급은 키움 `ka10051`(업종별투자자순매수, 종합 행 001_AL/101_AL, 13분류,
  과거 일자 조회 가능)로 전환 — KRX 로그인 의존 제거(§1 2026-07-19 갱신 참고).
  pykrx 경로(clients/pykrx_client.py)는 백업 소스로 보존
- [ ] (선물 수급용) KIS 계좌 + 앱키 — Phase 4 전까지만 결정하면 됨

### Phase 1 — 기반 골격 + 매크로 (API 키 없이도 개발·검증 가능한 것부터)

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 1-1 ✅ | 인프라 골격 | docker-compose(timescaledb), config.py, db.py, models.py(§5.2 전체), Alembic 마이그레이션, 라우터 뼈대 | `docker compose up` 후 `alembic upgrade head` 성공, `GET /api/admin/status` 200 |
| 1-2 ✅ | 매크로 수집 | naver_fx.py(네이버 1차→FRED DEXKOUS 폴백, 2026-07-19 ECOS 의존 제거) + commodities.py(yfinance→FRED 폴백) + collectors/macro.py + backfill(3년) | 환율 3년치(731건, source=naver) + WTI/브렌트 3년치 DB 적재 완료(2026-07-19) |
| 1-3 ✅ | 매크로 화면 | `GET /api/macro/series` + MacroPage + PeriodPicker | 브라우저에서 환율/유가 3개 라인차트 렌더 |
| 1-4 ✅ | 시장 수급 수집 | 키움 ka10051 기반 market_flow collector (source='kiwoom', 2026-07-19 pykrx→키움 교체 — KRX 로그인 불필요, pykrx 코드는 백업 보존) + 3년 backfill | 코스피/코스닥 13분류 일별 순매수 DB 적재 완료(2026-07-19) |
| 1-5 ✅ | 시장 화면 개편 | `GET /api/markets/{market}/series` + MarketPage (지수 라인 + 수급 스택 막대 + 누적 라인) | 기존 KRX 시세와 수급이 한 화면에 |

*(2026-07-19: 1-2/1-4의 "키 대기" 각주 해소 — 환율은 네이버, 시장 수급은 키움 ka10051로
대체 소스 확정 및 3년 백필 완료. ECOS/KRX 키는 더 이상 선행 조건이 아님, Phase 0 참고)*

### Phase 1.5 — 시장 체력 지표 (2026-07-17 추가) ★ 키움 앱키 재발급 대기로 블로킹

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 1.5-1 ✅ | 키움 probe 실측 | `scripts/kiwoom_probe.py` 실행 — TR URL 확정, rate limit 실측, **ka20001 응답에 등락 종목수 필드 존재 확정** (§3.5). **2026-07-19 실전 키로 완료**: ka10001/ka10059(`/api/dostk/stkinfo`), ka20001(`/api/dostk/sect`) 전부 200+return_code=0 실호출 확정. ka20001 필드(`rising/stdns/fall/upl/lst`)가 네이버 breadth와 완전 일치 확인. rate limit은 순간 버스트 한도 ~4(5번째부터 429), 클라이언트 기본값(1 req/s, burst 2)이 이보다 보수적이라 안전 — 변경 불필요 | 실측 결과를 kiwoom.py 주석/TR_RESOURCE_URL에 반영, 문서화 — **완료** |
| 1.5-2 ✅ | KOFIA 수집 | clients/kofia.py + macro 배치 편입 + 3년 backfill — 예탁금·신용융자·**대차잔고** | macro_series에 kofia 시리즈 3년치 적재, collect_log ok |
| 1.5-3 ✅* | breadth 수집·API | market_breadth 테이블(마이그레이션) + collectors/breadth.py(일별) + `/breadth`·`/breadth/live` | 일별 등락 종목수 적재 + 장중 live 호출 동작 — *네이버 임시 소스로 선구현(3.6-2), 키움 ka20001은 정밀화용(1.5-1 앱키 재발급 후 교체 예정)* |
| 1.5-4 ✅* | 화면 반영 | MarketPage에 등락 종목수(장중 배지 + 일별 시계열), 예탁금·신용융자·대차잔고 라인차트 | 시장 탭에서 "코스피 ○○○/△△△ 상승/하락" + 자금 차트 확인 — *자금·대차 차트 3종(MarketFundChart) 완료(2026-07-17), 등락 종목수 배지는 1.5-3 완료 후* |

의존성: 1.5-1이 1.5-3의 선행 (ka20001 필드 확정). 1.5-2는 독립 — 병렬 위임 가능.

### Phase 2 — 키움 연동 + 종목 분석 (키움 앱키 발급 후)

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 2-1 ✅* | 키움 클라이언트 | kiwoom.py: OAuth 토큰 캐시, rate limiter, TR 래퍼. **rate limit 실측 스크립트 포함** | 모의 키로 ka10001(종목정보) 호출 성공, 실측치 문서화 — *코드·단위테스트 완료, 실호출 검증은 1.5-1에서* |
| 2-2 | 종목 마스터/검색 | stocks 테이블 적재 + `GET /api/stocks/search` | "삼성" 검색 → 목록 반환 |
| 2-3 | 종목 수급 수집 | watchlist 종목의 ka10059(수급)·ka90013(프로그램)·차트 TR 일별 수집 + 1년 backfill | 워치리스트 종목 DB 적재 |
| 2-4 | 종목 화면 | StockPage: CandleChart(lightweight-charts, 캔들+거래량+누적순매수 오버레이) + FlowChart | 종목 검색→차트까지 동작 |

### Phase 3 — 주포 스코어 + ETF

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 3-1 | 지표 라이브러리 | indicators.py: N일 연속 순매수, 누적순매수, OBV, 거래량 z-score, 가격-수급 다이버전스 | pytest 단위 테스트 통과 |
| 3-2 | whale_score | §4의 6개 시그널 가중합 → -100~+100 + 근거 JSON. 일별 배치에 편입 | 워치리스트 전 종목 일별 스코어 산출 |
| 3-3 | 스코어 UI | WhaleGauge + 근거 배지 + 스코어 시계열 미니차트 | StockPage에 표시 |
| 3-4 | ETF | ka40004 목록 + EtfPage (수익률/NAV/괴리율 테이블 → 상세는 StockPage 재사용) | ETF 목록→차트 동작 |

### Phase 3.5 — 수급 경로 분석: ETF look-through (§4.5, 2026-07-18 추가)

무키 소스(네이버)로 시작 가능해 **Phase 2(키움 키)와 독립적으로 착수 가능**. 3.5-1/3.5-2는 병렬.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 3.5-1 | ETF 마스터·구성 수집 | 네이버 etfItemList(목록·AUM·NAV) + etfAnalysis(top10 비중·순유입) → stocks(is_etf)/etf_holdings/etf_stats 적재 (tab 1/2/3/7 거래대금 상위 300개 — §4.5 유니버스 개정 기준, 마이그레이션 포함). etfAnalysis의 net_inflow 스키마 상세 분석 포함 | 대상 ETF 구성·순유입 일별 적재, collect_log ok |
| 3.5-2 | 순매수 상위 수집·UI | 네이버 sise_deal_rank 파싱 → flow_rank 적재 + 시장 탭 "수급 상위" 테이블 (개별/ETF 배지, 외인/기관 탭) | 일별 상위 종목 테이블 렌더 |
| 3.5-2b | 매도·손바뀜 확장 | flow_rank에 side(buy/sell)·quantity 컬럼 추가(마이그레이션), 네이버 type=sell로 순매도 상위 수집, 랭킹 종목의 회전율(당일 거래대금÷시가총액 %) 부가 표시 — **정렬·판단은 금액이 기본, 거래량은 손바뀜 해석용 부가 지표** (순매수↑+손바뀜↓=조용한 매집 → §4 주포 시그널 재료) | 수급 상위 테이블에 매수/매도 토글 + 수량·회전율 컬럼 |
| 3.5-3 | look-through 계산·UI | flow_path 배치(직접 vs ETF 경유 분해, §4.5 방법론 1·2차 병기) + 종목 상세에 스택 차트·기여 ETF 목록 (StockPage 없는 동안은 수급 상위 테이블에서 클릭 시 모달/섹션으로) | 워치리스트+상위 종목의 경로 분해 값 산출·표시 |
| 3.5-4 | 정밀화 | 전체 구성종목(KIS `ETF 구성종목시세` TR 또는 Seibro) + 키움 순위 TR 교체 + Phase 2 연동(ka10059 직접 수급과 결합해 투자자별 경로 분해). **'파생형 ETF 자금' 지표 추가** — 주식 미보유(인버스/선물형) ETF의 순유입 합계를 별도 표시(look-through 기여 0인 자금 흐름의 가시화, §4.5 한계 절) | top10 대비 커버리지 개선 수치 보고 |

### Phase 3.6 — 시황·자금 집중 대시보드 (§4.6, 2026-07-18 추가) — 전부 무키, 즉시 착수 가능

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 3.6-1 ✅ | 거래대금 상위 | value_rank 테이블 + flow_rank.market 컬럼(마이그레이션 1개로), 네이버 거래대금 상위 수집(소스 실확정 포함), 표 UI(시장·ETF 배지, 등락률 색) | 코스피/코스닥 거래대금 상위 + 등락률이 한 표에 — *ValueRankTable + MarketPage 연동·실데이터 curl 확인 완료(2026-07-18)* |
| 3.6-2 ✅ | breadth(네이버 임시) | 상승/보합/하락/상한/하한 수집(소스 실확정) → market_breadth 적재 + 장중 프록시 + 시장 탭 상단 배지·비율 막대 | "코스피 N↑ M↓" 실데이터 표시 — *BreadthBadge + MarketPage 연동·실데이터 확인 완료(2026-07-18)* |
| 3.6-3 ✅ | 업종·테마 트리맵 | group_snapshot 테이블 + sise_group 수집(업종 79·테마 266) + recharts Treemap(크기=거래대금, 색=등락률 연속 빨↔파) | 업종/테마 토글 트리맵 렌더 — *GroupTreemap + MarketPage 연동, 업종 79·테마 266개 실데이터 확인 완료(2026-07-18)* |
| 3.6-4 ✅ | 수급 방향 종합 | ETF 경유 유출(음수) 상위 병기 + ETF 방향 배지(설정/환매) + ETF별 외인·기관 buy/sell 비교 + 시장 매수세/매도세 게이지(-100~+100, 근사 명시) | 시장 탭에서 매수세/매도세 한눈에 — *flow-path?direction=out/in(하위호환) + FlowPathTable 유입/유출 토글 + ETF 설정/환매 dot 배지 + app/sentiment.py 순수 계산부(단위테스트) + GET /api/markets/sentiment + SentimentGauge 완료(2026-07-18). (b) ETF별 buy/sell 비교는 기존 FlowRankTable(투자자·side 토글 + is_etf 배지, 3.5-2b)로 이미 충족* |

의존성: 3.6-1·3.6-2·3.6-3 상호 독립(병렬 위임 가능). 3.6-4는 3.6-2 이후.
마이그레이션(신규 테이블 2 + flow_rank.market)은 병렬 충돌 방지 위해 착수 전 메인 세션이 일괄 수행.

### Phase 3.7 — 대시보드 재구성 + 검색 + 장중 잠정치 (2026-07-19 추가, 사용자 확정)

원칙: **첫 화면은 숫자 요약 한 판** (장황한 정보·차트·긴 리스트는 뒤로). 새 '대시보드' 탭을
기본 화면으로 신설(기존 시장 탭은 상세 뷰로 보존), 트리맵은 첫 화면 유지(컴팩트).
실시간(장중)은 일단 로컬 전용 — github.io는 일별 스냅샷 유지, VPS는 추후 결정.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 3.7-1 | 대시보드 탭 | DashboardPage: KPI 타일(지수 3종·게이지·등락·예탁금·대차잔고·ETF순유입, 전일비 화살표) + 컴팩트 트리맵 + TOP5 요약 3종(수급/거래대금/ETF경유). 타일·행 클릭 → 차트/전체 리스트(100+) 모달. 기본 탭 전환 | 첫 화면 무스크롤 요약 + 모달 동작 |
| 3.7-2 | 종목 검색·미니 상세 | `GET /api/stocks/search`(마스터 4,299 LIKE) + 자동완성 검색바 + 종목 모달: 캔들(네이버 fchart 종목 일봉) + 투자자별 수급(키움 ka10059 온디맨드+캐시) | 검색→모달에서 캔들+수급 |
| 3.7-3 ✅* | 장중 잠정 수급 | `GET /api/markets/flow/live` 60초 캐시 프록시 → 대시보드 수급 KPI에 '장중 잠정' 배지, 마감 후 확정치 전환 | 장중 실시간 수급 숫자 (로컬) — *2026-07-18 완료. **ka10063→ka10051 소스 전환**: 실호출 검증 결과 ka10063(장중투자자별매매)/ka10066(장마감후투자자별매매)은 시장 합계 1행이 아니라 종목별 배열(코스피 최대 1,330종목)이라 시장 전체 순매수를 얻으려면 투자자 카테고리마다 전 종목 페이지네이션 합산이 필요(비용 큼, 상세 근거는 `backend/app/clients/kiwoom.py` 모듈 docstring "ka10063/ka10066 장중 잠정 수급 probe" 절). 대신 이미 검증된 `ka10051`(§6 1-4 배치 소스)을 `base_dt=오늘`로 재사용 — 시장당 1콜로 끝나고 ka10066 풀페이지네이션 합산과 오차 0.1% 이내로 일치함을 교차검증했다. 단, 주말 조사라 ka10051이 실제 "장중"에 분 단위로 갱신되는지(vs 장마감 후에만 최신값 반영)는 직접 확인 못 함 — **평일 장중 재검증은 사용자 몫**. ka10063/ka10066 편의 메서드(`intraday_investor_trading`/`after_hours_investor_trading`)는 종목별 스크리닝용으로 kiwoom.py에 추가해 둠. `GET /api/markets/flow/live`는 라이브 실패 시 market_flow DB 최신 확정치로 폴백(`provisional: false`), `market_closed`는 KST 15:30 이후 여부로 단순 판정. DashboardPage는 60초 setInterval 폴링(정적 배포는 시도 안 함), 장중엔 값+"장중 잠정" 주황 배지, 그 외엔 기존 확정치+"확정·날짜" 라벨로 자동 전환 |

### Phase 4.5 — 외인 양손 보기: 현선물 포지션·베이시스·파생ETF (2026-07-19 사용자 확정)

배경: 유튜브 왝더독 영상 검토 결과 "서사(의도된 함정)는 과장, 지표는 표준" — 함정
탐지기가 아닌 **중립적 상태 계기판**으로 구현. 시그널 명칭도 "현선 괴리 주의" 등
중립 표현. 데이터 축적 후 이벤트 스터디로 신호 유효성을 자체 검증(주장 신봉 금지).

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 4.5-1 | 파생ETF 방향성 게이지 | 레버리지/인버스 ETF 순유입·거래대금을 배수 부호로 합산 → "개인 방향성 베팅" 지표 + LP 헤지 수요 추정(AUM변화×배수). 기존 etf_stats 데이터 활용 — 즉시 가능 | /api/etf/derivative-flow + 카드 컴포넌트 |
| 4.5-2 | 외인 선물 수급 | K200 선물 투자자별 순매수 — 소스: ①네이버 선물 투자자 동향 실확정 → ②KRX 파생 통계 파싱 → ③KIS. market_flow(market='k200_futures') 적재 + 가능한 만큼 백필 | 외인 선물 순매수 일별 시계열 |
| 4.5-3 | KOSPI200·베이시스 | KPI200 지수 수집(index_ohlcv market='kospi200', 네이버 검증됨) → 베이시스 = 선물종가−KPI200, 백워데이션 감지 API | 베이시스 시계열 + 상태 플래그 |
| 4.5-4 | 프로그램 매매 | 키움 ka90010(일자별 추이) 실측 → 차익/비차익 순매수 적재(macro_series 재사용) + 백필 | 프로그램 차익 순매수 시계열 |
| 4.5-5 ✅ | 외인 양손 카드·시그널 | 대시보드 카드: 외인 현물 vs 선물 방향 나란히 + 개인 파생ETF 베팅 + 베이시스·만기 D-n(둘째 목요일/네마녀 계산). 방향 대치·백워데이션 시 중립 경고 배지 | 카드 렌더 + 시그널 동작 — **완료(2026-07-19)**: main.py에 basis 라우터 등록 + admin.py program_flow import 배선. 백엔드 종합 API는 신설하지 않고(중복 판단) 프런트(DashboardPage.jsx)가 기존 4개 API(markets/{market}/series futures 포함, markets/basis, etf/derivative-flow, macro/series prog_arb_*)를 조합 — 이미 fetch 중이던 marketData.futures/flowInvestorSummary·flowLiveSummary를 재사용해 중복 호출 없음. "투자자별 수급 요약" 바로 아래 "외인 양손·현선물" 섹션(6개 타일: 외인 현물/선물/개인 방향성 파생ETF(EtfDirectionCard 재사용, 모달)/베이시스/프로그램 차익 순매수/다음 만기) + 시그널 배지(현·선 방향 상이=주황, 백워데이션=파랑, 만기 D-3 이내=주황) + 상세 모달(ForeignPositionChart.jsx, recharts 2선 순매수+베이시스 보조축 오버레이, 기간선택). 실데이터로 "현·선 방향 상이" 배지 실제 발동 확인(2026-07-16 기준 외인 현물 -243.1억원 vs 선물 +7,014.0억원 — 선물 수치는 §4.5-5 작업 지시가 인용한 실사례와 정확히 일치, 현물 수치는 인용값(-1.37조)과 달랐으나 부호 대치라는 정성적 패턴은 동일하게 재현됨). export_static.py에 basis.json/derivative-flow.json 덤프 + macro.json에 prog_arb_*/prog_nonarb_* 추가, api.js에 STATIC_DATA 분기 포함 fetchBasis/fetchDerivativeFlow 추가, 정적 빌드로 라이브와 동일 값 재확인. deploy-pages.yml에 backfill_futures_flow(네이버, IP 제약 없음)·backfill_program_flow(키움, IP 제약 warning 코멘트) 스텝 추가. 검증: pytest 229 passed, oxlint 신규 경고 0, npm run build(라이브/정적 둘 다) 통과, Playwright로 라이브+정적 양쪽에서 대시보드 섹션·시그널 배지·상세 모달·파생ETF 모달·다크 모드 스크린샷 확인(콘솔 에러 0)

의존성: 4.5-1~4는 상호 독립(병렬), 4.5-5는 통합 단계. §4 주포 스코어의 재료로 연결.

### Phase 4.6 — 가이드 툴팁 + 일일 자동 진단 (2026-07-19 사용자 요청, 계획 승인 대기)

목표: ① 초보도 각 타일을 읽을 수 있게 화면 안 가이드, ② "숏커버 국면" 같은 해석을
사람이 아니라 **추이 분석 규칙이 매일 자동 생성**, ③ 서버(일별 배치+CI)가 매일 수행.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 4.6-1 | 가이드 툴팁 | 각 KPI 타일·섹션에 ⓘ 툴팁(무엇/읽는 법/색 의미 — frontend/src/guides.js 상수로 중앙 관리) + 헤더 ? 버튼 → "대시보드 읽는 법" 모달(30초 루틴 포함) | 전 타일 툴팁 + 가이드 모달 |
| 4.6-2 | 진단 엔진 | **규칙 기반**(LLM 아님 — 결정적·무비용) `app/quant/diagnosis.py`: 최근 N일 추이로 태그·문장 생성. 규칙 예: 외인 선물 연속 매수/매도·급반전, 현선 방향 상이 지속일수, 베이시스 추세·백워데이션 전환, 대차잔고 급감(숏커버 정합), 개인 파생ETF 쏠림 z-score, 프로그램 차익 누적, 만기 D-3. 산출 = 태그 목록 + 근거 수치 + 템플릿 한글 요약("외인이 하락 베팅을 거두는 흐름 — 선물 2일 연속 매수, 대차잔고 -10.3조"). 신규 테이블 `daily_diagnosis`(date PK, tags/inputs JSONB, summary) — 이력 축적(추후 이벤트 스터디 원료) | 오늘 데이터로 진단 1건 생성·저장, 규칙 단위테스트 |
| 4.6-3 | 진단 카드 | 대시보드 최상단 "오늘의 진단" 카드 — 태그 배지 + 요약 2~3줄 + 근거. 클릭 → 진단 이력 타임라인 모달. "참고용·규칙 기반" 라벨 명시 | 카드·이력 모달 렌더 |
| 4.6-4 | 서버 일일 루틴 | collectors REGISTRY에 diagnosis 잡 편입 — **모든 수집 후 실행 보장**(스케줄러 실행 순서 확인·조정). CI(평일 18:30)에도 편입 → github.io 배포본에도 그날 진단 포함. 로컬 스케줄러(ENABLE_SCHEDULER=1)로도 동일 | 배치 1회 실행 시 수집→진단→정적 덤프까지 자동 |

의존성: 4.6-1 독립(병렬). 4.6-2 → 4.6-3·4.6-4. 알림(텔레그램 등)은 Phase 4 항목으로 유지.

### Phase 4.7 — 3단 갱신 주기 (2026-07-20 사용자 확정)

배경: §4.6-4/4.5-5까지 60초 능동 갱신은 등락종목수·수급잠정·관심순위 3종뿐이었다.
"대시보드 전체가 처리돼야 하지 않냐"는 지적에 "무조건 1분 통일"은 기각 —
소스 자체가 하루 1회 확정인 데이터(ETF 순유입/파생ETF, KOFIA 3종, 유가)는
아무리 자주 조회해도 값이 안 바뀌고, 유가(yfinance)는 과요청 시 재차단 위험까지
있어 오히려 해롭다. 대신 **소스별 실제 갱신 가능 여부를 실측**해 3단으로 나눈다.

| 티어 | 확정 대상 | 조건부 대상(장중 실측 필요) |
|---|---|---|
| 1분 (유지) | 등락종목수·수급잠정(ka10051)·관심순위(ka00198) | 환율(네이버, FX는 상시 변동) — 이번 세션 실측 범위 밖(아래 5개 소스만 지시됨), 별도 실측 필요 |
| 5~10분 (신규) | — | 거래대금 상위·수급 상위(flow_rank)·업종테마 트리맵(group_snapshot)·베이시스/K200선물·외인선물수급 — 네이버 소스가 장중 갱신되는지 확인 후 편입 여부·주기 확정 |
| 1일 고정 (변경 금지) | ETF 순유입·파생ETF 방향성·KOFIA 3종(예탁금/신용융자/대차잔고)·WTI·브렌트(yfinance 재차단 리스크) | — |

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 4.7-1 | 장중 실측 | 조건부 대상 소스를 실제 장중(2026-07-20 월요일)에 5~10분 간격으로 재호출해 값이 바뀌는지 확인 — 바뀌면 티어 편입, 안 바뀌면(일별 확정치임이 실증되면) 1일 고정으로 재분류하고 근거를 PLAN에 기록 | ✅ 완료 — 아래 §4.7-1 실측 결과 표 |
| 4.7-2 | 3단 스케줄러 | collectors/live_refresh.py를 확장(또는 새 인터벌 잡 추가) — 1분 잡은 기존 그대로, 5~10분 잡을 별도 IntervalTrigger로 추가(장중만). market_flow/futures_flow/basis/value_rank/flow_rank/group_snapshot의 "장중 온디맨드" 캐시 워밍 함수 필요(기존 EOD 배치와 별개 — DB에는 안 쓰고 메모리 캐시만, §3.5 원칙과 동일) | ✅ 완료 — `live_refresh_extra` 7분 인터벌 잡, 4개 소스만 편입(아래 §4.7-2 참고) |
| 4.7-3 | 프런트 반영 | 대시보드의 해당 섹션에 폴링 주기 배지(예: "5분 갱신") 표시, setInterval 주기 조정 | ✅ 완료 — "7분 갱신" 배지 4개 섹션, Playwright 스크린샷 확인 |

#### §4.7-1 장중 실측 결과 (2026-07-20 월요일, 09:22~09:47 KST, 코드 변경 없이 소스 직접 재호출)

방법: `clients/naver_value_rank.py`·`clients/naver_rank.py`·`clients/naver_group.py`·`clients/naver_index.py`·
`clients/naver_futures_flow.py`를 백엔드 컨테이너 안에서 직접 호출하는 스크립트(`scripts/_measure_live_refresh.py`,
측정 완료 후 삭제)로 09:22:18, 09:22:48, 09:47:48 KST 3회(약 25분 간격) 재호출 + 구현 중 라이브
엔드포인트(`/api/markets/basis/live` 등)로 09:29~09:39 KST 사이 추가 스팟 체크. 시장 개장(09:00) 직후부터
측정해 장 초반에도 이미 갱신되는지까지 확인했다.

| 소스 | 09:22 KST | 09:47 KST(+25분) | 판정 |
|---|---|---|---|
| **거래대금 상위** (naver_value_rank, quantTop 누적거래대금) | KODEX 200선물인버스2X `accumulatedTradingValueRaw`=231,125,000,000원 | 같은 종목 308,769,000,000원 (+33.6%) | **장중 갱신 확인** — 당일 누적 체결대금이라 시간이 지날수록 단조 증가, 값이 뚜렷하게 바뀜 |
| **수급 상위** (naver_rank, sise_deal_rank_iframe) | "최근 2거래일" = 2026-07-15/07-16 (blocks) | 동일하게 2026-07-15/07-16 — **금요일(07-17)도, 오늘(07-20)도 전혀 반영 안 됨** | **미갱신** — 09:31 KST 스팟 체크(직접 재호출)에서도 동일, `flow_rank` DB 테이블의 실제 최신 적재 날짜도 2026-07-16으로 일치(psql 확인) — 우연한 샘플링이 아니라 소스 자체가 최소 2영업일 이상 지연 발행. 5~10분 재조회는 무의미 |
| **업종·테마** (naver_group, sise_group.naver 목록) | 업종 상위: 가정용품 +1.20%, 전자장비와기기 +1.12%, 에너지장비및서비스 +0.54% | 전자장비와기기 +2.13%(1위로 교체), 석유와가스 +1.95%(신규 진입), 가정용품 +1.18%, 카드 +0.74% | **장중 갱신 확인** — 순위·수치 모두 유의미하게 변함(그룹 상세 거래대금 합산은 그룹당 345회 호출·2~3분이 걸려 라이브 대상에서 제외, 목록의 등락률만 편입) |
| **베이시스/K200선물·KOSPI200현물** (naver_index fchart "오늘" 봉) | 선물종가 1049.85(고 1051.4/저 1033.1), 현물종가 1075.96 | 선물종가 1084.0(고 1090.35), 현물종가 1073.19 | **장중 갱신 확인** — 당일 봉이 체결마다 갱신되는 진짜 장중 캔들(고가·종가 모두 상승) |
| **외인 선물 수급** (naver_futures_flow, m.stock trend bizdate=오늘) | 개인 -3,520억/외국인 -3,960억/기관계 4,410억(억원, 09:22 시점 누적) | 개인 -1조6,110억/외국인 6,300억/기관계 1조7,030억 | **장중 갱신 확인** — 당일 누적 순매수치가 25분 만에 방향까지 바뀔 정도로 크게 갱신됨(개장 초반이라 변동성이 특히 컸다) |

**최종 3단 분류 확정** (계획 대비 달라진 점: "수급 상위·flow_rank"는 5~10분 후보였으나 실측 결과 제외):

| 티어 | 확정 |
|---|---|
| 1분 (기존 유지, 변경 없음) | 등락종목수·수급잠정(ka10051)·관심순위(ka00198) |
| **5~10분(7분 채택)** | 거래대금 상위(value-rank) · 업종/테마 등락률(groups, 거래대금 합산 제외) · 베이시스/K200선물(basis) · 외인 선물수급(futures-flow) — 4종 |
| 1일 고정 (기존 + 신규 편입) | ETF 순유입·파생ETF 방향성·KOFIA 3종·WTI·브렌트(기존) + **수급 상위/flow_rank(신규)** — 소스가 2영업일+ 지연 발행이라 몇 분 간격 재조회가 무의미, 기존 일별 배치(collectors/flow_rank.py)만 유지 |
| 미검증 | 환율(네이버) — 이번 세션 지시 범위 밖, §4.7-1 표의 5개 소스만 실측 대상이었음 |

7분을 채택한 근거: 4개 확정 소스 모두 25분 창 안에서 이미 뚜렷이 바뀌었고(값이 갱신되기까지 최소 몇 분이면 충분해 보임), value-rank/live 1회 호출이 코스피+코스닥 전량 순회로 15~30초가 걸려(§4.7-2 참고) 너무 촘촘한 주기는 서버 부담 대비 이득이 작다 — 5~10분 범위의 중간값인 7분으로 결정.

#### §4.7-2 구현 — 신규 API·주기

`collectors/live_refresh.py`에 `live_refresh_extra`(7분 IntervalTrigger, 장중만)를 기존 `live_refresh`(60초)와
독립적으로 추가. 신규 라이브 엔드포인트(전부 DB에 안 쓰는 메모리 캐시, §3.5 원칙, TTL=420초):

- `GET /api/markets/value-rank/live` (routers/flow_rank.py)
- `GET /api/markets/basis/live` (routers/basis.py)
- `GET /api/groups/live?type=upjong|theme` (routers/groups.py) — 등락률만, 거래대금 합산 없음
- `GET /api/markets/futures-flow/live` (routers/markets.py)

`flow-rank/live`는 실측 근거로 **추가하지 않았다** — EOD `GET /api/markets/flow-rank`만 유지.

**부수 발견/수정(장 마감 게이트 버그)**: 구현 중 기존 60초 라이브 3종(`breadth/live`·`flow/live`·`attention`)이
`market_closed`를 응답 메타데이터로만 쓰고 실제로는 장 마감 여부와 무관하게 항상 외부 API(키움/네이버)를
먼저 호출하던 버그를 발견해 함께 수정했다 — 이제 캐시 미스 시점에 장 마감이면 외부 호출을 아예 하지 않고
DB 확정치(breadth/flow) 또는 마지막 성공 캐시(attention, DB 저장이 없어)로 즉시 응답한다
(`provisional: false`/`market_closed: true`). 신규 5~10분 티어 4종도 처음부터 이 게이트를 내장했다(DB
폴백이 없어 마지막 캐시 재사용 또는 빈 값 + `market_closed: true`). 장 마감 유닛테스트로 외부 클라이언트
호출 없음을 assert하는 테스트를 추가했다(`test_markets_breadth_router.py`·`test_markets_flow_live_router.py`·
`test_markets_attention_router.py`의 신규 케이스 + 신규 4개 라이브 엔드포인트 테스트 파일 각각).

#### §4.7-3 프런트 반영

DashboardPage.jsx에 `EXTRA_LIVE_POLL_MS = 420_000`(7분) 상수 + 4개 폴링 `useEffect`(value-rank/groups/basis/
futures-flow, 전부 `STATIC_DATA`일 때는 폴링하지 않음 — 로컬 전용 기능). "거래대금 상위" TOP5 카드와
"업종·테마 강약" 트리맵은 라이브 응답으로 완전히 대체(트리맵은 박스 크기(value)는 EOD 그대로 두고
색(change_rate)만 이름 기준 병합), "외인 선물(K200)"·"베이시스" KPI 타일은 표시값만 라이브로 오버레이하고
시그널 판정(현·선 방향 상이/백워데이션 배지)은 EOD 데이터 기준을 그대로 유지(판정 로직 안정성 우선).
배지 문구: "7분 갱신 · 장중"(거래대금 상위·외인 선물), "콘탱고/백워데이션 · 7분 갱신"(베이시스), "박스 크기 =
일별 거래대금 · 색(등락률) 7분 갱신 · 장중"(트리맵). Playwright로 렌더 확인.

#### §4.7-4 "수급 상위" 키움 TR 대체 재검토 — 재차 미채택 (2026-07-21 화요일 장중 재확인)

배경: 사용자가 "수급 상위" 카드가 항상 어제/그제 값만 보인다고 반복 지적(§4.7-1의
naver_rank 2영업일+ 지연 문제 재확인) — 다른 라이브 카드(관심순위 등)처럼 키움 TR로
대체 가능한지 다시 조사. GitHub `younghwan91/kiwoom-rest-api`의 `domestic/ranking.py`
(`/api/dostk/rkinfo`)에서 후보 2개(`ka10065` 장중투자자별매매상위요청,
`ka90009` 외국인기관매매상위요청)를 찾아 실전 키로 직접 실호출.

**실측 결과**: `ka10065`는 응답(`opmr_invsr_trde_upper`)에 금액 필드가 없고 정렬
기준도 수량이라 "외국인 순매수 상위"(금액 기준) 카드 의미와 맞지 않아 부적합
판정(실호출 1위가 시가총액 작은 흥아해운). `ka90009`는 응답(`frgnr_orgn_trde_upper`)
필드/설계는 이상적이었다 — 외국인·기관 순매도/순매수 4개 랭킹이 병렬 컬럼으로
와서 기존 flow-rank의 investor x side 2x2 토글에 정확히 대응되고 단위(백만원)도
동일. 하지만 **장중 갱신 실측에서 탈락**: 2026-07-21 09:44~09:52 KST 구간에 90초
간격 5회씩 두 차례(총 10개 관측치, 12분+) 반복 호출했지만 상위 3종목의 값(SK하이닉스
42264/삼성전자 35624/KODEX 200 11619, 백만원)이 **단 한 번도 바뀌지 않았다** — 같은
시간대 대조군으로 `GET /api/markets/attention`(ka00198, 이미 라이브로 검증된 소스)을
75초 간격으로 재호출하면 상위 3종목 등락률이 실제로 변했으므로(장이 멈춰서가
아니라는 확인), ka90009 자체가 장중 실시간 갱신을 하지 않는 소스로 최종 판정.
`date` 파라미터도 오늘/어제/생략 세 경우 응답이 완전히 동일해 무시됨을 확인 —
임의 과거일 조회도 안 되고 실시간 갱신도 안 되는 애매한 소스(EOD 대체도, 라이브
대체도 아님).

**결론**: 두 TR 모두 부적합 — §4.7-1의 "**수급 상위/flow_rank는 1일 고정**" 분류를
그대로 유지한다. `clients/kiwoom.py`에 두 TR을 `TR_RESOURCE_URL`에 등록하고
`foreign_institution_trading_top()`(ka90009) 편의 메서드를 추가했지만 **어떤
라우터도 호출하지 않는다** — ka10063/ka10066과 동일한 관례로 "탐색했으나 부적합"
근거를 코드/docstring에 남겨 향후 같은 조사를 반복하지 않도록 했다
(`routers/flow_rank.py` 모듈 docstring "키움 TR(ka10065/ka90009) 대체 재검토" 절,
`clients/kiwoom.py`의 `foreign_institution_trading_top()` docstring 참고). 프런트
"수급 상위" TOP5 카드는 변경하지 않았다 — 기존 네이버 EOD 소스 + "확정 MM-DD"
라벨이 정확한 상태 그대로다.

## Phase 5 — 일중(분봉) 차트 + 스켈핑 스크리너·진입 시그널 (2026-07-21 사용자 요청)

배경: ① 지금은 일봉만 있어 장중 추이(1/3/5/10/60분봉)를 못 본다 — 데이터가 fresh하지
않아 단타 판단에 못 씀. ② 스켈핑을 하려면 "어떤 종목을(스크리닝) 언제 들어갈지(타이밍)"
정보가 필요한데 지금은 없다.

원칙: 스크리너·시그널은 **관찰 사실만 서술**한다 — "매수/매도해라" 지시 문구 금지,
기존 게이지·시그널의 "참고용/근사치" 톤을 그대로 계승. 데이터는 **오늘 하루치만**
온디맨드+짧은 캐시로 제공(영구 분봉 저장소 아님 — §3.5 DB 캐싱 원칙과 별개로, 분봉은
저장 비용 대비 가치가 낮아 요청 시점 조회로 충분).

### 5.1 분봉 데이터 인프라

실측 확정(2026-07-21, GitHub kiwoom-rest-api 소스 대조): 키움 `/api/dostk/chart` 카테고리에
- `ka10080` 주식분봉차트요청 — 파라미터 `stk_cd`, `tic_scope`(분 단위 — 실제 허용값 1/3/5/10/…/60 실호출로 확정 필요)
- `ka20005` 업종분봉차트요청 — 파라미터 `inds_cd`(001=KOSPI, 101=KOSDAQ 종합 — ka20001과 동일 코드 체계)
- **K200 선물 분봉은 키움 REST에 없음** (§1 "선물 도메인 자체가 없음"과 일치) — 대안: 네이버 등 인트라데이 분봉 소스 실탐색, 없으면 "미지원"으로 명시(허위 데이터로 채우지 않음)

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.1-1 | 종목·지수 분봉 API | ka10080/ka20005 실호출 확정(tic_scope 허용값, 응답 필드, 하루 범위) → `GET /api/stocks/{code}/intraday?interval=` , `GET /api/markets/{market}/intraday?interval=` (온디맨드+짧은 캐시, DB 미저장) | ✅ 완료 — 1/3/5/10/15/30/45/60분봉 전부 실데이터 응답 확인(아래 §5.1 결과) |
| 5.1-2 | 선물 분봉 소스 탐색 | 네이버 등에서 K200 선물 인트라데이 분봉 소스 실측 → 되면 API 추가, 안 되면 PLAN에 한계 기록 | ✅ 완료 — **미지원 확정**(근거 아래 §5.1 결과), `GET /api/markets/futures/intraday`는 501 |
| 5.1-3 | 차트 UI | CandleChart에 "일봉 / 1·3·5·10·60분" 토글 — 시장 탭 + 종목 모달 | ✅ 완료 — Playwright 스크린샷 확인(아래 §5.1 결과) |

#### §5.1 결과 (2026-07-21 08:00~09:xx KST 실측)

**tic_scope 허용값·응답 필드**(005930, inds_cd 001/101 대상, 실전 키로 실호출):

- URL: `ka10080`/`ka20005` 둘 다 `/api/dostk/chart`.
- 파라미터: `ka10080={"stk_cd", "tic_scope", "upd_stkpc_tp": "1"}`, `ka20005={"inds_cd", "tic_scope"}`.
- `tic_scope` **8개 값 전부 실호출 확인**: `1, 3, 5, 10, 15, 30, 45, 60`(분) — 매번 200 + `return_code=0`.
- 응답: `ka10080`→`stk_min_pole_chart_qry`, `ka20005`→`inds_min_pole_qry`, 둘 다 **고정 900행**. 필드
  `cntr_tm`(YYYYMMDDHHMMSS)/`cur_prc`/`open_pric`/`high_pric`/`low_pric`/`trde_qty`/`acc_trde_qty`.
- **가격 필드는 전일대비 부호(`+`/`-`)가 개별 접두**된 문자열(예: 같은 행에 `open_pric="+654294"`,
  `cur_prc="-651627"`가 같이 옴) — 절대값을 취해야 실제 가격. 드물게 `pred_pre`(안 씀)에서 `"--30433"`
  같은 이중 부호도 관측(원인 미상, 방어적으로만 처리).
- **하루 커버리지**: 한 콜(900행)에 여러 거래일이 섞여 옴(1분봉=최근 약 3거래일치, 60분봉=약 128거래일치)
  — 최신 거래일 하루는 항상 그 안에 통째로 포함돼(1분봉도 09:00~15:35 전 구간 확인) `cont-yn`/`next-key`
  연속조회 없이 1콜로 "오늘 하루치"를 뽑을 수 있다. 행 순서는 최신이 먼저라 오름차순으로 뒤집어 반환.
- 상세 필드/파싱 근거는 `backend/app/clients/kiwoom.py` 모듈 docstring "ka10080/ka20005 실측 확정" 절.

**장중 갱신 확인 — 완료(2026-07-21 09:05~09:08 KST)**: 개장 후 `curl
"http://localhost:8123/api/stocks/005930/intraday?interval=1"`을 실제로 3분 간격 재호출 —
09:05:16 응답은 09:05까지 6개 봉, 09:08:46 응답은 09:08까지 9개 봉으로 정확히 3개 봉이
새로 채워짐(시각도 실시간과 일치). **키움 ka10080이 장중에 최신 분봉을 실시간으로 채워준다는 것,
그리고 캐시 TTL(1분봉 60초) 만료 후 재요청이 실제로 새 데이터를 반영한다는 것 둘 다 실증됨.**

**선물 분봉 미지원 확정** — 시도 목록(전부 2026-07-21 실측):
- `m.stock.naver.com/api/chart/domestic/index/FUT?periodType=minute`(및 `minuteN`/`min`/숫자 등 변형) →
  빈 응답. 같은 엔드포인트의 `dayCandle`/`weekCandle`/`monthCandle`은 정상 동작 — 엔드포인트 자체는
  살아있지만 분 단위만 미지원.
- `m.stock.naver.com/api/chart/domestic/index/FUT/minute`(서브 리소스) → `[]`(빈 배열, 404 아님). 대조군으로
  일반 종목(`item/005930/minute`)도 동일하게 `[]` — 이 리소스가 공개 웹에 비활성화된 것으로 판단(선물만의
  문제가 아님).
- `api.stock.naver.com`(m.stock과 같은 백엔드로 추정) 동일 경로도 동일하게 `[]`.
- 레거시 `fchart.stock.naver.com/siseJson.naver?symbol=FUT&timeframe=minute` → 헤더 행만 오고 데이터 행 0개
  (`timeframe=day`는 정상 동작).
- `polling.finance.naver.com/api/realtime/...` → 실시간 스냅샷 1건만 주는 시세 API라 시계열 차트에 못 씀.
- 키움 REST에는 애초에 선물 도메인 자체가 없음(§1). → **결론: 억지로 채우지 않고 `GET
  /api/markets/futures/intraday`는 501 + 위 근거를 응답 `detail`에 남김.**

### 5.2 스켈핑 스크리너 — 종목 선정

기존 수집 데이터 재사용: 회전율(flow_rank/value_rank), 거래대금 급증(오늘 vs 최근 평균),
실시간 관심순위(attention), 등락률. → 모멘텀 스코어 상위 N을 대시보드 "스켈핑 후보" 카드로.

### 5.3 진입 타이밍 시그널

분봉 기반 계산(전부 관찰 서술, 지시 아님): VWAP 이격도, 당일 신고가/신저가 돌파,
이동평균(5/20분) 크로스, 거래량 스파이크(z-score), N분 모멘텀. 종목 모달의 분봉
차트에 오버레이 + 시그널 배지 목록("거래량 급증 3.2배" 등) + "참고용, 매매 신호 아님" 명시.

### 5.4 통합

종목 상세 모달 = 분봉 차트 + 시그널 배지 완성. 대시보드에 "스켈핑 후보" 카드 신설,
클릭 시 종목 모달(분봉+시그널)로 연결. 의존성: 5.1 → 5.2/5.3(병렬 가능) → 5.4.

### Phase 5.4 — 시세 일관성 + 수급 1D 장중 누적 (2026-07-21 사용자 지적)

**배경 ①**: "실시간 관심 TOP5"와 "스켈핑 후보"에 같은 종목(예: 삼천당제약)이 뜨는데
등락률이 다르게 보임 — 캐시 타이밍 문제가 아니라 **서로 다른 소스**였다(scalp.py의
change_rate는 value-rank/live=네이버 7분 캐시, attention은 키움 60초 캐시). "데이터
하우스처럼 관리해야 하지 않냐"는 지적이 정확 — 완전한 통합 시세 저장소는 지금
규모에선 과함, **attention 캐시를 우선 참조하는 절충안**으로 두 카드의 숫자를 맞춘다.

**배경 ②**: 투자자별 수급 요약·외인 양손 상세 모달이 EOD(일별) 히스토리 차트만
보여줌(사용자가 "3M"이라 표현) — 키움 ka10051에 분단위 이력 자체가 없어 소스에서
직접 당겨올 수 없다. **이미 60초/7분마다 돌고 있는 라이브 폴링 결과를 그날그날
메모리에 스냅샷으로 적립**해 "오늘 장중 누적 추이"를 자체 생성한다(신규 외부 API
호출 없음, 자정 리셋). 일봉/분봉 토글과 같은 패턴으로 "3M(일별 히스토리)" 옆에
"1D(오늘 장중 누적)" 탭을 추가한다.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.4-1 | 시세 일관성 | scalp.py의 change_rate를 attention 캐시 우선 참조로 변경(있으면 attention, 없으면 value-rank 폴백) — 두 카드에 겹치는 종목의 등락률 일치 | 삼천당제약류 겹침 종목 수치 일치 |
| 5.4-2 | 장중 누적 적립 인프라 | `collectors/live_refresh.py`의 60초/7분 잡에 훅을 걸어 개인·외국인·기관계 순매수(flow/live), 외인 현물/선물(flow/live·futures-flow/live) 스냅샷을 메모리 버퍼에 적립(자정 리셋, 시리즈별 각자의 실제 갱신주기로 틱 — 선물은 7분 그대로, 억지로 60초로 안 늘림) | 장중 몇 시간 누적 시계열 확인 |
| 5.4-3 | 1D 조회 API | `GET /api/markets/flow/intraday-accumulated`, `GET /api/markets/foreign-position/intraday-accumulated` — 오늘 적립분 반환 | 실데이터 응답 |
| 5.4-4 | 상세 모달 1D 탭 | 투자자별 수급 요약·외인 양손 상세 모달에 "3M/1D" 토글, 1D는 위 API의 누적 라인 차트 | 토글 렌더·데이터 확인 |

### Phase 5.5 — 차트 기본값 통일 + 7분 티어 쪼개기 + 매크로/게이지 재검토 (2026-07-21)

사용자 지적 4건 + "왜 7분마다냐, 1분 안 되냐" 질문에 대한 실측·진단 결과.

**진단 ①(지수 차트 3M/1D 불일치)**: 대시보드 지수 타일 클릭 시 뜨는 `CandleModal`은
분봉 토글 자체가 없어 90일 EOD만 보여준다(5.4에서 수급 모달들엔 1D를 기본값으로
했지만 이 모달은 애초에 그 옵션이 없었음). 시장 탭(MarketPage.jsx)의 `intradayMode`도
기본값이 `'daily'`라 5.1에서 분봉 기능을 만들어놓고 기본은 여전히 일봉이었다.

**진단 ②(7분 티어 갱신 안 되는 것처럼 보임 + "왜 7분이냐" 질문)**: 백엔드
`_run_live_refresh_extra`는 도커 로그로 7분 간격 정상 발동 확인됨(12:55→13:02→13:09
KST). 실비용 실측: **거래대금 상위(value-rank)만 진짜 비쌈**(코스피+코스닥 전체
~4,300종목 페이지네이션, 사이클당 ~44요청·13초+ — 유가 429 차단 전례와 같은
리스크 카테고리). **업종·테마(groups)는 목록 페이지 1회, 베이시스·외인선물도
단일 조회**라 원래 가벼운데 "단순함을 위해" 같은 티어에 묶여 있었을 뿐.
프런트 폴링 코드 자체는 리뷰 결과 구조적 버그 미발견(구현 단계에서 실제 장시간
관찰로 재확인 필요).

**진단 ③(환율·유가 1D 우선)**: 유가(WTI, yfinance)는 §7에 이미 기록된 실제
사고(2024~2025 과요청 차단)로 하루 1회가 의도된 설계 — 되돌리지 않는다. 환율은
아직 실시간 소스 가능성 미확인(clients/naver_fx.py가 일별 조회만 구현돼 있음,
네이버가 장중 시세를 주는지 실측 필요).

**진단 ④(시황·자금이 오늘 날짜 아님)**: "매수세 게이지"(market_sentiment)가
MarketBreadth/FlowRank **DB(EOD)만** 조회 — breadth 자체는 라이브(/breadth/live)가
있는데 게이지 계산엔 반영 안 됨. 예탁금/신용융자/대차잔고/ETF순유입은 소스
자체가 KOFIA T+1 공시라 구조적으로 라이브 불가(이미 §3.5/§4.5-1에 문서화됨,
바꿀 수 없음 — 그대로 날짜 라벨 유지).

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.5-1 | 지수 차트 1D 통일 | CandleModal에 분봉 토글 추가 + 기본값 1D, MarketPage `intradayMode` 기본값을 daily→1(분) 변경 | 지수 차트 전부 1D 기본 |
| 5.5-2 | 7분 티어 쪼개기 | value-rank만 7분 유지(비용 근거 유지), groups·basis·futures-flow를 1분 티어로 이동. 장시간(10분+) 실관찰로 프런트 렌더 실제 갱신 검증 | 3개 소스 1분 갱신 실증, 나머지는 여전히 정상 |
| 5.5-3 ✅ | 환율 라이브 소스 실측 | 네이버 환율에 장중 실시간 소스 있는지 실측 → 있으면 전환(§4.7 패턴), 없으면 현행 유지 + 이유 문서화. 유가는 손대지 않음(§7 리스크 유지) | 판정 결과 문서화 — **완료(2026-07-21)**: 아래 §5.5-3 실측 결과 참고, "된다" 판정으로 전환 |
| 5.5-4 ✅ | 게이지·시황 정합성 | market_sentiment의 breadth 요소를 라이브(breadth/live) 반영 검토, 구조적 EOD 항목은 명확한 라벨 유지(이미 있는 StaleDate 패턴 재사용) | 게이지가 오늘 등락 반영 — **완료(2026-07-21)**: 아래 §5.5-4 결과 참고 |

#### §5.5-3 환율 라이브 소스 실측 결과 (2026-07-21 화요일 장중, 13:27~13:32 KST)

기존에 이미 쓰던 `clients/naver_fx.py`의 `m.stock.naver.com/front-api/marketIndex/
prices` 응답을 60~90초 간격으로 3회 재호출(13:27:26/13:28:41/13:29:43)했을 때는
"오늘" 행이 세 번 다 동일(1,474.50)해 처음엔 "일별 배치 값"처럼 보였다. 그런데
`finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW`
페이지를 대조 확인하는 과정에서 "고시회차 281회"·"고시환율은 하루에도 여러번
재고시 될 수 있습니다"라는 문구를 발견했고, 그 iframe
(`exchangeDegreeCountQuote.naver?marketindexCd=FX_USDKRW`, "고시회차별 시세")을
25초 간격으로 재호출하자 281회→282회→283회로 고시회차 자체가 몇 분 안에 계속
올라가며 매매기준율도 1,474.50→1,474.10→1,474.30으로 실제 바뀌었다. 이 회차별
값과 `front-api/marketIndex/prices`의 "오늘" 행을 같은 시각에 나란히 비교하니
**완전히 일치**했다(13:30:36에 두 소스 모두 1,474.10, 13:31:26에 두 소스 모두
1,474.30) — 즉 처음 3회 실측이 우연히 같은 고시회차 구간(281회) 안에서 이뤄져
"안 바뀐다"는 오판을 할 뻔했을 뿐, **이미 쓰고 있던 소스의 "오늘" 행 자체가
장중 고시회차 갱신(대략 1~2분 간격)을 그대로 반영하는 준실시간 값이었다**.

**판정: 된다.** 새 소스 탐색이 필요 없었다 — `clients/naver_fx.py`의
`fetch_usdkrw_naver(start, end)`를 `[오늘, 오늘]` 구간으로 좁혀 그대로
재사용하면 라이브 조회가 된다. 유가(WTI/yfinance)는 지시대로 건드리지 않았다.

구현: `routers/markets.py`에 `_warm_fx_live()` + `GET /api/markets/fx/live`
신설(다른 1분 티어 warm 함수와 동일한 60초 캐시 + 장마감 게이트 패턴, 장마감
시 macro_series DB의 usdkrw 최신 확정치로 폴백, macro_series 테이블에는 쓰지
않음 — §3.5 원칙). `collectors/live_refresh.py`의 60초 잡에 `_warm_fx_live`
호출 추가. 프런트(`DashboardPage.jsx`)의 환율 타일이 `fxLive`(1분 폴링, 다른
1분 티어와 동일한 useEffect에 합류)를 우선 표시하고 "1분 갱신 · 장중" 라벨을
붙이며, 없으면 기존 `macroSeries`(EOD) 폴백 그대로. WTI 타일은 변경 없음.

실배포 검증: 배포 후 `GET /api/markets/fx/live`를 반복 호출해 값이
1,474.50→1,475.30(13:41 KST, live-refresh 60초 잡이 자연 갱신)으로 계속
바뀜을 재확인했다.

#### §5.5-4 게이지·시황 정합성 결과 (2026-07-21)

`routers/flow_rank.py`의 `market_sentiment`가 breadth 요소를 계산할 때
`_load_breadth_component_live(session)`을 먼저 시도하도록 변경했다 — 내부에서
`routers.markets._warm_breadth_live(session)`(이미 있는 1분 캐시 함수)를
재사용해 장중이고 라이브 조회가 성공하면 그 adv/dec/flat 합계로 breadth 요소를
계산하고(`source: "live"`), 장 마감이거나 라이브가 실패하면 기존
`_load_breadth_component`(market_breadth DB EOD, `source: "eod"`) 그대로
폴백한다 — **완전 대체가 아니라 우선순위 추가**. flow(flow_rank)·etf(etf_stats)
요소는 지시대로 손대지 않았다(flow는 §4.7-4에서 라이브 부적합 판정된 상태 유지).

실측(2026-07-21 13:37 KST 장중, `GET /api/markets/sentiment`): breadth가
`{"score": 21.5, "date": "2026-07-21", "adv": 1553, "dec": 982, "flat": 115,
"source": "live"}`로 응답(오늘 날짜, live). 같은 시각 DB EOD 경로만 직접 호출한
값과 대조하면 `{"score": -52.9, "adv": 556, "dec": 1944, "flat": 126, "source":
"eod"}`로 완전히 다르다 — EOD가 실제로는 장중에 이미 적재된 "오늘" 날짜의
불완전한 스냅샷이라 라이브와 크게 어긋나는 사례가 실증됐고(정확히 이런
어긋남 때문에 라이브 우선이 필요했다는 진단④가 재확인됨), flow/etf 요소는
여전히 전일(2026-07-20) EOD 그대로다.

프런트: `SentimentGauge.jsx`(대시보드 모달 + `MarketPage.jsx` 양쪽에서 재사용)의
breadth 요소 라벨 옆에 `components.breadth.source === "live"`일 때만 작은
"장중" 배지를 추가(최소 침습, 별도 상태/폴링 신설 없음 — 이미 있는
`sentiment.components` 응답을 그대로 사용).

### Phase 5.6 — 남은 EOD 항목 라벨링 정직성 점검 (2026-07-21)

"아직도 최신 정보가 아닌게 많아" 지적에 대한 전수 점검(Playwright 스크린샷 +
API date 필드 대조) 결과. 이번엔 "라이브로 못 바꾼다"가 아니라 **라이브 불가
항목의 날짜 표시 자체가 없거나 혼란스러운 케이스**를 찾는 게 목적 — 데이터
소스 구조적 한계(§4.7-4, §7)는 이미 충분히 실측·기록됐으므로 재조사하지 않음.

발견:
- **투자자예탁금 / 신용융자(코스피·코스닥) / 대차잔고** 3개 카드에 날짜 배지가
  아예 없음 — 실제로는 각각 07-16(예탁금·신용융자, KOFIA 5영업일 지연)·
  07-20(대차잔고)로 서로 다르게 지연되는데 오늘 값처럼 보임
- **ETF 경유 상위** 카드는 형제 카드(수급상위="확정 MM-DD", 거래대금상위="7분
  경신")와 달리 헤더에 날짜/갱신주기 배지가 없음. 모달은 "YYYY-MM-DD 기준"
  단일 날짜를 표기하지만 내부 `top_etfs[].date`는 기여 ETF마다 제각각(예:
  07-20/07-15 혼재)이라 단일 날짜 라벨이 실제 혼재를 감춤

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.6-1 ✅ | 예탁금·신용융자·대차잔고 날짜 배지 | ETF순유입/WTI 카드와 동일한 "MM-DD" 날짜 배지 패턴을 3개 카드에 통일 추가 | 3개 카드 모두 실제 데이터 날짜 노출 — **완료(2026-07-21)** |
| 5.6-2 ✅ | ETF 경유 상위 라벨 보강 | 카드 헤더에 "확정 [top-level date]" 배지 추가, 모달의 기여 ETF 배지에 개별 날짜(툴팁 또는 인라인) 노출 | 카드·모달 모두 날짜 불일치를 사용자가 알 수 있음 — **완료(2026-07-21)** |
| 5.6-3 ✅ | 업종·테마/베이시스/외인선물 1분 티어 회귀 수정 | §5.5-2가 프런트 폴링 주기만 옮기고 백엔드 TTL(420초)·스케줄러 잡 배정을 안 옮긴 회귀 발견·수정 | 3개 소스 실제로 60초 캐시 + 60초 스케줄러 잡에서 갱신 — **완료(2026-07-21)** |

#### §5.6-3 1분 티어 회귀 수정 결과 (2026-07-21)

사용자가 "업종·테마 강약, 종목 랭킹 요약의 일부가 최신 정보가 아니야.. 정보
갱신이 없어"라고 재차 지적. `GET /api/groups/live`를 90초 간격으로 재호출해
byte-for-byte 동일 응답(변동 없음)을 실측 확인, 코드 추적 결과 원인 발견:
§5.5-2에서 groupLive/basisLive/futuresFlowLive를 7분→1분 티어로 옮긴다며 실제로는
**프런트 폴링 주기(setInterval)만** 옮겼고, 백엔드 쪽 두 군데(`routers/groups.py`·
`routers/basis.py`·`routers/markets.py`의 `LIVE_TTL_SECONDS`/
`_FUTURES_FLOW_LIVE_TTL_SECONDS` 캐시 TTL 420초, `collectors/live_refresh.py`의
스케줄러 잡 배정 — 셋 다 여전히 7분 잡 `_run_live_refresh_extra` 소속)를 옮기는
걸 빠뜨렸다. 결과: 프런트는 60초마다 요청했지만 서버는 여전히 7분에 한 번만
실제로 네이버를 재조회하고 있었다 — 요청 빈도만 늘고 데이터 신선도는 그대로인
회귀. (§5.5-2 완료 보고의 "8분 간격 실관찰로 값 변화 증명"도 이 버그를 놓친
원인 중 하나였다 — 8분 창이 7분 캐시 경계를 우연히 하나 걸쳐서 "변한다"로
보였을 뿐, 60초 단위 세밀 검증은 안 됐었다.)

수정: 세 warm 함수(`_warm_basis_live`/`_warm_groups_live`/`_warm_futures_flow_live`)
호출을 `_run_live_refresh_extra`(7분)에서 `_run_live_refresh`(60초)로 옮기고,
세 라우터의 TTL 상수를 420→60초로 맞췄다. 7분 잡은 이제 `_warm_value_rank_live`
하나만 남는다(코스피+코스닥 전량 페이지네이션이 유일하게 비싼 소스라는 원래
근거 그대로 유지).

검증: 백엔드 로그에서 `cache warmed` 로그가 15:26:37→15:27:34→15:28:34로 정확히
60초 간격 발동 확인. 같은 60초 창 안에서 basis(11.41→11.31)·futures-flow 개인
순매수(175,400→190,900) 실제 값 변화 확인(groups 상위 3개는 우연히 같은 값이
반복됐지만 cached_at 자체가 06:27:35→06:29:01로 전진해 재조회가 실제로
일어났음은 로그로 별도 확인). pytest 344개 전체 통과(회귀 재현/방지용 신규
테스트 2개 추가 — 7분 잡이 basis/groups/futures-flow를 더 이상 호출하지
않는지 어서션하는 테스트 포함).

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.6-4 ✅ | 지수 일봉/등락종목수 얼어붙은 스냅샷 정정 | 오늘(2026-07-21) 도중 언젠가 한 번 조회된 "잠정 스냅샷"이 index_ohlcv·market_breadth에 굳어져 하루 종일 안 바뀌던 문제 발견·수동 배치로 정정 | 1D/1M 지수 차트 일치, 게이지 EOD 값 정상 — **완료(2026-07-21)** |
| 5.6-5 ✅ | 종목 상세 모달 SOT 회귀 수정 | `/api/stocks/{code}/series`의 캔들·수급 캐시가 "오늘 행 존재=끝"으로 장중 내내 얼어붙던 구조적 버그 — 장중엔 쿨다운(60초)마다 재조회하도록 수정 | 리스트 카드와 종목 상세 모달이 같은 값을 보여줌 — **완료(2026-07-21)** |

#### §5.6-4 지수 일봉/등락종목수 얼어붙은 스냅샷 정정 (2026-07-21)

사용자가 "코스피 1D 1분봉과 1M 1D가 안 맞아, 전체적으로 어제 정보 같아 —
로컬 문제냐 서버 문제냐"고 지적. DB(index_ohlcv)를 직접 조회해 원인 확인:
오늘(2026-07-21) 행이 `close=6519.16`으로 박혀 있었는데, 그 시각 라이브
1분봉은 이미 6747선까지 올라 있었다 — open(6553.88)·high(6556.57) 범위가
오전 초반 구간과 일치해, 정식 18:00 배치(아직 안 돎, 그때는 15시대)가 아니라
이 세션 도중 다른 작업 검증용으로 한 번 실행됐던 백필/배치 호출이 그 순간의
장중 잠정치를 그대로 확정치처럼 남겨둔 것으로 보인다. 같은 증상이
market_breadth에도 있었다(코스피 상승202/하락658 — 실제 라이브는 상승
1500+대였는데 완전히 역행하는 이른 시간대 스냅샷).

로컬도 서버 코드도 아니라 **DB에 박힌 하나의 오래된 잠정 스냅샷이 "오늘
확정치"인 척** 하고 있던 것 — API/JSON 로직 자체는 정상이었다.

조치: 마침 조사 도중 정규장이 마감(15:30 KST)돼서, 18:00 예정이던 일별
배치(`collectors.scheduler._run_all_jobs`, REGISTRY 11개 잡)를 즉시 수동으로
한 번 실행해 오늘자 실제 마감 데이터로 전부 갱신했다. 검증: 1분봉·일봉 코스피
종가 6747.95로 일치, 등락종목수 코스피 상승485/하락382로 정상화(게이지도
"eod" 소스로 정확히 반영), 나머지 9개 잡 전부 정상 완료(수급 상위만 여전히
07-16/07-20 — 소스 자체가 최신일 쿼리를 지원 안 하는 기존에 알려진 구조적
한계, 문제 아님). 코드 변경 없음(순수 데이터 정정), 18:00 정식 배치가 한 번
더 돌면 최종 확정치로 다시 덮어써질 예정.

#### §5.6-5 종목 상세 모달 SOT 회귀 수정 (2026-07-21)

이어서 사용자가 "리스트마다 삼천당제약 세부 정보가 다르다 — SOT(source of
truth) 이슈"라고 지적. `routers/stocks.py`의 `_ensure_candles_cached`/
`_ensure_flows_cached`(종목 상세 모달이 쓰는 `/api/stocks/{code}/series`의
DB 캐시 워머)를 추적해 원인 확인: 두 함수 다 "`stock_ohlcv`/`stock_flow`에
오늘 날짜 행이 이미 있으면 무조건 캐시 히트, 외부 호출 생략"이었다 — 장중이든
아니든 상관없이. 즉 **그날 그 종목 상세를 처음 연 사람이 그 순간의 장중
잠정 스냅샷을 하루 종일 고정**시켜버리고, 이후 아무리 다시 열어도 그 값 그대로
나온다. 반면 리스트 카드들(attention/value-rank/live 등)은 각자 60초~7분
TTL로 계속 새로 받아온다 — 그래서 같은 종목인데 "리스트에선 -29.75%인데
모달 열면 다른 값"이 나왔다. §5.6-4(index_ohlcv/market_breadth)와 같은
계열의 문제지만 원인은 다르다 — 5.6-4는 일회성 오염(잘못된 수동 배치
실행분), 이건 상시 재현되는 구조적 캐시 설계 버그다.

수정: 두 함수에 `market_hours.is_market_closed` 체크를 추가 — **장 마감**일
때만 "오늘 행 존재"를 진짜 캐시 히트로 취급한다. 장중이면 오늘 행이 있어도
기존 쿨다운(60초, 원래 "휴일에 매 요청마다 재시도" 방지용으로 있던 장치)이
지날 때마다 다시 외부(네이버/키움)를 불러 최신 잠정치로 갱신한다 — 쿨다운
자체는 그대로 둬 과호출은 막는다. 회귀 재현/방지 테스트 2개 추가
(`test_stocks_router.py`): 기존 "두 번째 요청은 캐시 히트" 테스트는
`is_market_closed=True`로 명시 고정하도록 수정(원래 벽시계 시각에 우연히
의존하던 취약한 테스트였다), 신규 테스트가 "장중이면 오늘 행이 있어도
쿨다운 지나면 다시 호출해 값이 갱신됨"을 검증한다. pytest 345개 전체 통과.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.6-6 ✅ | NXT 확장세션 반영 | 개별 종목 라이브 소스(attention·value-rank·종목 상세)의 "장 마감" 판정을 KRX 정규장(09:00~15:30)에서 NXT 확장세션(08:00~20:00)으로 분리 | 15:30~20:00에도 개별 종목 라이브가 계속 갱신됨 — **완료(2026-07-21)** |

#### §5.6-6 NXT 확장세션 반영 (2026-07-21)

사용자가 "NXT 장이 있어서 오전 8시부터 오후 8시까지 장이 열려 있어, 지금
끝난 게 아니야"라고 지적(18:3x KST, §5.6-4에서 "정규장 마감(15:30)"이라고
판단해 배치를 앞당겼던 시점 이후). 이 앱의 모든 "장 마감" 판정은 그때까지
`market_hours.is_market_closed`(KRX 정규장 09:00~15:30) 단 하나뿐이었다 —
NXT(넥스트레이드, 2025년 개설된 국내 대체거래소) 존재를 반영하지 못했다.

실측(18:36 KST)으로 두 부류가 다르게 움직임을 확인:
- 키움 ka20005(지수 분봉) — 마지막 봉이 정확히 `15:30:00`에서 끊김, 그 이후
  봉 없음 → **KOSPI/KOSDAQ 공식 지수는 정규장 마감에 고정**(NXT 거래는
  개별 종목 호가일 뿐 공식 지수 산출엔 반영 안 됨).
- 키움 ka00198(관심순위) — 18:36에도 `tm=183600`으로 살아있는 응답, 삼성전자
  등락률이 15시대와 다르게 계속 갱신 → **개별 종목은 NXT에서 20:00까지
  계속 거래**.
- 네이버 개별 종목 거래대금 목록(value-rank 소스) — 삼천당제약 거래대금
  242,510→244,400백만원, 등락률 -29.75%→-29.79%로 15시대 캡처와 18시대
  재조회 사이 실제 변동 확인 → 이것도 NXT 반영.

즉 §5.6-4에서 "정규장 마감했으니 배치를 앞당겨도 된다"고 판단한 건 지수/
등락종목수 등 **집계·지수 통계**에 대해서는 맞았지만(그것들은 실제로 정규장
마감에 고정됨), 개별 종목 데이터 전반의 "장 마감" 게이트가 전부 이 좁은
창 하나만 썼던 건 NXT 도입 이전 가정이 그대로 남아있던 구조적 누락이었다.

수정: `market_hours.py`에 `NXT_OPEN_TIME_KST`(08:00)/`NXT_CLOSE_TIME_KST`
(20:00)/`is_nxt_closed()`를 추가하고, 개별 종목 라이브 소스 3곳의 장 마감
게이트를 `is_market_closed`→`is_nxt_closed`로 교체했다:
- `routers/markets.py::_warm_attention`(ka00198)
- `routers/flow_rank.py::_warm_value_rank_live`(네이버 거래대금 목록)
- `routers/stocks.py::_ensure_candles_cached`/`_ensure_flows_cached`(§5.6-5에서
  방금 추가한 `is_market_closed` 체크를 `is_nxt_closed`로 재교체 — 5.6-5의
  "장중엔 재조회" 원칙 자체는 맞았지만 "장중"의 정의가 너무 좁았다)
- `collectors/live_refresh.py`의 두 스케줄러 잡(60초/7분) **잡 레벨** 게이트도
  더 넓은 NXT 창으로 바꿔 15:30~20:00에도 잡 자체가 계속 돌게 했다 — 잡
  안에 섞여 있는 지수/집계 함수(breadth·flow·index-tiles·fx·basis·groups·
  futures-flow)는 각자 내부의 좁은 `is_market_closed` 체크로 스스로
  건너뛰므로 이중 게이트가 안전하게 공존한다.

지수/집계 통계(index-tiles·breadth·basis·groups·futures-flow) 게이트는
그대로 뒀다 — 실측상 정규장 마감에 정말로 고정되기 때문에 바꾸면 오히려
"이미 끝난 지수를 계속 호출"하는 낭비가 된다. 환율(fx)도 이번 범위 밖으로
남겨뒀다 — NXT와 무관한 별도 시장이라 여전히 `is_market_closed`를 쓰는데,
저녁 시간대 환율 라이브가 과도하게 막힐 수 있는 별도의 알려진 한계로 문서화만
해둔다(추후 과제).

검증: `GET /api/markets/attention`·`GET /api/markets/value-rank/live`·
`GET /api/markets/scalp-candidates`를 18:43 KST(NXT 세션 중, KRX 정규장은
이미 마감)에 재호출 — 셋 다 `market_closed: false`로 정상 응답하고 실제
값도 15시대 대비 갱신됨을 확인. 같은 시각 `GET /api/markets/index-tiles/live`·
`GET /api/markets/breadth/live`는 여전히 `market_closed: true`로 응답해
지수/집계 쪽은 의도대로 정규장 마감에 고정된 채임을 확인. 영향받은 기존
테스트 4개 파일(`test_markets_attention_router.py`·`test_stocks_router.py`·
`test_live_refresh_snapshot_wiring.py`·`test_value_rank_live_router.py`)의
monkeypatch 대상을 `is_market_closed`→`is_nxt_closed`로 맞춰 pytest 345개
전체 통과.
- [ ] KIS 클라이언트 + `FHPTJ04040000`으로 market_flow 소스를 pykrx→KIS 교체 (pykrx는 검증용 강등)
- [ ] 선물 투자자별: KIS `FHPTJ04030000` 시장구분 코드 실호출 검증 → 실패 시 KRX 파생 통계 파싱
- [ ] 키움 WebSocket: 장중 잠정 수급(ka10063 폴링 or `0w` 프로그램매매), StockPage 실시간 갱신
- [ ] 매집 시그널 조건 충족 시 알림 (초기엔 대시보드 배지, 이후 텔레그램 등)

### Phase 5.7 — 스켈핑 후보 추적 기록 (2026-07-21 사용자 제안, 관찰 로그)

사용자 제안: "스켈핑 후보가 유효한지 추이분석을 기록해서 모의투자 했다면
어떻게 될지 기록해 두면서 로직을 개선해 볼까?" — score(§quant/screener.py)가
실제로 의미 있는지 지금은 검증 없이 쓰고 있다. 픽을 DB에 기록하고 이후 가격을
샘플링해두면 나중에 score와 실제 추이의 상관관계를 볼 수 있는 근거가 쌓인다.

**원칙**: §5 전체 원칙 그대로 — "매매 추천 성과"가 아니라 **관찰 기록**이다.
실제 매매를 시뮬레이션하는 게 아니라 "이 시점에 이 종목이 후보였다 → 이후
등락률이 이렇게 흘러갔다"는 사실만 중립적으로 쌓는다. 진입/청산가 매매
수수료·슬리피지 등 실제 매매 조건은 반영하지 않는다(그 정밀도가 필요하면
나중 단계).

**설계**:
1. **진입 기록(1일 1종목 1회)** — 그날 스켈핑 후보 상위 N(예: 10위)에 어떤
   종목이 "처음" 등장하면 그 순간을 기록한다(같은 날 재등장은 중복 기록
   안 함 — 반복 등장을 서로 다른 신호로 세면 자기상관으로 통계가 왜곡됨).
   기록 항목: date, code, name, market, entry_time(KST), entry_rank,
   entry_score, entry_change_rate(그 시점 attention/value-rank 우선순위
   그대로, scalp.py 기존 관례 재사용), entry_turnover, in_attention_top_at_entry.
2. **추이 샘플링(고정 호라이즌)** — 진입 후 5분/15분/30분/60분/당일 마감
   시점에 그 종목의 change_rate를 다시 조회해 기록한다(호라이즌 컬럼을
   미리 만들어두고 해당 시각이 지나면 채우는 방식 — 매 폴링마다 "아직 안 채운
   호라이즌 중 시각이 지난 것"만 처리). 조회 우선순위는 scalp.py와 동일하게
   attention 우선, 없으면 value-rank/live 폴백(§5.4-1 관례 재사용) — 새 외부
   호출을 늘리지 않고 이미 도는 60초/7분 캐시만 재사용한다.
3. **저장 위치**: 반드시 DB(§5.6 "1D 누적 버퍼가 재배포마다 날아간 문제"
   교훈 — 메모리 저장 금지). 새 테이블 `scalp_pick`(entry 정보 + 호라이즌별
   change_rate 컬럼, PK: date+code). Alembic 마이그레이션 필요(레포 컨벤션).
4. **스케줄링**: 기존 60초 라이브 티어(collectors/live_refresh.py)에
   "신규 진입 기록 + 도래한 호라이즌 채우기" 단계를 추가 — 이미 그 안에서
   워밍된 attention/value-rank 캐시를 재사용하므로 새 외부 API 호출이
   없다(예산 불변).
5. **조회 API**: `GET /api/markets/scalp-candidates/track-record` — 최근
   N일의 pick 목록 + 각 호라이즌 change_rate를 그대로 반환(집계·상관계수
   계산은 이번 단계에서 안 함 — 데이터가 며칠~몇 주 쌓이기 전엔 표본이 너무
   작아 의미가 없다, §"scalp 유효성 검증" 대화에서 이미 합의된 제약).
6. **로직 개선(score 가중치 조정 등)은 이번 phase 범위 밖**이다 — 기록
   인프라만 먼저 깔고, 데이터가 쌓인 뒤 별도 phase에서 분석·조정한다.

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 5.7-1 | DB 스키마 + 진입 기록 | `scalp_pick` 모델 + 마이그레이션, 신규 진입 감지·기록 로직 | 스켈핑 후보 신규 진입 시 DB에 정확히 1행 기록 |
| 5.7-2 | 호라이즌 샘플링 | 5/15/30/60분 + 당일 마감 change_rate 채우기, live_refresh 60초 잡에 배선 | 시간 지나면 해당 컬럼이 채워짐, 새 외부 호출 없음 |
| 5.7-3 | 조회 API | `GET /api/markets/scalp-candidates/track-record` | 최근 N일 pick + 호라이즌 값 반환 |

## 6.5 개발 진행 방식 (컨텍스트/토큰 운영)

- **계획·리뷰는 메인 세션, 코딩은 Sonnet 서브에이전트**: 위 표의 작업(1-1, 1-2, …)
  하나가 에이전트 1회 위임 단위. 에이전트 프롬프트에는 "PLAN.md §5.2/§5.3의 해당 부분 +
  완료 기준"만 전달해 자기완결로 실행
- 작업 간 의존이 없으면 병렬 위임 (예: 1-2와 1-4는 1-1 완료 후 동시 진행 가능)
- 각 작업 완료 시 에이전트가 **실행 검증**(완료 기준의 명령/호출)까지 마치고 결과만 보고
- 메인 세션은 큰 파일을 직접 읽지 않고 에이전트 보고 + 스모크 테스트로 확인

---

## 7. 리스크 / 미확정 사항

| 항목 | 내용 | 대응 |
|---|---|---|
| 키움 rate limit | 공식 수치 미공개 (1~20건/초 관측치 상충) | 초기에 실측, 클라이언트에 보수적 rate limiter 내장 |
| 선물 수급 소스 | KIS `FHPTJ04030000`의 선물 시장코드 지원 여부 미검증 | Phase 4에서 실호출 검증, 실패 시 KRX/네이버 파싱 |
| pykrx 안정성 | 2026-02 개편 후 **data.krx.co.kr 무료 회원 로그인 필수**(KRX_ID/PW 없으면 HTTP 400 전면 차단 — 구현 중 실확인). 무인증 크롤링 시대는 끝남 | 무료 가입으로 당장은 사용 가능하나, KIS `FHPTJ04040000` **또는 키움 `ka10051`**(2026-07-19 실호출 확정, §1 참고 — 종합 행 1개로 시장 전체 13분류 순매수, 과거 일자 조회 가능)로의 1차 소스 교체를 권장 |
| yfinance 429 | 2024~2025 rate limit 사태 반복 | 하루 1회 배치 + FRED 백업 자동 전환 |
| KOFIA freesis 파싱 | 비공식 통계 화면 POST 파싱 — 사이트 개편 시 장애 가능 | collect_log 실패 감지, 일별 T+1 지표라 하루 지연 허용 가능 |
| ka20001 등락 종목수 | REST 응답에 구 opt20001의 상승/하락 종목수 필드가 있는지 미확정 | 1.5-1 probe로 실측 확정, 없으면 pykrx 카운트(일별)/네이버 파싱(장중) 대안 |
| 두바이유 일별 | 무료 공식 API 없음 | WTI/브렌트만 우선, 두바이는 월별 or 오피넷 파싱 |
| 수급 데이터 시점 | 확정치는 장마감 후 | 장중에는 잠정치(`ka10063`)임을 UI에 명시 |
| 지수 시세 소스 | KRX Open API(`idx/kospi_dd_trd` 등)가 **403 Forbidden**(서비스 이용 승인 미비, 2026-07 확인)이라 `/api/markets/{market}/series`가 라이브 500을 반환. `index_ohlcv`를 배치로 채워 라우터는 DB만 읽도록 전환(collectors/ohlcv.py) — 코스피/코스닥은 yfinance(`^KS11`/`^KQ11`) 1차 + 네이버 fchart(`fchart.stock.naver.com/siseJson.naver`, 비공식) 폴백, 코스피200선물(k200_futures)은 yfinance에 심볼이 없어 네이버 fchart(symbol=FUT)만 사용. 두 소스 모두 거래대금(원화 금액)을 제공하지 않아 `index_ohlcv.value`는 당분간 NULL(거래대금 차트는 0으로 표시) | 임시 조치 — 추후 키움 차트 TR(OHLCV+거래대금)로 교체 예정. KRX Open API 승인이 나면 되돌릴 수 있도록 `krx_client.py`/`services.get_index_series`·`get_futures_series`는 그대로 보존 |
