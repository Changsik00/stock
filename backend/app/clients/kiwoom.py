"""Kiwoom Securities REST API client (PLAN.md §1, §5.4, §6 Phase 2-1).

키움 REST API(2025-03 출시, openapi.kiwoom.com)는 기존 OCX 방식과 달리 순수
HTTP라서 Mac/Linux에서도 동작한다. 이 모듈은 OAuth 접근토큰 발급·캐시, TR
(Transaction, api-id) 호출 공통 래퍼, per-TR token-bucket rate limiter를
제공한다.

## 스펙 출처 (2026-07-15 조사)

1. 공식 문서 https://openapi.kiwoom.com/guide/apiguide — WebFetch로 확인:
   - 실전 호스트 `https://api.kiwoom.com`, 모의 호스트 `https://mockapi.kiwoom.com`
   - 토큰 발급: `POST /oauth2/token`, `Content-Type: application/json;charset=UTF-8`,
     요청 body `{grant_type, appkey, secretkey}`, 응답 body
     `{return_code, return_msg, token_type, token, expires_dt}`
   - TR(예: ka10008) 상세 페이지에서 확인된 공통 헤더:
     - 요청: `authorization: Bearer <token>`(필수), `api-id`(필수, TR코드),
       `cont-yn`/`next-key`(선택, 연속조회 시 직전 응답 헤더값을 그대로 재전송)
     - 응답: `cont-yn`(다음 데이터 유무 Y/N), `next-key`(다음 조회 키), `api-id`
   - **주의**: SPA라 apiId 쿼리파라미터별 상세 필드까지는 WebFetch로 안정적으로
     긁히지 않았다(TR마다 다른 카테고리/URL이 나와야 하는데 일부 TR에서 직전 결과가
     재사용되는 현상을 확인함). 아래 `TR_RESOURCE_URL`의 개별 TR URL은 공식 문서
     대신 (2)를 근거로 삼았다.
2. GitHub https://github.com/younghwan91/kiwoom-rest-api (PyPI `kiwoom-client`,
   MIT, 실서버 스모크 테스트 `tests/integration_api_smoke.py` 포함) — `gh api`로
   소스 원문 확인:
   - `src/kiwoom_rest_api/base.py`: 요청 헤더 구성, POST + JSON body, HTTP 429 →
     지수 백오프 재시도, body의 `return_code`(0=성공, 5=요청 초과)로 성공 판정
   - `src/kiwoom_rest_api/domestic/stock_info.py`: `RESOURCE_URL = "/api/dostk/stkinfo"`
     아래 `ka10001`(종목기본정보), `ka10059`(투자자기관별종목별) 등록
   - README "요청 제한(Rate Limit)" 절 — **실측치**: TR(api_id)별 독립 버킷,
     지속 안전 속도 약 1 req/s(거부 0건), 순간 버스트 약 2건, 초과 시
     `HTTP 429` + body `{"return_code": 5, "return_msg": "허용된 요청 개수를
     초과하였습니다"}`. 이 값을 이 클라이언트의 기본 rate limit(1 req/s, burst 2)
     로 그대로 채택했다 — PLAN.md §1 "Rate limit 공식 미공개... 보수적으로 설계"
     방침과 일치.
   - `tests/integration_api_smoke.py`의 `PARAMS` 딕셔너리 — 실호출로 검증된
     TR별 요청 body 파라미터 예시. `ka10001: {"stk_cd": "005930"}`,
     `ka10059: {"dt": <YYYYMMDD>, "stk_cd": "005930", "amt_qty_tp": "1",
     "trde_tp": "0", "unit_tp": "1000"}`를 이 클라이언트의 편의 메서드 기본값으로
     그대로 사용했다.

`ka10059`의 URL을 공식 문서 TR 상세 페이지에서 조회하면 `/api/dostk/frgnistt`로
표시되기도 했는데, 이는 위 "주의" 사항(SPA 재사용 의심)과 충돌한다. **2026-07-19
실전 키로 실호출해 `/api/dostk/stkinfo`가 맞다고 확정했다**(아래 "Phase 1.5-1
probe 실측 확정" 참고) — `/api/dostk/frgnistt`는 오탐이었던 것으로 보인다.

## Phase 1.5-1 probe 실측 확정 (2026-07-19, 실전 키로 실호출 완료)

`.env`의 `KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET`(2026-07-17 블로커였던 8001
인증 실패가 재발급으로 해소됨)으로 실전 서버(`api.kiwoom.com`)에 실호출해
아래를 전부 확정했다. 모의 서버(`mockapi.kiwoom.com`)는 이 키를 거부함
(실전 전용 키 — 정상, PLAN.md 참고).

- **TR URL 3종 전부 실호출로 확정**(추정 아님):
  - `ka10001`(종목기본정보) → `/api/dostk/stkinfo` — 200, `return_code=0`,
    필드 47개. 005930 조회 성공(`stk_nm='삼성전자'`).
  - `ka10059`(종목별투자자기관별) → `/api/dostk/stkinfo` — 200,
    `return_code=0`. 응답이 `stk_invsr_orgn` 배열(일자별) 형태.
  - `ka20001`(업종현재가) → `/api/dostk/sect` — 200, `return_code=0`,
    필드 25개(+ `inds_cur_prc_tm` 분단위 배열). (2) GitHub 소스코드 근거가
    전부 맞았다.
- **`ka20001`에 등락 종목수 필드 존재 확정** — PLAN.md §3.5의 핵심 미확정
  사항 해소. 필드명: `rising`(상승), `stdns`(보합), `fall`(하락),
  `upl`(상한), `lst`(하한). 2026-07-18 장중 실측값이 네이버 breadth와
  **정확히 일치**:
  - KOSPI(`inds_cd="001"`): `rising=384, stdns=40, fall=488, upl=6` ↔
    네이버 "코스피 384↑/40—/488↓/상한6" — 완전 일치.
  - KOSDAQ(`inds_cd="101"`): `rising=501, stdns=56, fall=1182` ↔
    네이버 "코스닥 501↑/56—/1182↓" — 완전 일치.
  - 결론: `ka20001`을 breadth 소스로 채택 가능(네이버 파싱 대체 후보).
    다만 현재 §1.5-3 구현은 네이버 임시 소스로 이미 동작 중이므로,
    교체는 "정밀화" 우선순위 작업으로 남겨둔다(장중 프록시가 이미
    네이버로 동작하고 있어 급하지 않음) — PLAN.md §3.6-2 참고.
- **rate limit 실측**: 클라이언트 rate limiter를 끄고(사실상 무제한) 같은
  TR(`ka10001`)을 백투백으로 연속 호출하면 **4번째까지 OK, 5번째부터 즉시
  HTTP 429**(응답 시간 각 ~9ms, 사실상 지연 없이 순간 버스트). 이후
  클라이언트 기본값(`rate_limit=1.0`, `rate_burst=2`)을 그대로 사용해 8회
  연속 호출 시 **전부 OK, 429/rc=5 없음**(처음 2건 버스트 후 ~1초 간격).
  결론: 기존 README 실측 기반 기본값(1 req/s, burst 2)이 실측 관찰(순간
  버스트 한도 ~4)보다 보수적이라 안전 — **변경 불필요, 그대로 유지**.
- 실측에 사용한 총 TR 호출 수는 약 20건(토큰 발급 제외) — 절제된 수준에서
  중단.

## ka10051(업종별투자자순매수) 추가 검증 (2026-07-19, 시장 전체 수급 후보)

PLAN.md §1 "시장 전체(코스피/코스닥) 투자자별 순매수 일별 시계열 전용 TR 없음
→ `ka10051` 우회 가능하나 비효율" 판단을 재검증하기 위해 `/api/dostk/sect`
(ka20001과 동일 카테고리, GitHub 소스 `sector.py` 근거)로 실호출:

- 요청 파라미터 확정: `{"mrkt_tp": "0|1"(코스피/코스닥), "amt_qty_tp": "0",
  "base_dt": "YYYYMMDD", "stex_tp": "3"}`. **과거 일자 지정 가능** —
  `base_dt=20260702`로 조회하면 당일(`20260718`)과 다른 실제 값이 돌아옴
  (예: KOSPI `pred_pre` -46381 vs -65532) → 1일 1콜로 원하는 만큼 과거로
  백필 가능(날짜 범위 조회는 안 되고 날짜당 1콜).
- 응답은 `inds_netprps`(업종별 배열) — **첫 번째 행이 `inds_cd="001_AL"`
  (KOSPI) / `"101_AL"`(KOSDAQ) `inds_nm="종합(KOSPI/KOSDAQ)"`인 시장 전체
  합계 행**이라 개별 업종을 합산할 필요 없이 그 한 행만 쓰면 시장 전체
  순매수로 바로 쓸 수 있음. 이 행의 `trde_qty`(424280)가 같은 날 ka20001의
  `trde_qty`와 정확히 일치 — 같은 모집단임을 교차 확인.
- 투자자 분류 컬럼 13종: `sc_netprps`, `insrnc_netprps`(보험),
  `invtrt_netprps`(투신), `bank_netprps`(은행), `jnsinkm_netprps`(연기금 추정),
  `endw_netprps`(기금 추정), `etc_corp_netprps`(기타법인), `ind_netprps`(개인),
  `frgnr_netprps`(외국인), `native_trmt_frgnr_netprps`(내국인대우외국인),
  `natn_netprps`(국가), `samo_fund_netprps`(사모펀드), `orgn_netprps`(기관계
  합계) — pykrx의 13분류와 대등한 세밀도.
- **결론(pykrx 대체 가능)**: `ka10051`은 시장 전체 투자자별 순매수를
  (a) 파라미터로 과거 임의 일자 조회, (b) 종합 행 1개로 시장 전체 집계,
  (c) 13분류 세부 투자자 구분까지 전부 충족한다. PLAN.md §1의 "비효율"
  평가는 재검토 필요 — 날짜당 1콜이지만 rate limit(1 req/s) 기준 3년
  백필(~750영업일)도 약 12분이면 끝나 KRX 로그인(`KRX_ID`/`PW`) 없이
  pykrx를 대체할 유력 후보. `TR_RESOURCE_URL`에 등록해 둠. 실제 마이그레이션
  여부(1-4 소스 교체)는 별도 의사결정 필요 — 이번 probe는 실호출 가능성만
  확정.

## ka10063/ka10066 장중 잠정 수급 probe (2026-07-18, PLAN.md §6 Phase 3.7-3)

`.env`의 실전 키로 ka10063(장중투자자별매매요청)·ka10066(장마감후투자자별매매요청)을
실호출해 URL·파라미터·응답 스키마를 확정했다. **결론부터: 이 둘은 PLAN.md가
가정한 "시장 전체 잠정 순매수 1행" 응답이 아니다** — 아래 근거로 `GET
/api/markets/flow/live`(routers/markets.py)는 이 두 TR 대신 `ka10051`을
"장중에도 쓸 수 있는 소스"로 재사용하도록 설계를 바꿨다(근거는 이 절 마지막
문단). 두 TR 모두 편의 메서드(`intraday_investor_trading`,
`after_hours_investor_trading`)는 추가해 뒀다 — 종목별 스크리닝 화면(예: "지금
외국인이 사는 종목 랭킹")을 만들 때는 그대로 유용하다.

- **URL**: 둘 다 GitHub 소스(`domestic/market.py`, `RESOURCE_URL =
  "/api/dostk/mrkcond"`) 그대로 실호출 확인됨 — `/api/dostk/mrkcond`,
  200 + `return_code=0`.
- **파라미터**: GitHub `tests/integration_api_smoke.py`의 `PARAMS` 딕셔너리
  값을 그대로 실호출해 통과 확인:
  - ka10063: `{"mrkt_tp": "000|001|101", "amt_qty_tp": "1", "invsr": "0"~"9",
    "frgn_all": "1", "smtm_netprps_tp": "1", "stex_tp": "3"}`. `mrkt_tp`는
    "000"(전체)/"001"(코스피)/"101"(코스닥) 전부 200 확인(ka10051의
    "0"/"1" 표기와 다름 — TR마다 다른 코드 체계이니 섞어 쓰지 말 것).
  - ka10066: `{"mrkt_tp": "000|001|101", "amt_qty_tp": "1", "trde_tp": "0",
    "stex_tp": "3"}`.
- **응답 스키마(핵심 발견)**: 두 TR 다 **시장 합계 행이 아니라 종목별 배열**을
  준다.
  - ka10063 → `opmr_invsr_trde` 배열, 행마다 `stk_cd`/`stk_nm` +
    `netprps_amt`/`buy_amt`/`sell_amt`(및 수량 버전) — **`invsr`(투자자구분)
    파라미터로 딱 한 투자자 카테고리만 선택**해서 그 투자자가 그날 거래한
    종목만 나열한다(전 종목이 아님 — 실측: 코스피에서 `invsr` 값에 따라
    반환 종목 수가 6개~800개로 들쭉날쭉, 예: invsr=2→6종목,
    invsr=6→800종목). `invsr` 숫자 코드(0~9)가 정확히 어느 투자자
    분류(개인/외국인/기관 세부)에 대응하는지는 공식 문서로 확인하지
    못했다(SPA라 WebFetch로 상세 페이지가 안 긁힘 — 모듈 docstring 상단
    "주의" 절과 동일한 제약). 응답에 markt합계/총계 필드가 별도로 없어
    "시장 전체 순매수"를 얻으려면 `invsr` 카테고리마다 전 종목을
    cont-yn/next-key로 완전히 페이지네이션해서 직접 합산해야 한다.
  - ka10066 → `opaf_invsr_trde` 배열, 행마다 `stk_cd`/`stk_nm` + **13개
    투자자 카테고리 전부**(`ind_invsr`, `frgnr_invsr`, `orgn`, `fnnc_invt`,
    `insrnc`, `invtrt`, `etc_fnnc`, `bank`, `penfnd_etc`, `samo_fund`,
    `natn`, `etc_corp`)가 한 행에 다 들어있다 — `invsr` 파라미터 없이
    전 종목(코스피 실측 1,330종목, 100행/페이지 × 14페이지)을 코드순으로
    나열한다. ka10063과 마찬가지로 시장 합계 행은 없다.
- **페이지네이션 실측**: ka10066 `mrkt_tp="001"`(코스피) 전량을
  cont-yn/next-key로 14페이지(100행×13+30행) 끝까지 받아 `ind_invsr`(개인)/
  `frgnr_invsr`(외국인)/`orgn`(기관계)을 종목 전부 합산한 결과 —
  개인 +5,057,799 / 외국인 -2,069,721 / 기관계 -3,164,429(단위 만원,
  amt_qty_tp="1"). 같은 거래일 `ka10051`(코스피, amt_qty_tp="0"=백만원)의
  종합 행은 개인 +50,624 / 외국인 -20,698 / 기관계 -31,684(백만원) —
  100을 곱하면 +5,062,400 / -2,069,800 / -3,168,400으로 **오차 0.1% 이내
  일치**(합산 대상 종목 커버리지 차이로 추정되는 미세한 차이만 있음).
  이 교차검증으로 (a) ka10066 풀페이지네이션 합산이 ka10051 종합 행과
  같은 모집단을 가리키고, (b) 두 TR의 금액 단위가 100배 차이(ka10051=백만원,
  ka10066 amt_qty_tp="1"=만원)임을 확인했다.
- **장외 시간 동작(2026-07-18 토요일 실측, 비영업일)**: `ka10051`을
  `base_dt=오늘(토)`로 호출하면 에러/빈 값이 아니라 **가장 최근 확정
  거래일(금)과 완전히 동일한 값**을 반환한다(같은 시장·같은 필드 값이
  한 자리도 다르지 않음) — TR 자체가 "비영업일 조회 시 마지막 확정치로
  안전하게 폴백"하는 것으로 보인다. ka10063/ka10066도 마찬가지로 토요일에
  200 + 데이터가 왔다(빈 배열 아님) — 즉 이 TR들은 장외 시간에 에러를
  던지지 않고 마지막 알려진 값을 그대로 준다.
- **설계 결론**: `GET /api/markets/flow/live`(PLAN.md §6 3.7-3)는 위 근거로
  ka10063/ka10066 대신 `ka10051`(`sector_investor_net_buy`, 이미 §6 1-4
  일별 배치가 쓰는 것과 동일 TR)을 `base_dt=오늘`로 호출해 재사용한다 —
  시장당 1콜로 끝나고, 위 교차검증으로 ka10063/ka10066 풀페이지네이션
  합산과 사실상 같은 값을 준다는 근거가 있다. **단, 이번 조사는 주말이라
  ka10051이 실제 "장중"(트레이딩 진행 중)에 분 단위로 갱신되는지는 직접
  확인하지 못했다** — 지금까지 확인한 것은 "비영업일에 마지막 확정치로
  안전하게 폴백한다"까지다. 평일 장중 재확인은 사용자 몫으로 남긴다
  (PLAN.md §6 3.7-3 완료 기준 참고).

## ka00198(실시간종목조회순위) 조사·실측 (2026-07-19, "실시간 관심 종목 TOP20" 카드용)

이 TR은 (1)의 openapi.kiwoom.com SPA 상세 페이지가 "주의" 절에서 언급한 이유로
안정적으로 긁히지 않아, GitHub 코드 검색으로 TR id를 먼저 찾은 뒤 실호출로
검증했다.

- **TR id 발견 경로**: GitHub 공개 키움 REST API 클라이언트 저장소 코드 검색.
  - `younghwan91/kiwoom-rest-api`의 `stock_info.py`가 `realtime_stock_inquiry_rank`
    메서드로 `ka00198`을 등록해 둠 — (1)의 `ka10001`/`ka10059`와 같은 파일, 같은
    `RESOURCE_URL = "/api/dostk/stkinfo"` 카테고리.
  - `unohee/pykiwoom-rest`(문서 저장소)에 `ka00198`의 공식 필드 스펙 전문이
    있어 요청/응답 필드 의미(아래)를 교차 확인했다.
- **실호출로 검증**(실전 키, `api.kiwoom.com`): `POST /api/dostk/stkinfo`,
  요청 body `{"qry_tp": "4"}`(당일 누적). 200 + `return_code=0`, 응답 헤더
  `{'cont-yn': 'N', 'next-key': '', 'api-id': 'ka00198'}`.
  - 응답 body의 `item_inq_rank` 배열, 행마다 `stk_cd`(종목코드)/`stk_nm`(종목명)/
    `bigd_rank`(순위, 문자열 정수, 이미 1..N 정렬됨)/`base_comp_chgr`(기준가 대비
    등락율 %, 부호(`+`/`-`)가 이미 붙은 문자열이라 `float()`로 바로 파싱 가능) 등.
  - `qry_tp` 의미: "1"=1분, "2"=10분, "3"=1시간, "4"=당일 누적, "5"=30초.
  - **market(코스피/코스닥)·ETF 여부 필드는 이 TR 응답에 없다** — 필요하면
    로컬 `stocks` 테이블과 조인해야 한다(routers/markets.py의
    `GET /api/markets/attention` 참고).
  - probe에서 `qry_tp`가 1/4/5일 때 매번 정확히 20행이 돌아왔다(더 적거나
    많은 경우 관측 안 됨) — TOP20 카드 요구사항과 정확히 맞아떨어짐.

## ka00198 qry_tp 재실측 (2026-07-21, "1분 갱신" UI 문구가 거짓이었던 문제 조사)

**증상**: `GET /api/markets/attention`(qry_tp="4"=당일 누적)을 65초 간격으로 재호출해도
`queried_at`(캐시 갱신 시각)은 매번 바뀌는데 삼성전자/SK하이닉스/삼천당제약의
`change_rate`는 소수점까지 완전히 동일했다 — 60초 캐시/스케줄러는 정상 동작 중인데
(캐시 자체는 매 폴링마다 실제로 재호출됨, `queried_at`이 매번 앞으로 감), **소스
자체(qry_tp=4, 당일 누적)가 분 단위로는 거의 안 바뀌는 필드**였다.

**재실측 방법**: 라우터 캐시를 우회해 `KiwoomClient.realtime_inquiry_rank`를
`qry_tp` 1/2/3/5로 직접 6라운드(45초 간격, 약 4.5분) 호출, 삼성전자(005930)/
SK하이닉스(000660)/삼천당제약(000250) 세 종목의 `bigd_rank`/`base_comp_chgr`를
라운드마다 기록(백엔드 컨테이너 안, 실전 키, 2026-07-21 09:19~09:23 KST).

| qry_tp | 의미 | 관측 |
|---|---|---|
| "1" | 1분 | 45~70초 간격 5번 비교 중 4번 값이 바뀜(예: 000660 chgr +2.66→+2.95→+3.17→+3.00, 000250 순위 6→6→7→6) — **주기적으로 갱신되지만 과격하지 않음** |
| "2" | 10분 | 첫 비교(라운드1→2)에서만 바뀌고 이후 3라운드(약 2.5분) 연속 완전히 동일 — 이름 그대로 10분 단위로 갱신되는 것으로 보임, 60초 캐시엔 너무 느림 |
| "3" | 1시간 | 6라운드(약 4.5분) 내내 세 종목 전부 순위·등락률 완전히 동일 — qry_tp=4와 마찬가지로 60초 캐시에는 사실상 정지 화면 |
| "4" | 당일 누적(기존 기본값) | 이번 재실측에서도 별도로 `/api/markets/attention`을 4라운드(약 4분) 재호출해 삼성전자 change_rate=2.66이 그대로 고정됨을 재확인 |
| "5" | 30초 | 거의 매 라운드 바뀌지만 **변동폭·순위가 들쭉날쭉**(000250 순위 6→7→8→6, chgr가 오르내림 반복) — "너무 튀는 랭킹"이 실제로 관측됨 |

**결론**: `qry_tp="1"`(1분)로 교체. 대안 중 유일하게 (a) 60초 캐시 주기와 실제
갱신 빈도가 맞고 (b) qry_tp="5"처럼 매 폴링마다 순위가 출렁이지도 않는다 —
"1분 갱신" UI 문구가 처음으로 사실과 일치하게 된다. 기본값을 `"4"`→`"1"`로
변경(아래 `realtime_inquiry_rank` 시그니처), `routers/markets.py`의
`_warm_attention`도 동일하게 변경. 캐시 TTL(60초)은 그대로 유지 — qry_tp=1
자체가 이름 그대로 "1분" 단위라 60초 TTL과 자연스럽게 맞아떨어진다.

## ka10080(주식분봉차트요청)/ka20005(업종분봉차트요청) 실측 확정 (2026-07-21, PLAN.md §5.1)

실전 키로 `/api/dostk/chart`에 직접 실호출해 아래를 전부 확정했다(005930 및
inds_cd 001/101 기준, `.env` 실전 키, 총 호출 수 약 30건).

- **URL**: 둘 다 `/api/dostk/chart` (GitHub `domestic/chart.py` 근거와 일치).
- **파라미터**: `ka10080` → `{"stk_cd": code, "tic_scope": "1".."60", "upd_stkpc_tp":
  "1"}`(GitHub `tests/integration_api_smoke.py` PARAMS 예시와 동일, 수정주가
  반영 "1" 채택). `ka20005` → `{"inds_cd": "001"|"101", "tic_scope": "1".."60"}`
  (마찬가지로 GitHub PARAMS와 동일, mrkt_tp 불필요).
- **`tic_scope` 허용값 전부 실호출로 확인**: `1, 3, 5, 10, 15, 30, 45, 60`
  (분 단위) — 8개 값 전부 `ka10080`/`ka20005` 양쪽에서 200 + `return_code=0`
  확인됨(그 밖의 값은 시도하지 않음 — 요청 범위가 이미 이 8개였음).
- **응답 스키마**: `ka10080` → `data["stk_min_pole_chart_qry"]` 배열(항상 900행
  고정으로 관측됨). `ka20005` → `data["inds_min_pole_qry"]` 배열(마찬가지로
  900행). 행 필드는 동일: `cntr_tm`(체결시각 `YYYYMMDDHHMMSS`), `cur_prc`(그 봉의
  종가), `open_pric`/`high_pric`/`low_pric`, `trde_qty`(그 봉의 거래량),
  `acc_trde_qty`(그날 누적 거래량), `pred_pre`(전일대비, 참고용— 이 클라이언트는
  파싱하지 않음), `pred_pre_sig`.
  - **가격 필드 부호 인코딩 주의**: `cur_prc`/`open_pric`/`high_pric`/`low_pric`은
    "전일 대비 방향"을 나타내는 부호 문자(`+`/`-`)가 접두된 문자열이다(값 자체가
    음수라는 뜻이 아님) — 예: `open_pric="+654294"`, `cur_prc="-651627"`가 같은
    행에 같이 나온다(시가는 전일보다 위, 종가는 전일보다 아래). 절대값을 취해야
    실제 가격이 나온다. `pred_pre`는 드물게 `"--30433"`처럼 부호가 두 번
    겹치는 경우가 관측됐다(원인 미상, 아마 소스 쪽 포매팅 특이사항) — 이
    필드는 쓰지 않으므로 별도 대응 없이 무시한다.
  - `_parse_minute_price()`가 이 부호 접두 처리(선행 `+`/`-` 전부 소비 후 절대값)를
    전담한다.
- **하루 커버리지(연속조회 불필요)**: 한 번의 호출(900행)이 여러 거래일치를
  포함한다 — 예: `tic_scope=1`은 900행이 최근 3거래일(하루 약 382행, 09:00~15:35),
  `tic_scope=60`은 900행이 약 128거래일(하루 7행)을 커버. **가장 최근 거래일 하루는
  항상 그 안에 전부 포함**되므로(1분봉도 그날 09:00~15:35 전 구간이 한 페이지 안에
  다 들어옴, 실측 확인) `cont-yn`/`next-key` 연속조회 없이 1콜로 "오늘 하루치"를
  충분히 뽑을 수 있다 — 응답에서 가장 최근 날짜(첫 행 `cntr_tm[:8]`)만 필터링해서
  쓰면 된다.
  - 행 순서는 **최신이 먼저**(내림차순) — 오름차순으로 뒤집어야 프론트 캔들
    컨벤션(`CandleChart.jsx`가 기대하는 왼쪽=과거, 오른쪽=최신)과 맞는다.
  - 2026-07-21(화) 08시(개장 전) 실측: 가장 최근 날짜가 `20260720`(월)로
    나옴 — "개장 전엔 어제 마지막 거래일 분봉"이라는 예상과 일치. `20260717`
    (금)은 데이터에 아예 없었다(PLAN.md §7 "2026-07-17 관측: 그날 데이터
    없음"과 일치하는 휴장일로 추정).
- **09:00 개장 후 갱신 여부**: 이번 조사는 08시(개장 전)에 수행되어 즉시
  확인하지 못했다 — 라우터 구현 후 09:00 이후 재호출로 별도 검증 예정
  (아래 라우터 모듈의 실측 기록 참고).

## ka20005 가격 필드 100배 스케일 버그 (2026-07-21, 대시보드 지수 타일 라이브화 작업 중 발견)

`ka20005`(업종분봉차트)의 `cur_prc`/`open_pric`/`high_pric`/`low_pric`은 **개별
종목(ka10080)과 달리 index_ohlcv/네이버 fchart 대비 100배 스케일**이다 — 지수를
소수점 2자리까지 정수로 표현하는 것으로 보인다. 실측: 2026-07-21 09:00 첫 봉
`open_pric="+655388"`가 같은 순간 index_ohlcv의 코스피 오늘 시가 `6553.88`
(655388/100)과 정확히 일치. `parse_minute_chart_rows()`는 이 배율을 모른 채
절대값만 반환한다 — `MarketPage.jsx`의 지수 분봉 캔들(`fetchMarketIntraday`)은
그 안에서 상대적으로만 쓰여(캔들 모양 자체는 스케일과 무관) 지금까지 드러나지
않았지만, index_ohlcv 확정 종가와 절대값으로 직접 비교해야 하는 소비처(예:
`routers/markets.py`의 `GET /api/markets/index-tiles/live`)는 반드시 100으로
나눠야 한다 — 해당 라우터의 `_KA20005_PRICE_SCALE` 상수 참고. 코스피/코스닥 둘 다
동일하게 적용된다(개별 종목은 원화 정수값이라 이 배율이 없다).

## ka90013(종목일별프로그램매매추이요청) 실측 확정 (2026-07-26, PLAN.md §5.29)

`models.py`의 `ProgramTrade`(code, date, arb_net, non_arb_net, total_net)는
ka90013용으로 스키마만 미리 만들어 둔 채 수집기/라우터/프런트가 전혀 없이
방치돼 있었다(PLAN.md §5.29 발견). 실전 키로 `/api/dostk/mrkcond`에 직접
실호출해 URL·파라미터·응답 스키마를 아래처럼 확정했다(probe 총 호출 수
약 12건 — 절제된 수준).

- **TR id 발견 경로**: GitHub `younghwan91/kiwoom-rest-api`의
  `src/kiwoom_rest_api/domestic/market.py` — `program_trading_by_stock_day()`
  메서드가 `RESOURCE_URL="/api/dostk/mrkcond"`(ka90010/ka10063/ka10066와 같은
  카테고리) 아래 `ka90013`을 등록해 둠. `tests/integration_api_smoke.py`의
  `PARAMS["ka90013"] = {"stk_cd": STK, "date": TODAY, "amt_qty_tp": "1",
  "mrkt_tp": "P00101"}`를 그대로 실호출해 확인.
- **URL/파라미터**: `POST /api/dostk/mrkcond`, body `{"stk_cd": "005930",
  "date": "20260726", "amt_qty_tp": "1", "mrkt_tp": "P00101"}` → 200 +
  `return_code=0`. `mrkt_tp`를 `"P00101"`/`"P001_AL01"`/`"P10102"`/
  `"P101_AL02"` 4종으로 바꿔가며 호출해도, `stex_tp`를 "1"/"2"/"3"으로
  추가해도(요청 스키마엔 없지만 거부되지 않음) **전부 바이트 단위로 완전히
  동일한 응답**이 돌아왔다 — `stk_cd` 하나로 서버가 종목의 시장을 알아서
  판별하는 것으로 보이며, 두 파라미터는 사실상 무시된다(그래도 필수
  파라미터라 `program_trading_by_date`와 동일한 관례로 기본값만 채워
  보낸다). 코스닥 종목(247540 에코프로비엠)·다른 코스피 종목(000660
  SK하이닉스)에서도 동일하게 200 확인.
- **응답 스키마(핵심 발견 — 차익/비차익 분리 없음)**: `stk_daly_prm_trde_trnsn`
  배열, **한 페이지 정확히 20행 고정**(ka00198의 "항상 20행" 관찰과 동일한
  패턴), 날짜 내림차순(최신 먼저). `cont-yn`/`next-key`로 과거 페이지네이션
  가능(실측: 응답 헤더의 `next-key`를 그대로 다음 호출에 넘기면 그 다음 20
  거래일이 끊김 없이 이어서 나옴 — ka90010과 동일한 관례). 행 필드: `dt`
  (YYYYMMDD), `cur_prc`/`pred_pre`/`flu_rt`(등락 관련, 미사용), `trde_qty`
  (거래량), `prm_sell_amt`/`prm_buy_amt`(프로그램 매도/매수 금액),
  **`prm_netprps_amt`(프로그램 순매수 금액 — 이 TR이 주는 유일한 순매수
  필드)**, `prm_netprps_amt_irds`(전일대비 증감, 미사용), 수량 버전
  (`prm_sell_qty`/`prm_buy_qty`/`prm_netprps_qty`/`prm_netprps_qty_irds`,
  `amt_qty_tp="2"` 요청 시 유효할 것으로 추정 — 미검증), `base_pric_tm`/
  `dbrt_trde_rpy_sum`/`remn_rcvord_sum`(실측에서 항상 빈 문자열), `stex_tp`
  (실측에서 항상 `"KRX"`).
  **`ka90010`(시장 단위)과 달리 이 TR에는 차익(`dfrt_trde_netprps`류)/비차익
  (`ndiffpro_trde_netprps`류) 분리 필드가 없다 — `prm_netprps_amt` 하나가
  "차익+비차익 합계"인 종목 단위 프로그램 순매수 총계다.** `models.py`의
  `ProgramTrade.arb_net`/`non_arb_net`는 이 TR로는 채울 수 없으므로 수집기가
  `None`으로 남기고 `total_net`만 채운다(PLAN.md §5.29 판단: 스키마 변경
  대신 기존 컬럼에 맞춘다 — arb/non_arb 분리가 정말 필요해지면 별도 TR을
  다시 조사해야 한다).
- **단위**: `amt_qty_tp="1"`(금액) 실측값으로 검산한 결과 `ka90010`과 동일한
  **백만원** 단위다 — 예: 005930 2026-07-24 `prm_sell_amt=2445048`(=2.445조원),
  그날 종가 249,500원 × 거래량 26,175,580주 ≈ 6.5조원(총거래대금)보다 작아
  프로그램 매도 금액이 하루 거래대금의 일부라는 상식적 범위 안에 있다
  (천원 단위였다면 24.5억원으로 사실상 무의미한 규모가 돼 모순).
- **부호 인코딩 버그(ka10080의 "가격 필드 부호 접두"와는 다른 종류의 버그)**:
  `prm_netprps_amt`(및 `pred_pre`/`prm_netprps_amt_irds`/`prm_netprps_qty`류)의
  음수는 부호가 **두 번 겹쳐** 온다 — 예: `"--676861"`(=-676861),
  `"--20500"`(=-20500). 양수는 정상적으로 홑부호(`"+182002"`). 표준 `int()`는
  `"--676861"`을 그대로 넘기면 `ValueError`를 던진다 — `collectors/
  program_stock.py`의 `_parse_signed_amount`가 선행 부호 문자를 전부 읽어
  "하나라도 '-'가 있으면 음수"로 판정한 뒤 나머지 숫자에 부호를 적용하는
  방식으로 전담 처리한다(`_parse_minute_price`가 절대값만 반환하는 것과
  달리, 여기는 부호 자체가 의미 있는 값이라 보존해야 한다).

## 주문 TR(kt10000~kt10003)/계좌 TR(kt00001, ka10075) 조사 — 실호출로 미확정
(2026-07-31, PLAN.md §5.48, 실전 매매 최초 도입)

이 프로젝트가 지금까지 다룬 모든 TR은 읽기 전용 시세/수급 조회였다. 이번에
처음으로 실제 돈이 오가는 주문 제출 기능을 추가한다 — 사용자가 합의한 총
예산은 5만원이고, 최초 검증은 실거래가 아니라 체결 불가능한 지정가
주문(시세보다 훨씬 낮게 걸어 절대 체결될 수 없게 한 뒤 취소)으로 한다는
방침이다. 아래 TR 스펙은 이번 작업에서 웹서치·GitHub 조사로만 확보했고
아직 한 번도 실호출로 검증하지 않았다 — 이 파일의 다른 TR들(예:
ka90013)이 "실호출로 확정"되기 전까지 거쳐온 것과 같은 미확정 상태다. 실제
주문 전에 반드시 실호출로 재확인해야 한다.

- **TR id/URL**: 주문 4종은 `/api/dostk/ordr` 아래 `kt10000`(매수주문),
  `kt10001`(매도주문), `kt10002`(정정주문), `kt10003`(취소주문). 계좌 조회
  2종은 `/api/dostk/acnt` 아래 `kt00001`(예수금상세현황요청, 읽기전용),
  `ka10075`(미체결요청, 읽기전용 — 주문이 실제로 걸렸는지 취소 전에 확인하는
  용도). 두 URL 다 웹서치로만 확인했고 이 클라이언트로 실호출한 적 없다.
- **요청 body 필드(매수/매도)**: 서로 독립인 두 소스(실제 요청 JSON 예시가
  실린 한국어 블로그 글 하나, openapi.kiwoom.com 공식 파라미터 문서 웹서치
  하나)가 다음 형태에 동의했다:
  ```json
  {"dmst_stex_tp": "KRX", "stk_cd": "007980", "ord_qty": 1, "ord_uv": 1928, "trde_tp": "0"}
  ```
  `dmst_stex_tp`(거래소구분, "KRX"/"NXT"/"SOR" 중 안전한 기본값 "KRX"),
  `stk_cd`(종목코드), `ord_qty`(주문수량, 정수), `ord_uv`(주문단가, 정수 —
  지정가일 때만 의미), `trde_tp`(매매구분). `trde_tp` 값 자체는 두 소스
  사이에서도 완전히 일치하지 않는다 — "0"=보통(지정가)이라는 근거와
  "00"=지정가라는 근거가 둘 다 있었다(`ORDER_TRADE_TYPE_LIMIT` 상수로 빼
  둔 이유). 세 번째 소스(커뮤니티 GitHub 래퍼의 예제 스크립트, 독립 검증
  없음, 아마도 틀렸거나 희망사항)는 `acnt_no`/`acnt_prdt_cd`/`ord_dv`/
  `orig_ord_no` 같은 전혀 다른 필드명을 썼다 — 위 두 소스가 서로 독립적으로
  일치하므로 이쪽을 신뢰했지만, 실호출로 완전히 틀렸다고 확정된 것은 아니라는
  점은 남겨 둔다.
- **취소주문(kt10003)**: 원주문번호가 필요한데 정확한 JSON 키는 미확정이다
  (후보: `orig_ord_no`). `cancel_order` 메서드의 `orig_order_no` 파라미터로
  분리해 뒀으니, 실호출로 실제 키가 확인되면 메서드 본문 한 곳만 고치면 된다.
- **계좌번호 필드 미확정**: 지금까지 이 클라이언트의 어떤 TR도 요청 body나
  헤더에 계좌번호를 넣은 적이 없다(앱키/토큰으로 계좌가 암묵적으로
  결정되는 것으로 추정). 주문 TR은 다를 수도 있다(계좌번호 필드가 필수일
  가능성) — 이번 조사로는 확인하지 못했고, 이 프로젝트 `.env`에는 계좌번호를
  담는 설정 키 자체가 아직 없다. 확인되지 않은 필드를 추측해서 넣지
  않았다 — 실제 테스트에서 계좌번호가 필요하다고 드러나면 그건 이번 작업이
  아니라 후속 수정 사항이다.
- **안전장치**: `MAX_ORDER_NOTIONAL_KRW`(5만원) — 주문 1건의 notional(수량 x
  단가)이 사용자가 합의한 이 기능의 총 예산을 넘지 못하게 막는 방어선.
  `place_buy_order`/`place_sell_order`가 HTTP 호출 전에 반드시 검사한다.
  자세한 설계 의도와 알려진 한계(누적 노출은 이 체크로 못 막음)는 상수
  정의부 주석 참고.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# 실전/모의 호스트 (공식 문서 + README "환경 설정" 표, 2026-07-15 확인)
PROD_BASE_URL = "https://api.kiwoom.com"
MOCK_BASE_URL = "https://mockapi.kiwoom.com"

TOKEN_ENDPOINT = "/oauth2/token"

# TR(api-id) → 리소스 URL. 2026-07-19 실전 키로 실호출 확정(모듈 docstring
# "Phase 1.5-1 probe 실측 확정" 참고) — 전부 200 + return_code=0 확인됨.
TR_RESOURCE_URL: dict[str, str] = {
    "ka10001": "/api/dostk/stkinfo",  # 종목기본정보요청
    "ka10059": "/api/dostk/stkinfo",  # 종목별투자자기관별요청
    "ka00198": "/api/dostk/stkinfo",  # 실시간종목조회순위요청 ("실시간 관심 종목 TOP20" 카드, 2026-07-19 실호출 확정)
    "ka20001": "/api/dostk/sect",  # 업종현재가요청 (PLAN.md §3.5 breadth 선행 조건)
    "ka10051": "/api/dostk/sect",  # 업종별투자자순매수요청 (PLAN.md §1 시장 전체 수급 후보, 2026-07-19 실호출 확정)
    "ka10063": "/api/dostk/mrkcond",  # 장중투자자별매매요청 (PLAN.md §6 3.7-3, 2026-07-18 실호출 확정 — 종목별 배열, 모듈 docstring 참고)
    "ka10066": "/api/dostk/mrkcond",  # 장마감후투자자별매매요청 (PLAN.md §6 3.7-3, 2026-07-18 실호출 확정 — 종목별 배열, 모듈 docstring 참고)
    "ka90010": "/api/dostk/mrkcond",  # 프로그램매매추이요청 일자별 (PLAN.md §4.5-4, 2026-07-19 실호출 확정)
    "ka10080": "/api/dostk/chart",  # 주식분봉차트요청 (PLAN.md §5.1, 2026-07-21 실호출 확정)
    "ka20005": "/api/dostk/chart",  # 업종분봉차트요청 (PLAN.md §5.1, 2026-07-21 실호출 확정)
    "ka10065": "/api/dostk/rkinfo",  # 장중투자자별매매상위요청 (수급 상위 카드 라이브화 probe, 2026-07-21)
    "ka90009": "/api/dostk/rkinfo",  # 외국인기관매매상위요청 (수급 상위 카드 라이브화 probe, 2026-07-21)
    "ka90013": "/api/dostk/mrkcond",  # 종목일별프로그램매매추이요청 (PLAN.md §5.29, 2026-07-26 실호출 확정)
    "kt10000": "/api/dostk/ordr",  # 매수주문 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요)
    "kt10001": "/api/dostk/ordr",  # 매도주문 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요)
    "kt10002": "/api/dostk/ordr",  # 정정주문 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요, 편의 메서드 미구현)
    "kt10003": "/api/dostk/ordr",  # 취소주문 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요)
    "kt00001": "/api/dostk/acnt",  # 예수금상세현황요청 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요, 읽기전용)
    "ka10075": "/api/dostk/acnt",  # 미체결요청 (PLAN.md §5.48, 미확정 — 실호출로 확정 필요, 읽기전용)
}

# ka10080/ka20005 tic_scope 허용값 — 2026-07-21 실호출로 8개 전부 확인(모듈
# docstring "ka10080/ka20005 실측 확정" 절 참고). 라우터의 interval 쿼리파라미터
# 검증에도 재사용한다.
MINUTE_CHART_TIC_SCOPES: tuple[str, ...] = ("1", "3", "5", "10", "15", "30", "45", "60")
MINUTE_CHART_INTERVALS: frozenset[int] = frozenset(int(v) for v in MINUTE_CHART_TIC_SCOPES)

# README 실측치: TR별 지속 1 req/s(거부 0), 버스트 약 2건.
DEFAULT_RATE_LIMIT = 1.0
DEFAULT_RATE_BURST = 2

# 주문 매매구분(trde_tp) 기본값 — kt10000/kt10001 요청 body 필드. 두 독립 소스(한
# 블로그의 실제 요청 body 예시, openapi.kiwoom.com 공식 파라미터 문서 웹서치)가
# "0"=보통(지정가)에 동의했으나, 다른 자료에서는 "00"=지정가로 표기된 경우도 봤다
# (모듈 docstring "주문 TR(kt10000~kt10003) 조사" 절 참고) — 실호출로 미확정.
# 상수로 분리해 실제 응답을 본 뒤 한 곳만 고치면 되도록 해 둠.
ORDER_TRADE_TYPE_LIMIT = "0"

# 이 프로젝트에서 실제 돈이 오가는 첫 기능(PLAN.md §5.48) — 사용자가 명시적으로
# 합의한 이 기능 전체의 총 예산(5만원)을 "주문 1건"에 대한 방어선으로 그대로
# 채택한다. 목적은 버그(수량/가격 자릿수 실수, 원 단위/천원 단위 혼동, 재시도
# 루프로 인한 중복 제출 등)로 의도치 않은 실거래가 나가는 것을 막는 것 — 정상
# 시나리오를 제한하려는 게 아니라 "이 값을 넘으면 뭔가 잘못됐다"는 안전핀이다.
#
# **알려진 한계(이번 작업 범위 밖)**: 이건 "주문 1건"의 notional(ord_qty * ord_uv)만
# 검사한다. 나중에 자동매매(auto-execution)를 만들 때는 이 사람이 합의한 5만원이
# "총" 예산이라는 점을 지켜야 하므로, 그 계층은 이 주문 하나짜리 체크와는 별개로
# **누적** 노출(이미 낸 주문들의 합)을 따로 추적해야 한다 — 예를 들어 4.9만원짜리
# 주문을 10번 내면 이 체크는 매번 통과하지만 합계는 49만원이 되어 총 예산을 크게
# 넘는다. 이 상수/체크는 그 문제를 풀지 않는다 — 자동매매 설계 시 별도로 다뤄야
# 할 후속 작업으로 남겨 둔다.
MAX_ORDER_NOTIONAL_KRW = 50_000

# 토큰 만료 30분 전에 선제 재발급 (PLAN.md §5.4).
TOKEN_REFRESH_MARGIN = dt.timedelta(minutes=30)

# 토큰 캐시 파일: backend/.kiwoom_token.json (이 파일은 backend/app/clients/kiwoom.py
# 에서 parents[2] == backend/). .gitignore에 등록되어 있음(평문 토큰 포함).
DEFAULT_TOKEN_CACHE_PATH = Path(__file__).resolve().parents[2] / ".kiwoom_token.json"

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

# return_code==3은 키움 API의 범용 오류 코드다 — "인증 실패(8005 Token 무효)"뿐
# 아니라 전혀 무관한 오류(예: 존재하지 않는 종목코드)에도 재사용된다(실측:
# tests/test_kiwoom_client.py::test_return_code_error_raises_kiwoom_api_error가
# return_code=3 + "존재하지 않는 종목코드입니다"로 이미 이 사실을 검증하고
# 있었음). 따라서 return_code만으로 "토큰 무효"를 판정하면 안 되고, 실제
# 관측된 에러 메시지(`[3] 인증에 실패했습니다[8005:Token이 유효하지
# 않습니다]`)의 서브코드/키워드까지 함께 확인해야 한다 — PLAN.md §5.46
# "버그 1" 참고.
_TOKEN_INVALID_RETURN_CODE = 3


def _is_token_invalid_error(return_code: Any, return_msg: str | None) -> bool:
    """return_code==3이 "8005 Token 무효" 류의 인증 실패인지 판정.

    강제 토큰 재발급은 다른 프로세스(backend/worker)의 캐시까지 건드릴 수 있는
    비용이 있으므로(모듈 docstring/PLAN.md §5.46 근거: 한쪽의 재발급이 다른 쪽
    토큰을 서버에서 무효화시키는 것으로 추정), return_code==3인 모든 경우가
    아니라 메시지에 8005 또는 "인증...실패" 패턴이 실제로 있을 때만 재발급을
    시도한다.
    """
    if return_code != _TOKEN_INVALID_RETURN_CODE:
        return False
    msg = return_msg or ""
    return "8005" in msg or ("인증" in msg and "실패" in msg)


class KiwoomAuthError(Exception):
    """앱키/시크릿이 없거나 토큰 발급 자체가 실패했을 때."""


class KiwoomAPIError(Exception):
    """TR 호출이 `return_code != 0`으로 실패했을 때(rate limit 소진 후 포함)."""

    def __init__(self, code: Any, message: str, response: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.response = response
        super().__init__(f"[{code}] {message}")


@dataclass
class _TokenCache:
    access_token: str
    expires_at: dt.datetime  # tz-aware (UTC)
    is_mock: bool

    def is_valid(self) -> bool:
        return dt.datetime.now(dt.timezone.utc) < self.expires_at - TOKEN_REFRESH_MARGIN

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat(),
            "is_mock": self.is_mock,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "_TokenCache":
        return cls(
            access_token=data["access_token"],
            expires_at=dt.datetime.fromisoformat(data["expires_at"]),
            is_mock=data["is_mock"],
        )


class _AsyncTokenBucket:
    """Per-TR asyncio token-bucket rate limiter.

    키움의 rate limit이 TR(api_id)별 독립이라는 실측 근거(모듈 docstring 참고)에
    따라 TR마다 별도 버킷을 유지한다 — 서로 다른 TR을 섞어 호출할 때 불필요하게
    서로를 막지 않기 위함.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = float(capacity) if capacity is not None else float(rate)
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> None:
        while True:
            async with self._lock:
                now = asyncio.get_running_loop().time()
                tokens, last_refill = self._buckets.get(key, (self.capacity, now))
                tokens = min(self.capacity, tokens + (now - last_refill) * self.rate)
                if tokens >= 1:
                    self._buckets[key] = (tokens - 1, now)
                    return
                # Not enough tokens: compute wait time, release lock while sleeping.
                wait = (1 - tokens) / self.rate
                self._buckets[key] = (tokens, now)
            await asyncio.sleep(wait)


