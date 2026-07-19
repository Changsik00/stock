# 코스피 · 코스닥 · 선물 시세 대시보드

한국거래소(KRX) 공식 Open API(https://openapi.krx.co.kr) 기준으로 코스피/코스닥 지수와
코스피200 선물의 일별 종가·거래량·거래대금을 보여주는 웹 대시보드입니다.

## 현재 상태 / 알려진 제약

- **개인/기관/외인 투자자별 수급 데이터는 이 API에 없습니다.** KRX Open API의
  파생상품(drv)·지수(idx) 카테고리는 전부 "일별매매정보"(시세/거래량)만 제공하고
  투자자 유형별 분류는 제공하지 않습니다(직접 확인함). 수급 데이터가 꼭 필요하면
  한국투자증권 등 증권사 Open API(코스피/코스닥만 지원, 실계좌 필요) 또는
  `data.krx.co.kr` 로그인 크롤링(ToS 회색지대) 중 하나를 추가로 붙여야 합니다.
- `.env`의 `KRX_OPENAPI_KEY`는 발급은 됐지만, 실제 호출하려면 openapi.krx.co.kr
  마이페이지에서 아래 3개 데이터셋에 대해 **개별 이용 승인**을 받아야 합니다:
  - 지수 > KOSPI 시리즈 일별시세정보 (`idx/kospi_dd_trd`)
  - 지수 > KOSDAQ 시리즈 일별시세정보 (`idx/kosdaq_dd_trd`)
  - 파생상품 > 선물 일별매매정보 (`drv/fut_bydd_trd`)
  - 승인 전에는 `401 Unauthorized API Call`이 반환되며, 백엔드가 이를 502로
    감싸서 프런트에 안내 메시지로 보여줍니다.

## 실행

### Docker Compose (권장 — 상시 서비스)

DB(TimescaleDB)·백엔드·프런트 3개 컨테이너를 한 번에 띄웁니다. 사용자 세션에
종속된 임시 프로세스가 아니라 `docker compose up -d`로 백그라운드(detached)
기동하므로, 터미널/에이전트 세션이 끝나도 계속 떠 있습니다(DB 컨테이너가
이미 이 방식으로 상시 운영되던 것과 동일한 패턴을 백엔드/프런트에도 적용).

```bash
docker compose build
docker compose up -d

# 확인
docker compose ps                 # 3개 컨테이너 healthy/running
docker compose logs -f backend    # 60초 간격 live-refresh 워밍 로그 등
```

- 백엔드: http://localhost:8123 (`ENABLE_SCHEDULER=1` 평일 18:00 일별 배치,
  `ENABLE_LIVE_REFRESH=1` 장중 60초 간격 breadth/live·flow/live·attention
  캐시 선제 워밍 — 둘 다 컨테이너에서는 기본 켜짐)
- 프런트: http://localhost:5173 (`/api`는 컨테이너 안에서 `backend:8123`으로 프록시)
- `./backend:/app`, `./frontend:/app` 바인드 마운트로 코드 수정 시 핫리로드(uvicorn
  `--reload`, vite HMR)가 그대로 동작합니다 — 이미지를 다시 빌드할 필요는 의존성
  변경(requirements.txt/package.json) 때만 있습니다.
- 컨테이너 재빌드가 필요할 때: `docker compose build backend` 또는
  `docker compose up -d --build`
- 중지: `docker compose down` (볼륨 `pgdata`는 남아 DB 데이터가 보존됩니다)

### 도커 없이 로컬 개발 (대안)

```bash
# 1) 백엔드
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8123 --reload

# 2) 프런트엔드 (다른 터미널)
cd frontend
npm install
npm run dev   # http://localhost:5173, /api 는 8123으로 프록시됨
```

이 경우 DB는 `docker compose up -d db`로 DB 컨테이너만 띄우거나 별도로 준비해야
합니다. `vite.config.js`의 `/api` 프록시 대상은 `VITE_API_PROXY_TARGET` 환경변수로
바꿀 수 있고, 미설정 시 기존처럼 `http://127.0.0.1:8123`을 씁니다.

`.env`는 저장소 루트에 있고 `KRX_OPENAPI_KEY`를 담고 있습니다 (git에는 커밋되지 않음).
Docker Compose는 `env_file: .env`로 이 파일을 그대로 참조하며, `docker-compose.yml`
자체에는 시크릿을 하드코딩하지 않습니다(단 `DATABASE_URL`은 컨테이너 내부 네트워크
주소로 `environment`에서 명시 오버라이드합니다 — `.env`의 `localhost:5433` 값은 호스트에서
직접 접속할 때만 씁니다).

## 승인 후 확인

승인이 완료되면 실제 응답 필드명이 `app/services.py`에서 가정한 것과 다를 수 있습니다
(`IDX_NM`/`CLSPRC_IDX`/`ACC_TRDVOL` 등은 공개 문서 기반 추정치). 아래 스모크 테스트로
원본 JSON을 먼저 찍어보고 필요하면 `services.py`의 필드명을 맞춰주세요:

```bash
cd backend
python -m scripts.smoke_test          # 어제 영업일 기준
python -m scripts.smoke_test 20260710 # 특정 날짜 기준
```

## 구조

```
backend/app/krx_client.py   KRX Open API 호출 (AUTH_KEY 헤더 인증)
backend/app/services.py     일별 스냅샷을 시계열로 조립 (지수명/선물 종목 필터링)
backend/app/main.py         FastAPI, GET /api/series?market=kospi|kosdaq|futures&days=N
frontend/src/               React + recharts 대시보드
```
