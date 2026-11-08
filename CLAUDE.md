# 저장소 규칙 (모든 에이전트 공통, 최소본)

## UI (frontend/)
- UI 작업 전 `frontend/DESIGN.md`를 먼저 읽고 따른다. 토큰(`src/index.css` 변수)·버튼 시스템(`.btn-*`)·색 의미 규칙 준수: `--up/--down`은 가격 방향 전용, 주황=경고, 회색=중립.
- 변경 후 라이트/다크 × 390px/1280px 스크린샷(Playwright)으로 직접 확인한다. 확인하지 못했으면 못 했다고 보고한다.

## 실거래 안전
- 자동매매 킬스위치(`AutoTradeState.enabled`)와 실거래 규칙(`backend/app/quant/auto_trade_rules.py` 등)은 사용자의 명시 요청 없이 변경·해제하지 않는다.
- 일부 테스트가 킬스위치 가드로 실행 거부되는 것은 정상이다. 테스트를 위해 킬스위치를 끄지 않는다.

## 컨테이너·환경
- worker 컨테이너는 핫리로드가 없다. 수정 후 `docker restart stock-worker-1`.
- `.env` 변경은 컨테이너 재생성이 필요하다. `.env`의 키는 출력·커밋하지 않는다.
- backend 포트는 8123.

## 데이터 단위
- DB 금액 단위는 백만원(market_flow 포함; 2026-11 ×100 보정 이력은 `PLAN.md` 참고). 화면 표기는 억/조.

## 문서·코드 관례
- 새 기능은 `PLAN.md`에 Phase로 기록한다(기존 관례 유지). 코드 주석은 한국어.
- git commit/push는 사용자가 요청할 때만 한다.
