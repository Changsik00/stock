# 프론트엔드 디자인 가이드 (look & feel 유지용)

시각 언어: 플랫, 1px 얇은 테두리, 999px 필(칩·탭·버튼), 12~13px 작은 폰트, 그림자·그라데이션 없음.
색은 의미가 있을 때만 쓴다. 모든 색은 `src/index.css`의 변수로만 참조한다(하드코딩 금지).

## 토큰 (라이트 / 다크는 `:root`, `prefers-color-scheme`, `[data-theme]` 세 블록에 동일 이름으로 정의)

| 토큰 | 용도 |
|---|---|
| `--page` / `--surface` | 페이지 배경 / 카드·모달 배경 |
| `--text-primary` / `--text-secondary` / `--text-muted` | 본문 / 보조 / 비활성·캡션 |
| `--border`, `--grid`, `--axis` | 카드 테두리 / 격자·눌림 배경 / 컨트롤 외곽선 |
| `--chip-bg` | 칩·hover·active 배경 |
| `--series-price`, `--series-value` | 가격·거래대금 시리즈, 포커스 링, 활성 탭 밑줄 |
| `--up`, `--down` | 가격 방향 전용: 상승/매수=빨강, 하락/매도=파랑 (한국 관행) |
| `--warn-text` | 배경 위 주황 경고 글자(변동성 고조, danger hover) |
| `--badge-*-bg/fg` | 분류 배지(코스피/코스닥/ETF/선물/live/warn/info) |
| `--risk-*` | KRX 규정 임계 경보 배너 전용 |
| `--btn-primary-bg/-hover/-fg` | 주 동작 버튼 (라이트 #1f66bf+흰 글자, 다크 #3987e5+검정 글자) |

## 컴포넌트 사용 규칙

- **버튼 `.btn`** (필, 36px / `.btn-sm` 32px). 한 영역에 `.btn-primary`는 하나만.
  - `.btn-primary`: 화면의 주 동작(승인, 저장, 추가).
  - `.btn-secondary`(기본): 보조 동작(수정, 수정 후 승인, 뉴스 수집).
  - `.btn-ghost`: 취소, 더보기 등 저강도 동작.
  - `.btn-danger`: 삭제, 무시 등 되돌리기 어려운 동작. 평소엔 중립, hover 때만 `--warn-text` 주황. 빨강/파랑 금지.
  - `.btn-link`: 펼치기/접기 같은 텍스트 링크형.
- **필터·토글·탭**: `.toggle-chip`, `.tab`, `.range`, `.page-tab`. 선택 상태가 있는 컨트롤은 버튼이 아니라 이것들을 쓴다.
- **배지 `.badge-*`**: 읽기 전용 상태 표시. 클릭 동작은 버튼으로.
- **카드/타일**: `--surface` + `1px solid var(--border)` + 8~10px 라운드. 클릭 가능한 KPI는 `.kpi-tile`.

## 색 의미 규칙

`--up/--down`은 가격 방향 전용(버튼·분류·상태 표시에 쓰지 않는다). 주황(`--warn-text`, `badge-warn`)은 주의/경고. 회색은 중립. 글자를 투명도로 흐리게 하지 말고 `--text-secondary/-muted` 토큰을 쓴다.

## 새 UI 추가 시

1. 기존 클래스를 먼저 찾고, 없을 때만 새로 만든다. 인라인 style 색 지정 금지.
2. `<button>`에는 항상 `type`과 `.btn` 계열 클래스를 붙인다(기본 브라우저 버튼 금지).
3. 터치 타깃 높이 32px 이상, 키보드 `:focus-visible` 링 확인.
4. 라이트·다크 둘 다 본다: 글자 대비 4.5:1 이상(큰 글자 3:1), disabled 상태, hover/active, 빨강/파랑이 가격 외 의미로 쓰이지 않았는지.
5. 새 토큰은 세 테마 블록에 모두 추가한다.