class KiwoomClient:
    """키움 REST API 비동기 클라이언트.

    Args:
        app_key, app_secret: 미지정 시 `config.get_settings()`의
            `kiwoom_app_key`/`kiwoom_app_secret` 사용.
        mock: 미지정 시 `settings.kiwoom_mock`(.env `KIWOOM_MOCK=1`) 사용.
        rate_limit / rate_burst: TR당 초당 허용 요청 수 / 버스트 크기.
            기본값은 README 실측치(1 req/s, burst 2) — PLAN.md §5.4.
        token_cache_path: 접근토큰 캐시 파일 경로. 기본값은
            `backend/.kiwoom_token.json`.
        http_client: 테스트에서 `httpx.AsyncClient(transport=MockTransport(...))`
            등을 주입하기 위한 훅. 지정하지 않으면 실제 HTTP 클라이언트를 만든다.
    """

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        mock: bool | None = None,
        rate_limit: float = DEFAULT_RATE_LIMIT,
        rate_burst: float = DEFAULT_RATE_BURST,
        token_cache_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        settings = get_settings()
        self.app_key = app_key if app_key is not None else settings.kiwoom_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kiwoom_app_secret
        self.is_mock = settings.kiwoom_mock if mock is None else mock
        self.base_url = MOCK_BASE_URL if self.is_mock else PROD_BASE_URL
        self.token_cache_path = token_cache_path or DEFAULT_TOKEN_CACHE_PATH
        self.max_retries = max_retries

        self._client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self._owns_client = http_client is None
        self._token: _TokenCache | None = None
        self._token_lock = asyncio.Lock()
        self._bucket = _AsyncTokenBucket(rate_limit, rate_burst)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "KiwoomClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- 인증 -----------------------------------------------------------

    def _require_keys(self) -> None:
        if not self.app_key or not self.app_secret:
            raise KiwoomAuthError(
                "키움 앱키/시크릿이 설정되지 않았습니다. .env의 KIWOOM_APP_KEY / "
                "KIWOOM_APP_SECRET을 채운 뒤 다시 시도하세요 "
                "(openapi.kiwoom.com에서 서비스 신청 후 발급, PLAN.md §6 Phase 0)."
            )

    def _load_cached_token(self) -> _TokenCache | None:
        if not self.token_cache_path.exists():
            return None
        try:
            data = json.loads(self.token_cache_path.read_text())
            cache = _TokenCache.from_json(data)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("키움 토큰 캐시 파일 파싱 실패, 무시하고 재발급: %s", exc)
            return None
        if cache.is_mock != self.is_mock:
            # 실전/모의 토큰을 섞어 쓰면 안 되므로 무시.
            return None
        return cache

    def _save_token_cache(self, cache: _TokenCache) -> None:
        try:
            self.token_cache_path.write_text(json.dumps(cache.to_json(), ensure_ascii=False))
        except OSError as exc:
            logger.warning("키움 토큰 캐시 파일 저장 실패(다음 요청 시 매번 재발급될 수 있음): %s", exc)

    async def _issue_token(self) -> _TokenCache:
        """POST /oauth2/token — 접근토큰발급 (au10001, 공식 문서 확인).

        만료(expires_dt)는 발급 시각 기준 24시간이 기본이지만, 서버가 돌려주는
        `expires_dt`(형식 `YYYYMMDDHHMMSS` 절대 시각으로 관측)를 우선 사용하고,
        형식이 다르거나 없으면 '지금부터 24시간'으로 보수적으로 폴백한다.
        """
        self._require_keys()
        resp = await self._client.post(
            TOKEN_ENDPOINT,
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        if resp.status_code >= 400:
            raise KiwoomAuthError(
                f"키움 토큰 발급 실패: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        return_code = data.get("return_code", 0)
        if return_code not in (0, None):
            raise KiwoomAuthError(
                f"키움 토큰 발급 실패: return_code={return_code} "
                f"return_msg={data.get('return_msg')!r}"
            )
        token = data.get("token") or data.get("access_token")
        if not token:
            raise KiwoomAuthError(f"키움 토큰 발급 응답에 token 필드가 없습니다: {data}")

        expires_at = self._parse_expires_dt(data.get("expires_dt"))
        cache = _TokenCache(access_token=token, expires_at=expires_at, is_mock=self.is_mock)
        self._save_token_cache(cache)
        logger.info(
            "키움 접근토큰 발급 완료 (%s, 만료 %s)",
            "모의" if self.is_mock else "실전",
            expires_at.isoformat(),
        )
        return cache

    @staticmethod
    def _parse_expires_dt(raw: str | None) -> dt.datetime:
        now = dt.datetime.now(dt.timezone.utc)
        if raw:
            for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    # 키움 서버 시각은 KST(UTC+9) 기준으로 관측됨.
                    parsed = dt.datetime.strptime(raw, fmt)
                    kst = parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
                    return kst.astimezone(dt.timezone.utc)
                except ValueError:
                    continue
            logger.warning("expires_dt 파싱 실패(%r), 24시간 뒤로 폴백", raw)
        return now + dt.timedelta(hours=24)

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and self._token.is_valid():
                return self._token.access_token

            cached = self._load_cached_token()
            if cached is not None and cached.is_valid():
                self._token = cached
                return cached.access_token

            self._token = await self._issue_token()
            return self._token.access_token

    async def _refresh_token(self) -> str:
        """로컬 캐시(메모리+파일)를 모두 무시하고 서버에서 새 토큰을 강제 발급.

        `_get_token()`과 달리 `self._token`/`_load_cached_token()`을 절대
        신뢰하지 않고 항상 실제 `/oauth2/token`을 호출한다 — call_tr이
        return_code==3(8005 Token 무효)을 감지했을 때 사용(PLAN.md §5.46).
        발급 결과를 `self._token`에 반영해 두므로, 호출 직후 `_get_token()`을
        다시 불러도 방금 받은 새 토큰을 그대로 돌려준다(재발급 이중 호출 방지).
        """
        async with self._token_lock:
            self._token = await self._issue_token()
            return self._token.access_token

    # -- TR 호출 ----------------------------------------------------------

    async def call_tr(
        self,
        api_id: str,
        body: dict[str, Any],
        cont_yn: str | None = None,
        next_key: str | None = None,
        resource_url: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """공통 TR 호출 래퍼.

        Returns:
            `(응답 body dict, {"cont-yn", "next-key", "api-id"} 응답 헤더 dict)`

        Raises:
            KiwoomAuthError: 앱키/시크릿 미설정 또는 토큰 발급 실패.
            KiwoomAPIError: `return_code != 0` (rate limit 소진 후 포함).
            httpx.HTTPStatusError: 429/5xx 재시도를 모두 소진한 뒤에도 실패.
        """
        self._require_keys()
        url = resource_url or TR_RESOURCE_URL.get(api_id)
        if not url:
            raise ValueError(
                f"api_id={api_id!r}의 리소스 URL을 모릅니다. resource_url을 "
                "직접 지정하거나 TR_RESOURCE_URL에 등록하세요."
            )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            token = await self._get_token()
            await self._bucket.acquire(api_id)

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": api_id,
                "cont-yn": cont_yn or "N",
                "next-key": next_key or "",
            }
            try:
                resp = await self._client.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await self._backoff(attempt)
                    continue
                raise

            # HTTP 429 또는 5xx → 지수 백오프 재시도 (PLAN.md §5.4).
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                if attempt < self.max_retries:
                    await self._backoff(attempt)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            data = resp.json()
            return_code = data.get("return_code", 0)
            # return_code == 5: "허용된 요청 개수를 초과" (rate limit, README 실측).
            # HTTP 200으로 오는 케이스도 있어 status_code만으로는 못 잡으므로 별도 처리.
            if return_code == 5 and attempt < self.max_retries:
                last_exc = KiwoomAPIError(return_code, data.get("return_msg", ""), data)
                await self._backoff(attempt)
                continue
            # return_code == 3 + "8005 Token 무효"류: 로컬 만료 계산과 무관하게
            # 서버가 토큰을 이미 무효화한 경우(PLAN.md §5.46 "버그 1" — backend/
            # worker 두 프로세스가 독립적으로 토큰을 재발급할 수 있어 한쪽 재발급이
            # 다른 쪽의 로컬 캐시를 서버 쪽에서 무효화시키는 것으로 추정). rate
            # limit과 달리 같은(무효) 토큰으로 그냥 기다렸다 재시도해봐야 똑같이
            # 실패하므로, 백오프 대신 캐시를 무시한 강제 재발급 후 즉시 재시도한다.
            if _is_token_invalid_error(return_code, data.get("return_msg")) and attempt < self.max_retries:
                last_exc = KiwoomAPIError(return_code, data.get("return_msg", ""), data)
                logger.warning(
                    "키움 토큰 인증 실패 감지(return_code=3, %r) — 캐시 무시하고 강제 재발급 후 재시도 (attempt %d/%d)",
                    data.get("return_msg", ""),
                    attempt + 1,
                    self.max_retries,
                )
                await self._refresh_token()
                continue
            if return_code not in (0, None):
                raise KiwoomAPIError(return_code, data.get("return_msg", "Unknown error"), data)

            resp_headers = {
                "cont-yn": resp.headers.get("cont-yn", "N"),
                "next-key": resp.headers.get("next-key", ""),
                "api-id": resp.headers.get("api-id", api_id),
            }
            return data, resp_headers

        # 재시도 소진.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("call_tr retry loop exited unexpectedly")  # pragma: no cover

    async def _backoff(self, attempt: int) -> None:
        delay = _RETRY_BASE_DELAY * (2**attempt)
        logger.warning("키움 API 재시도 대기 %.1fs (attempt %d)", delay, attempt + 1)
        await asyncio.sleep(delay)

    # -- 편의 메서드 --------------------------------------------------------

    async def stock_info(self, code: str) -> dict[str, Any]:
        """종목기본정보요청 (ka10001). `code`: 거래소별 종목코드(예: "005930")."""
        data, _ = await self.call_tr("ka10001", {"stk_cd": code})
        return data

    async def stock_investor_daily(
        self,
        code: str,
        date: dt.date | str | None = None,
        amt_qty_tp: str = "1",
        trde_tp: str = "0",
        unit_tp: str = "1000",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """종목별투자자기관별요청 (ka10059).

        Args:
            code: 종목코드.
            date: 조회 일자(기본값: 오늘, KST).
            amt_qty_tp: 금액수량구분 — "1"=금액, "2"=수량.
            trde_tp: 매매구분 — "0"=순매수, "1"=매수, "2"=매도.
            unit_tp: 단위구분 — "1000"=천주, "1"=단주.

        Returns:
            `(응답 body, 응답 헤더)` — 연속조회가 필요하면 헤더의 cont-yn/next-key를
            다음 호출의 cont_yn/next_key로 그대로 넘기면 된다.
        """
        if date is None:
            date_str = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
        elif isinstance(date, dt.date):
            date_str = date.strftime("%Y%m%d")
        else:
            date_str = date

        body = {
            "dt": date_str,
            "stk_cd": code,
            "amt_qty_tp": amt_qty_tp,
            "trde_tp": trde_tp,
            "unit_tp": unit_tp,
        }
        return await self.call_tr("ka10059", body, cont_yn=cont_yn, next_key=next_key)

    async def realtime_inquiry_rank(
        self,
        qry_tp: str = "1",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """실시간종목조회순위 (ka00198) — "실시간 관심 종목 TOP20" 카드 소스.

        TR id 발견 경로·실호출 검증 결과는 이 모듈 docstring의 "ka00198(실시간
        종목조회순위) 조사·실측" 절, qry_tp 재선정 근거는 같은 docstring의
        "ka00198 qry_tp 재실측"(2026-07-21) 절 참고.

        Args:
            qry_tp: 조회구분 — "1"=1분(기본값, 2026-07-21 실측으로 60초 캐시와
                가장 잘 맞는 옵션으로 재선정), "2"=10분, "3"=1시간, "4"=당일 누적
                (예전 기본값 — 실측 결과 최소 4분 넘게 값이 전혀 안 바뀌어 폐기),
                "5"=30초(너무 자주·크게 튀어 채택 안 함, 아래 절 참고).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `item_inq_rank`가 순위 배열
            (`stk_cd`/`stk_nm`/`bigd_rank`/`base_comp_chgr` 등). probe에서
            `qry_tp` 1/4/5 전부 항상 정확히 20행이었다. market/ETF 여부 필드는
            없다 — 필요하면 호출자가 `stocks` 테이블과 조인해야 한다.
        """
        body = {"qry_tp": qry_tp}
        return await self.call_tr("ka00198", body, cont_yn=cont_yn, next_key=next_key)

    async def sector_current_price(
        self,
        inds_cd: str,
        mrkt_tp: str = "0",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """업종현재가요청 (ka20001) — PLAN.md §3.5 등락 종목수(breadth) 후보 TR.

        Args:
            inds_cd: 업종코드. "001"=종합(KOSPI), "101"=종합(KOSDAQ) —
                2026-07-19 실호출로 확정(모듈 docstring 참고).
            mrkt_tp: 시장구분. 통합테스트 예시 기본값 "0".

        Returns:
            `(응답 body, 응답 헤더)`. 등락 종목수 필드는 `rising`(상승),
            `stdns`(보합), `fall`(하락), `upl`(상한), `lst`(하한) —
            2026-07-19 실호출로 확정, 네이버 breadth와 값 일치 확인됨
            (모듈 docstring 참고).
        """
        body = {"mrkt_tp": mrkt_tp, "inds_cd": inds_cd}
        return await self.call_tr("ka20001", body, cont_yn=cont_yn, next_key=next_key)

    async def sector_investor_net_buy(
        self,
        mrkt_tp: str,
        base_dt: dt.date | str,
        amt_qty_tp: str = "0",
        stex_tp: str = "3",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """업종별투자자순매수요청 (ka10051) — PLAN.md §1/§6 1-4 시장 전체 수급 소스.

        pykrx(KRX 로그인 필요)를 대체하는 코스피/코스닥 시장 전체 투자자별 순매수
        소스. 파라미터/응답 형태 및 "종합" 집계 행 위치·13개 투자자 분류 필드는
        이 모듈 docstring의 "ka10051(업종별투자자순매수) 추가 검증" 절 참고 —
        요약하면 `base_dt`로 과거 임의 일자를 1콜로 조회할 수 있고, 응답
        `inds_netprps` 배열에서 `inds_cd`가 "001_AL"(코스피) 또는 "101_AL"
        (코스닥)인 행이 시장 전체 합계다.

        Args:
            mrkt_tp: 시장구분. "0"=코스피, "1"=코스닥.
            base_dt: 조회 기준일. `dt.date` 또는 이미 포맷된 "YYYYMMDD" 문자열
                (`stock_investor_daily`의 `date` 처리와 동일한 관례).
            amt_qty_tp: 금액수량구분 — "0"=금액(기본값, 이 프로젝트의 수집 경로가
                쓰는 값). "1"=수량도 존재하는 것으로 보이나(탐색적 확인), 수집기는
                호출 수 예산(날짜당 1콜) 때문에 금액만 사용하고 net_volume은 항상
                None으로 둔다 — collectors/market_flow.py 참고.
            stex_tp: 거래소구분. 기본값 "3"(검증된 값 그대로).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `inds_netprps`가 업종별 배열.
        """
        if isinstance(base_dt, dt.date):
            base_dt_str = base_dt.strftime("%Y%m%d")
        else:
            base_dt_str = base_dt

        body = {
            "mrkt_tp": mrkt_tp,
            "amt_qty_tp": amt_qty_tp,
            "base_dt": base_dt_str,
            "stex_tp": stex_tp,
        }
        return await self.call_tr("ka10051", body, cont_yn=cont_yn, next_key=next_key)

    async def intraday_investor_trading(
        self,
        mrkt_tp: str = "000",
        invsr: str = "6",
        amt_qty_tp: str = "1",
        frgn_all: str = "1",
        smtm_netprps_tp: str = "1",
        stex_tp: str = "3",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """장중투자자별매매요청 (ka10063) — PLAN.md §6 Phase 3.7-3.

        **주의(2026-07-18 실호출로 확인)**: 이 TR은 시장 전체 순매수 1행이 아니라
        **종목별** 배열(`opmr_invsr_trde`)을 준다 — `invsr`이 선택한 투자자
        카테고리 한 종류가 그날 거래한 종목만 나열되고(코스피 기준 6~800종목,
        `invsr` 값에 따라 들쭉날쭉), 시장 합계 행은 없다. "시장 전체 잠정
        순매수"가 필요하면 이 메서드로 전 종목을 페이지네이션(cont-yn/next-key)
        해 직접 합산하거나(비용 큼), `sector_investor_net_buy`(ka10051)를
        `base_dt=오늘`로 호출하는 쪽을 쓴다 — 자세한 근거는 이 모듈 docstring의
        "ka10063/ka10066 장중 잠정 수급 probe" 절 참고. `invsr` 숫자 코드(0~9)가
        정확히 어느 투자자 분류에 대응하는지는 공식 문서로 확인하지 못했다
        (기본값 "6"은 probe에서 800종목을 반환한 값 — 개인 또는 외국인처럼
        커버리지가 넓은 카테고리로 추정되나 확정 아님).

        Args:
            mrkt_tp: 시장구분. "000"=전체, "001"=코스피, "101"=코스닥
                (ka10051의 "0"/"1" 코드 체계와 다르니 섞어 쓰지 말 것).
            invsr: 투자자구분. 0~9 실호출 확인(의미 미확정, 위 주의 참고).
            amt_qty_tp: 금액수량구분 — "1"=금액(GitHub PARAMS 기본값).
            frgn_all: 외국인전체 포함 여부로 추정(미확정) — 기본값 "1".
            smtm_netprps_tp: 동시순매수구분으로 추정(미확정) — 기본값 "1".
            stex_tp: 거래소구분 — 기본값 "3"(ka10051과 동일 관례).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `opmr_invsr_trde`가 종목별 배열.
        """
        body = {
            "mrkt_tp": mrkt_tp,
            "amt_qty_tp": amt_qty_tp,
            "invsr": invsr,
            "frgn_all": frgn_all,
            "smtm_netprps_tp": smtm_netprps_tp,
            "stex_tp": stex_tp,
        }
        return await self.call_tr("ka10063", body, cont_yn=cont_yn, next_key=next_key)

    async def after_hours_investor_trading(
        self,
        mrkt_tp: str = "000",
        amt_qty_tp: str = "1",
        trde_tp: str = "0",
        stex_tp: str = "3",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """장마감후투자자별매매요청 (ka10066) — PLAN.md §6 Phase 3.7-3.

        ka10063과 같은 카테고리(`/api/dostk/mrkcond`)이지만 `invsr` 파라미터 없이
        **종목별로 13개 투자자 카테고리 전부**(`ind_invsr`/`frgnr_invsr`/`orgn`/
        `fnnc_invt`/`insrnc`/`invtrt`/`etc_fnnc`/`bank`/`penfnd_etc`/`samo_fund`/
        `natn`/`etc_corp`)를 한 행에 준다 — 역시 시장 합계 행은 없고 전 종목을
        코드순으로 나열한다(코스피 실측 1,330종목, 100행/페이지). 전 종목
        페이지네이션 합산이 `ka10051`(base_dt=오늘) 종합 행과 오차 0.1% 이내로
        일치함을 교차검증했다(단위는 이 TR이 100배 작음 — ka10051은 백만원,
        이 TR의 amt_qty_tp="1"은 만원) — 자세한 근거는 모듈 docstring의
        "ka10063/ka10066 장중 잠정 수급 probe" 절 참고.

        Args:
            mrkt_tp: 시장구분. "000"=전체, "001"=코스피, "101"=코스닥.
            amt_qty_tp: 금액수량구분 — "1"=금액(GitHub PARAMS 기본값, 단위는
                위 주의 참고).
            trde_tp: 매매구분으로 추정(미확정) — 기본값 "0".
            stex_tp: 거래소구분 — 기본값 "3".

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `opaf_invsr_trde`가 종목별 배열.
        """
        body = {"mrkt_tp": mrkt_tp, "amt_qty_tp": amt_qty_tp, "trde_tp": trde_tp, "stex_tp": stex_tp}
        return await self.call_tr("ka10066", body, cont_yn=cont_yn, next_key=next_key)

    async def program_trading_by_date(
        self,
        mrkt_tp: str,
        date: dt.date | str,
        amt_qty_tp: str = "1",
        min_tic_tp: str = "1",
        stex_tp: str = "3",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """프로그램매매추이요청 일자별 (ka90010) — PLAN.md §4.5-4 차익/비차익 프로그램매매.

        실호출 검증 결과는 이 모듈 docstring의 "ka90010(프로그램매매추이요청
        일자별) 실측" 절 참고. 핵심 요약:

        - 응답은 종목이 아니라 **날짜별 배열**(`prm_trde_trnsn`)이다 — 한 번
          호출로 여러 거래일치(요청일 기준 과거로 다건, 실측 약 20건/페이지)가
          한꺼번에 오고, `cont-yn`/`next-key`로 더 과거까지 연속조회할 수 있다.
          즉 "일자별 추이"라는 이름 그대로 하루 1콜이 아니라 **페이지당 다건
          시계열**이라 백필 호출 수가 날짜 수보다 훨씬 적게 든다.
        - 각 행의 `cntr_tm`은 `YYYYMMDDHHmmss`이지만 시분초는 항상
          `000000`이라 사실상 날짜 문자열이다(`cntr_tm[:8]`로 날짜만 뽑아
          쓰면 됨).
        - 차익거래 순매수: `dfrt_trde_netprps` (부호 포함 문자열, 단위는
          `amt_qty_tp`에 따라 백만원(금액) 또는 천주(수량)).
          비차익거래 순매수: `ndiffpro_trde_netprps`. 그 외 매수/매도
          개별 값, 전체 순매수(`all_netprps`), 참고용 `kospi200`/`basis`도
          같이 온다(이 프로젝트는 순매수만 macro_series에 적재).

        Args:
            mrkt_tp: 시장구분 — 코스피/코스닥별로 거래소 커버리지에 따라 세
                가지 코드가 있다: KRX만("P00101"/"P10102"), NXT만
                ("P001_NX01"/"P101_NX02"), KRX+NXT 통합
                ("P001_AL01"/"P001_AL02"). 이 프로젝트는 시장 전체 값을
                원하므로 통합 코드(`P001_AL01`=코스피, `P001_AL02`=코스닥)를
                쓴다(collectors/program_flow.py 참고) — `stex_tp="3"`(통합)과
                짝이 맞는 조합.
            date: 조회 기준일. 이 값 이전(포함) 과거 시계열이 반환된다.
            amt_qty_tp: 금액수량구분 — "1"=금액(백만원, 기본값), "2"=수량(천주).
            min_tic_tp: 분틱구분 — "0"=틱, "1"=분(기본값, GitHub 통합테스트
                기본값과 동일). 응답이 일자별이라 이 값이 결과에 영향을 주는
                것을 실측으로 확인하지 못했다(문서상 필수 파라미터라 값만
                채워 보냄).
            stex_tp: 거래소구분 — "1"=KRX, "2"=NXT, "3"=통합(기본값).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `prm_trde_trnsn`이 날짜별 배열
            (최신순으로 추정). 연속조회가 필요하면 응답 헤더의 cont-yn/next-key를
            다음 호출에 그대로 넘긴다.
        """
        if isinstance(date, dt.date):
            date_str = date.strftime("%Y%m%d")
        else:
            date_str = date

        body = {
            "date": date_str,
            "amt_qty_tp": amt_qty_tp,
            "mrkt_tp": mrkt_tp,
            "min_tic_tp": min_tic_tp,
            "stex_tp": stex_tp,
        }
        return await self.call_tr("ka90010", body, cont_yn=cont_yn, next_key=next_key)

    async def stock_program_trading(
        self,
        code: str,
        date: dt.date | str | None = None,
        amt_qty_tp: str = "1",
        mrkt_tp: str = "P00101",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """종목일별프로그램매매추이요청 (ka90013) — PLAN.md §5.29 종목별 프로그램매매.

        실호출 검증 결과는 이 모듈 docstring의 "ka90013(종목일별프로그램매매추이요청)
        실측 확정" 절 참고. 핵심 요약:

        - 응답은 `stk_daly_prm_trde_trnsn`(날짜별 배열, 한 페이지 정확히 20행,
          최신순) — `cont-yn`/`next-key`로 과거까지 연속조회 가능(ka90010과
          동일한 관례).
        - `dfrt_trde_netprps`(차익)/`ndiffpro_trde_netprps`(비차익) 같은 분리
          필드가 **없다** — `prm_netprps_amt`(부호 포함 문자열, 단, 음수는
          `"--123"`처럼 부호가 두 번 겹치는 버그가 있다 — 아래 Args 참고) 하나가
          "차익+비차익 합계"인 종목 단위 프로그램 순매수 총계다.
        - `mrkt_tp`/`stex_tp`는 실측상 결과에 아무 영향이 없었다(4종/3종 조합
          전부 바이트 단위로 동일한 응답) — 필수 파라미터라 값만 채워 보낸다.

        Args:
            code: 종목코드(예: "005930").
            date: 조회 기준일. 기본값은 오늘(KST) — 이 값 이전(포함) 과거
                시계열이 반환된다.
            amt_qty_tp: 금액수량구분 — "1"=금액(백만원, 기본값 — 실측으로 ka90010과
                동일 단위 확인). "2"=수량은 이 프로젝트에서 검증하지 않았다.
            mrkt_tp: 시장구분. 실측상 결과에 영향 없음(위 요약 참고) — 기본값
                "P00101"(GitHub 통합테스트 예시 그대로).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `stk_daly_prm_trde_trnsn`이
            날짜별 배열. 파싱은 `collectors/program_stock.py`의
            `parse_program_trade_rows`가 전담한다(부호 이중 인코딩 처리 포함).
        """
        if date is None:
            date_str = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
        elif isinstance(date, dt.date):
            date_str = date.strftime("%Y%m%d")
        else:
            date_str = date

        body = {"stk_cd": code, "date": date_str, "amt_qty_tp": amt_qty_tp, "mrkt_tp": mrkt_tp}
        return await self.call_tr("ka90013", body, cont_yn=cont_yn, next_key=next_key)

    async def stock_minute_chart(
        self,
        code: str,
        tic_scope: str,
        upd_stkpc_tp: str = "1",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """주식분봉차트요청 (ka10080) — PLAN.md §5.1 종목 분봉.

        Args:
            code: 종목코드(예: "005930").
            tic_scope: 분 단위. 실호출로 확인된 허용값은 `MINUTE_CHART_TIC_SCOPES`
                (`"1"`/`"3"`/`"5"`/`"10"`/`"15"`/`"30"`/`"45"`/`"60"`) — 모듈
                docstring "ka10080/ka20005 실측 확정" 절 참고.
            upd_stkpc_tp: 수정주가구분 — "1"=수정주가 반영(GitHub PARAMS 예시 기본값).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `stk_min_pole_chart_qry`가
            분봉 배열(최신 순, 여러 거래일 섞여 있음). `parse_minute_chart_rows`로
            "오늘"(최신 날짜) 하루치만 오름차순으로 뽑아 쓴다.
        """
        body = {"stk_cd": code, "tic_scope": tic_scope, "upd_stkpc_tp": upd_stkpc_tp}
        return await self.call_tr("ka10080", body, cont_yn=cont_yn, next_key=next_key)

    async def sector_minute_chart(
        self,
        inds_cd: str,
        tic_scope: str,
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """업종분봉차트요청 (ka20005) — PLAN.md §5.1 지수(코스피/코스닥) 분봉.

        Args:
            inds_cd: 업종코드. "001"=종합(KOSPI), "101"=종합(KOSDAQ) — ka20001/
                ka10051과 동일한 코드 체계.
            tic_scope: 분 단위. `stock_minute_chart`와 동일(`MINUTE_CHART_TIC_SCOPES`).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `inds_min_pole_qry`가 분봉 배열
            (ka10080과 필드 구조 동일, 모듈 docstring 참고).
        """
        body = {"inds_cd": inds_cd, "tic_scope": tic_scope}
        return await self.call_tr("ka20005", body, cont_yn=cont_yn, next_key=next_key)

    async def foreign_institution_trading_top(
        self,
        mrkt_tp: str = "000",
        amt_qty_tp: str = "0",
        stex_tp: str = "3",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """외국인기관매매상위요청 (ka90009) — "수급 상위" 카드 라이브화 **시도했으나
        미채택**(2026-07-21 실호출, routers/flow_rank.py 모듈 docstring "키움
        TR(ka10065/ka90009) 대체 재검토" 절 참고). ka10063/ka10066과 같은 관례로
        "탐색했으나 부적합" 근거를 코드에 남겨두기 위해 메서드는 그대로 둔다 —
        어떤 live 라우터도 이 메서드를 호출하지 않는다.

        네이버 sise_deal_rank_iframe(2영업일 이상 지연)을 대체하는 라이브 후보로
        ka10065(장중투자자별매매상위요청)와 함께 검토했으나, ka10065는
        `opmr_invsr_trde_upper` 응답이 **수량만** 주고 정렬 기준도 금액이 아니라
        회전율/수량 쪽으로 보여(예: 실호출 상위 1위가 시가총액 작은 흥아해운 —
        "외국인 순매수 상위"라는 카드 의미와 어긋남) 채택하지 않았다. ka90009는
        `frgnr_orgn_trde_upper` 응답 한 번으로 **외국인 순매도/순매수·기관
        순매도/순매수 4개 랭킹을 병렬 컬럼**으로 준다(각 컬럼은 그 컬럼 기준
        내림차순 정렬 — 실호출로 `for_netprps_amt`가 42264 -> 35624 -> 11619 ->
        10768 -> 8438로 단조감소함을 확인). 필드/설계만 보면 기존 flow-rank의
        investor(foreign/institution) x side(buy/sell) 2x2 토글에 정확히
        대응된다: foreign+buy=`for_netprps_*`, foreign+sell=`for_netslmt_*`,
        institution+buy=`orgn_netprps_*`, institution+sell=`orgn_netslmt_*`
        (institution 쪽 `*_stk_cd`는 `"000660_AL"`처럼 `_AL` 접미사가 붙어 온다 —
        실제 종목코드를 쓰려면 접미사를 잘라내야 한다, ka10051의 `"001_AL"`
        종합행 코드와 유사한 관례로 추정).

        **그러나 장중 갱신 실측에서 탈락시켰다**: 2026-07-21 09:44:59~09:51:00
        KST(90초 간격 5회, 총 6분+) 동일 파라미터로 반복 호출했지만 상위
        3종목(SK하이닉스 42264/삼성전자 35624/KODEX 200 11619, 백만원)이
        **바이트 단위로 완전히 동일**했다 — 같은 시간대 `GET /api/markets/
        attention`(ka00198)은 75초 간격 재호출에서 상위 3종목 등락률이 실제로
        바뀌었으므로(장이 멈춰서가 아니라는 대조군 확인), ka90009 자체가
        장중 실시간 갱신을 하지 않는 것으로 판정했다. `date` 파라미터도
        오늘/어제/생략 세 경우 응답이 바이트 단위로 완전히 동일해(무시됨) —
        이 TR은 "현재 시각 실시간 스냅샷"이 아니라 훨씬 드물게(또는 일 1회만)
        갱신되는 소스로 추정된다(임의 과거일 조회도 안 됨 — EOD 대체도 아니고
        진짜 라이브도 아닌 애매한 소스).

        Args:
            mrkt_tp: 시장구분 — "000"=전체(GitHub PARAMS 예시 기본값).
            amt_qty_tp: 금액수량구분 — "0"=금액(백만원, 기본값 — 이 프로젝트가
                쓰는 값), "1"=수량으로 추정(미확인).
            stex_tp: 거래소구분 — 기본값 "3"(다른 TR과 동일 관례).

        Returns:
            `(응답 body, 응답 헤더)` — 응답 body의 `frgnr_orgn_trde_upper`가
            25행 배열, 각 행에 8개 종목(4개 랭킹 x 종목코드/명/금액/수량)이
            나란히 들어있다(위 컬럼 대응 참고).
        """
        body = {
            "mrkt_tp": mrkt_tp,
            "amt_qty_tp": amt_qty_tp,
            "qry_dt_tp": "0",
            "date": dt.date.today().strftime("%Y%m%d"),
            "stex_tp": stex_tp,
        }
        return await self.call_tr("ka90009", body, cont_yn=cont_yn, next_key=next_key)

    # -- 주문 (실거래, PLAN.md §5.48 — 실호출로 미확정) ----------------------------

    async def place_buy_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        exchange: str = "KRX",
        order_type: str = ORDER_TRADE_TYPE_LIMIT,
    ) -> dict[str, Any]:
        """매수주문 (kt10000) — **실호출로 미확정**(모듈 docstring "주문 TR
        (kt10000~kt10003)/계좌 TR 조사" 절 참고). 이 클라이언트가 처음으로
        실제 돈이 오가는 주문을 내는 메서드다.

        **안전장치(우회 불가)**: HTTP 호출 전에 `quantity * price`(notional)를
        계산해 `MAX_ORDER_NOTIONAL_KRW`(5만원, 사용자가 합의한 이 기능 전체의
        총 예산)를 넘으면 `call_tr`을 아예 부르지 않고 `ValueError`를 던진다.
        이 검사는 메서드 내부에 있으므로, 같은 TR을 `call_tr("kt10000", ...)`
        으로 밖에서 직접 호출해 우회하는 경우에만 뚫린다 — 즉 주문은 항상 이
        메서드(또는 `place_sell_order`)를 거쳐야 하고, 새로운 호출 경로를
        추가할 때도 이 캡을 다시 구현하지 말고 반드시 이 메서드를 재사용할 것.

        매도(`place_sell_order`)에는 원래 "의도치 않은 지출"이라는 이유가
        적용되지 않지만(자산을 팔 뿐 돈을 쓰는 게 아님), 이번 최초 버전에서는
        단순함과 대칭성을 위해 매도에도 동일한 캡을 건다 — 큰 수량을 잘못
        입력하는 버그를 막는 효과는 있고, 성숙 단계에서 완화할 수 있다.

        Args:
            stock_code: 종목코드(예: "005930").
            quantity: 주문수량(`ord_qty`, 정수).
            price: 주문단가(`ord_uv`, 정수, 원). 지정가일 때만 의미가 있다.
            exchange: 거래소구분(`dmst_stex_tp`) — "KRX"/"NXT"/"SOR" 중
                안전한 기본값 "KRX"(2026-07-31 조사 근거, 실호출 미확정).
            order_type: 매매구분(`trde_tp`) — 기본값 `ORDER_TRADE_TYPE_LIMIT`
                ("0", 보통/지정가로 추정). 실호출로 값이 다르다고 밝혀지면
                이 상수 하나만 고치면 된다(모듈 docstring 참고).

        Returns:
            `call_tr`이 반환한 응답 body dict(주문번호 등 — 실제 필드는
            실호출 전까지 미확정).

        Raises:
            ValueError: notional이 `MAX_ORDER_NOTIONAL_KRW`를 초과할 때
                (HTTP 호출 전에 발생 — 실제 주문이 나가지 않았음이 보장됨).
        """
        notional = quantity * price
        if notional > MAX_ORDER_NOTIONAL_KRW:
            raise ValueError(
                f"매수 주문 notional {notional:,}원(수량 {quantity} x 단가 "
                f"{price:,}원)이 안전 한도 {MAX_ORDER_NOTIONAL_KRW:,}원을 "
                "초과합니다 — 이 프로젝트가 실거래에 배정한 총 예산을 지키기 "
                "위한 하드 캡입니다(모듈 상수 MAX_ORDER_NOTIONAL_KRW 정의부 "
                "주석 참고). 수량/단가 단위를 다시 확인하세요."
            )
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "ord_qty": quantity,
            "ord_uv": price,
            "trde_tp": order_type,
        }
        data, _ = await self.call_tr("kt10000", body)
        return data

    async def place_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        exchange: str = "KRX",
        order_type: str = ORDER_TRADE_TYPE_LIMIT,
    ) -> dict[str, Any]:
        """매도주문 (kt10001) — **실호출로 미확정**(요청 body 형태는
        `place_buy_order`와 동일, 모듈 docstring "주문 TR" 절 참고).

        `place_buy_order`와 동일한 `MAX_ORDER_NOTIONAL_KRW` notional 캡을
        건다 — 매도는 원래 "돈을 쓴다"는 의미의 캡이 필요 없지만(자산을 팔 뿐),
        이번 최초 버전은 단순함/대칭성을 위해 매수와 동일한 방어선을 그대로
        적용하는 의도적 단순화다(잘못된 대량 수량 매도를 막는 부수 효과는
        있음). 기능이 성숙하면 완화될 수 있다.

        Args/Returns/Raises: `place_buy_order` 참고(캡 초과 시 HTTP 호출 전에
        `ValueError`).
        """
        notional = quantity * price
        if notional > MAX_ORDER_NOTIONAL_KRW:
            raise ValueError(
                f"매도 주문 notional {notional:,}원(수량 {quantity} x 단가 "
                f"{price:,}원)이 안전 한도 {MAX_ORDER_NOTIONAL_KRW:,}원을 "
                "초과합니다 — 매수와 동일한 하드 캡을 매도에도 적용한다(모듈 "
                "상수 MAX_ORDER_NOTIONAL_KRW 정의부 주석 참고). 수량/단가 "
                "단위를 다시 확인하세요."
            )
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "ord_qty": quantity,
            "ord_uv": price,
            "trde_tp": order_type,
        }
        data, _ = await self.call_tr("kt10001", body)
        return data

    async def cancel_order(
        self,
        stock_code: str,
        orig_order_no: str,
        quantity: int,
        exchange: str = "KRX",
    ) -> dict[str, Any]:
        """취소주문 (kt10003) — **실호출로 미확정**. 자금 노출을 새로 만들지
        않는(오히려 없애는) 동작이라 `MAX_ORDER_NOTIONAL_KRW` 캡은 걸지 않는다.

        원주문번호의 정확한 요청 JSON 키는 미확정이다(후보: `orig_ord_no` —
        모듈 docstring "주문 TR" 절 참고). 실호출로 실제 키가 다르다고
        밝혀지면 아래 `body` dict 한 곳만 고치면 되도록 `orig_order_no`를
        별도 named parameter로 분리해 뒀다.

        Args:
            stock_code: 종목코드.
            orig_order_no: 취소 대상 원주문번호(주문 시 응답으로 받은 값).
            quantity: 취소수량.
            exchange: 거래소구분 — 기본값 "KRX"(주문 메서드들과 동일 관례).

        Returns:
            `call_tr`이 반환한 응답 body dict.
        """
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "orig_ord_no": orig_order_no,
            "ord_qty": quantity,
        }
        data, _ = await self.call_tr("kt10003", body)
        return data

    async def get_deposit_detail(self) -> dict[str, Any]:
        """예수금상세현황요청 (kt00001) — **읽기전용**, 실호출로 미확정.

        주문을 전혀 내지 않는 조회 TR이라 완전히 안전하다 — 실제 첫 주문
        전에 잔고/증거금을 확인하는 용도로 쓴다. 요청 파라미터가 정확히
        무엇을 요구하는지 실호출로 확인하지 못해 일단 빈 body로 호출한다
        (다른 TR 중 파라미터 없이 호출하는 기존 선례가 없어, 실호출 결과
        400/파라미터 누락 오류가 나면 이 메서드를 조정해야 한다).

        Returns:
            `call_tr`이 반환한 응답 body dict.
        """
        data, _ = await self.call_tr("kt00001", {})
        return data

    async def get_unfilled_orders(self) -> dict[str, Any]:
        """미체결요청 (ka10075) — **읽기전용**, 실호출로 미확정.

        `place_buy_order`/`place_sell_order`로 낸 주문이 실제로 서버에
        걸렸는지 취소 전에 확인하는 용도(비체결 지정가 주문 검증 절차).
        `get_deposit_detail`과 마찬가지로 파라미터 요구사항이 미확정이라
        일단 빈 body로 호출한다.

        Returns:
            `call_tr`이 반환한 응답 body dict.
        """
        data, _ = await self.call_tr("ka10075", {})
        return data


# -- ka10080/ka20005 공통 분봉 응답 파싱 -------------------------------------------

MINUTE_CHART_ROWS_KEY: dict[str, str] = {
    "ka10080": "stk_min_pole_chart_qry",
    "ka20005": "inds_min_pole_qry",
}


def _parse_minute_price(raw: Any) -> int | None:
    """ka10080/ka20005 가격 필드의 부호 접두 파싱(모듈 docstring "가격 필드 부호
    인코딩 주의" 절 참고) — 선행 `+`/`-` 문자를 전부 방향 표시로 소비하고 절대값을
    반환한다(가격 자체는 항상 양수)."""
    if raw is None:
        return None
    text = str(raw).strip()
    i = 0
    while i < len(text) and text[i] in "+-":
        i += 1
    digits = text[i:]
    if not digits:
        return None
    try:
        return abs(int(digits))
    except ValueError:
        return None


# ka20005(업종분봉차트) 전용 가격 스케일 보정 — 모듈 docstring "ka20005 가격 필드
# 100배 스케일 버그" 절 참고. ka10080(개별 종목 분봉)은 이 배율이 없다(정상).
# 대시보드 지수 타일 라이브화 작업(2026-07-21) 중 발견됐는데, 처음엔 그 작업의
# 라우터 안에서만 국소적으로(÷100) 보정했다가 시장 탭의 기존 분봉 차트(같은
# 공용 파서를 쓰는 `/api/markets/{market}/intraday`)는 그대로 100배로 남아있던
# 걸 뒤늦게 발견해 여기 공용 파서로 옮겼다 — 소비자가 몇 곳이든 한 곳만 고치면
# 되도록.
_KA20005_PRICE_SCALE = 100


def parse_minute_chart_rows(data: dict[str, Any], api_id: str) -> list[dict[str, Any]]:
    """ka10080/ka20005 공통 응답(body) -> "오늘"(최신 날짜) 하루치 분봉,
    오름차순(과거->최신)으로 정렬해 반환한다.

    한 콜 응답에는 여러 거래일치가 섞여 있어(모듈 docstring "하루 커버리지" 절)
    가장 최근 날짜(`cntr_tm[:8]`의 최댓값)만 남긴다. 원본 행 순서는 최신이
    먼저(내림차순)라 프론트 캔들 컨벤션(왼쪽=과거)에 맞춰 뒤집는다.

    ka20005는 가격 필드가 실제 지수값의 100배로 온다(모듈 docstring 참고) —
    api_id가 ka20005이면 여기서 ÷100 보정까지 마치고 반환한다(ka10080은 그대로).

    Returns ``[{"date": "YYYYMMDD", "time": "HHMM", "timestamp": iso8601(+09:00),
    "open", "high", "low", "close", "volume"}, ...]`` — 빈 응답이면 빈 리스트.
    """
    rows_key = MINUTE_CHART_ROWS_KEY.get(api_id)
    raw_rows = (data.get(rows_key) if rows_key else None) or []
    if not raw_rows:
        return []

    latest_date = max(r["cntr_tm"][:8] for r in raw_rows if r.get("cntr_tm"))
    today_rows = [r for r in raw_rows if (r.get("cntr_tm") or "").startswith(latest_date)]
    today_rows.sort(key=lambda r: r["cntr_tm"])

    def _price(raw: Any) -> float | int | None:
        value = _parse_minute_price(raw)
        if value is None:
            return None
        if api_id == "ka20005":
            return round(value / _KA20005_PRICE_SCALE, 2)
        return value

    out: list[dict[str, Any]] = []
    for r in today_rows:
        cntr_tm = r["cntr_tm"]
        time_str = cntr_tm[8:12]
        iso_ts = (
            f"{cntr_tm[0:4]}-{cntr_tm[4:6]}-{cntr_tm[6:8]}"
            f"T{cntr_tm[8:10]}:{cntr_tm[10:12]}:00+09:00"
        )
        try:
            volume = int(r.get("trde_qty") or 0)
        except (TypeError, ValueError):
            volume = 0
        out.append(
            {
                "date": latest_date,
                "time": time_str,
                "timestamp": iso_ts,
                "open": _price(r.get("open_pric")),
                "high": _price(r.get("high_pric")),
                "low": _price(r.get("low_pric")),
                "close": _price(r.get("cur_prc")),
                "volume": volume,
            }
        )
    return out
