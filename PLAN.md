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

### Phase 4 — 선물 + 실시간 (선택)
- [ ] KIS 클라이언트 + `FHPTJ04040000`으로 market_flow 소스를 pykrx→KIS 교체 (pykrx는 검증용 강등)
- [ ] 선물 투자자별: KIS `FHPTJ04030000` 시장구분 코드 실호출 검증 → 실패 시 KRX 파생 통계 파싱
- [ ] 키움 WebSocket: 장중 잠정 수급(ka10063 폴링 or `0w` 프로그램매매), StockPage 실시간 갱신
- [ ] 매집 시그널 조건 충족 시 알림 (초기엔 대시보드 배지, 이후 텔레그램 등)

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
