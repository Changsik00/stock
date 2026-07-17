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
| 시장 전체(코스피/코스닥) 투자자별 순매수 **일별 시계열 전용 TR** | `ka10051`(종합 업종) 일자별 반복 호출로 우회 가능하나 비효율. KIS `FHPTJ04040000`이 정석 |
| 환율/유가 | ECOS / yfinance (§3) |

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
| USD/KRW 일별 | **한국은행 ECOS API** — `731Y001` / 주기 `D` / 항목 `0000001` (매매기준율). 무료, 키 신청 1일 내, 기간 조회 한 번에 가능. **실호출 검증됨** | 한국수출입은행 API, FRED `DEXKOUS` |
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
  상승/상한/보합/하락/하한 종목수를 받는 방식이 정석. 구 OpenAPI+ opt20001에 해당
  필드가 있었고 REST가 구 TR을 미러링하므로 있을 가능성 높음 — **probe 실호출로 확정 필요**
- 실패 시 대안: 일별은 pykrx 전 종목 스냅샷에서 카운트, 장중은 네이버 시장 페이지 파싱
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
- [ ] ECOS 인증키 신청 (ecos.bok.or.kr, 무료, ~1일) → `.env`의 `ECOS_API_KEY` — **환율 백필 선행 조건**
- [ ] **data.krx.co.kr 무료 회원가입** → `.env`의 `KRX_ID`/`KRX_PW` — 2026-02 KRX 포털
  개편 이후 pykrx가 이 로그인 없이는 전면 차단됨(구현 중 실확인). **시장 수급 백필 선행 조건**
- [ ] (선물 수급용) KIS 계좌 + 앱키 — Phase 4 전까지만 결정하면 됨

### Phase 1 — 기반 골격 + 매크로 (API 키 없이도 개발·검증 가능한 것부터)

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 1-1 ✅ | 인프라 골격 | docker-compose(timescaledb), config.py, db.py, models.py(§5.2 전체), Alembic 마이그레이션, 라우터 뼈대 | `docker compose up` 후 `alembic upgrade head` 성공, `GET /api/admin/status` 200 |
| 1-2 ✅* | 매크로 수집 | ecos.py + commodities.py(yfinance→FRED 폴백) + collectors/macro.py + backfill(3년) | ECOS sample 키로 환율 10건, FRED로 WTI/브렌트 3년치 DB 적재 확인 |
| 1-3 ✅ | 매크로 화면 | `GET /api/macro/series` + MacroPage + PeriodPicker | 브라우저에서 환율/유가 3개 라인차트 렌더 |
| 1-4 ✅* | 시장 수급 수집 | pykrx 기반 market_flow collector (키 불필요, KIS 발급 전 임시 소스) + 3년 backfill | 코스피/코스닥 13분류 일별 순매수 DB 적재 |
| 1-5 ✅ | 시장 화면 개편 | `GET /api/markets/{market}/series` + MarketPage (지수 라인 + 수급 스택 막대 + 누적 라인) | 기존 KRX 시세와 수급이 한 화면에 |

*✅\* = 코드 완료, 백필 실행은 키 대기 (1-2: ECOS_API_KEY 환율분, 1-4: KRX_ID/PW)*

### Phase 1.5 — 시장 체력 지표 (2026-07-17 추가) ★ 키움 앱키 재발급 대기로 블로킹

| # | 작업 | 내용 | 완료 기준 |
|---|---|---|---|
| 1.5-1 ⚠️ | 키움 probe 실측 | `scripts/kiwoom_probe.py` 실행 — TR URL 확정, rate limit 실측, **ka20001 응답에 등락 종목수 필드 존재 확정** (§3.5). **블로킹**: 2026-07-17 실행 결과 토큰 발급이 실전/모의 양쪽 다 `8001` 인증 실패로 막힘 — TR URL·rate limit·ka20001 필드 전부 미검증. 코드는 준비됨(`ka20001` 호출·`step_d` 덤프 단계 추가 완료, 정적분석 근거 URL 반영) — **앱키 재발급 후 재실행만 하면 됨** | 실측 결과를 kiwoom.py 주석/TR_RESOURCE_URL에 반영, 문서화 — *URL/rate limit/필드 확정은 미완료, 앱키 재발급 대기* |
| 1.5-2 ✅ | KOFIA 수집 | clients/kofia.py + macro 배치 편입 + 3년 backfill — 예탁금·신용융자·**대차잔고** | macro_series에 kofia 시리즈 3년치 적재, collect_log ok |
| 1.5-3 | breadth 수집·API | market_breadth 테이블(마이그레이션) + collectors/breadth.py(일별) + `/breadth`·`/breadth/live` | 일별 등락 종목수 적재 + 장중 live 호출 동작 |
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
| pykrx 안정성 | 2026-02 개편 후 **data.krx.co.kr 무료 회원 로그인 필수**(KRX_ID/PW 없으면 HTTP 400 전면 차단 — 구현 중 실확인). 무인증 크롤링 시대는 끝남 | 무료 가입으로 당장은 사용 가능하나, KIS `FHPTJ04040000`으로의 1차 소스 교체(Phase 4 → 조기 검토)를 권장 |
| yfinance 429 | 2024~2025 rate limit 사태 반복 | 하루 1회 배치 + FRED 백업 자동 전환 |
| KOFIA freesis 파싱 | 비공식 통계 화면 POST 파싱 — 사이트 개편 시 장애 가능 | collect_log 실패 감지, 일별 T+1 지표라 하루 지연 허용 가능 |
| ka20001 등락 종목수 | REST 응답에 구 opt20001의 상승/하락 종목수 필드가 있는지 미확정 | 1.5-1 probe로 실측 확정, 없으면 pykrx 카운트(일별)/네이버 파싱(장중) 대안 |
| 두바이유 일별 | 무료 공식 API 없음 | WTI/브렌트만 우선, 두바이는 월별 or 오피넷 파싱 |
| 수급 데이터 시점 | 확정치는 장마감 후 | 장중에는 잠정치(`ka10063`)임을 UI에 명시 |
| 지수 시세 소스 | KRX Open API(`idx/kospi_dd_trd` 등)가 **403 Forbidden**(서비스 이용 승인 미비, 2026-07 확인)이라 `/api/markets/{market}/series`가 라이브 500을 반환. `index_ohlcv`를 배치로 채워 라우터는 DB만 읽도록 전환(collectors/ohlcv.py) — 코스피/코스닥은 yfinance(`^KS11`/`^KQ11`) 1차 + 네이버 fchart(`fchart.stock.naver.com/siseJson.naver`, 비공식) 폴백, 코스피200선물(k200_futures)은 yfinance에 심볼이 없어 네이버 fchart(symbol=FUT)만 사용. 두 소스 모두 거래대금(원화 금액)을 제공하지 않아 `index_ohlcv.value`는 당분간 NULL(거래대금 차트는 0으로 표시) | 임시 조치 — 추후 키움 차트 TR(OHLCV+거래대금)로 교체 예정. KRX Open API 승인이 나면 되돌릴 수 있도록 `krx_client.py`/`services.get_index_series`·`get_futures_series`는 그대로 보존 |
