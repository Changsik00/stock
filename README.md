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

`.env`는 저장소 루트에 있고 `KRX_OPENAPI_KEY`를 담고 있습니다 (git에는 커밋되지 않음).

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
