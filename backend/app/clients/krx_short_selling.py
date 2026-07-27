"""공매도(short selling) 시장 전체·종목별 — KRX 정보데이터시스템 공매도 통계
포털(data.krx.co.kr, PLAN.md §5.32).

## 조사 배경

경쟁 서비스 벤치마킹 결과 키움/삼성증권/네이버페이 증권/KRX 오픈데이터 어디에나
있는 공매도 거래량/거래대금/비중·잔고 데이터가 이 프로젝트엔 없었다. 이 프로젝트가
이미 가진 "대차잔고"(``clients/kofia.py``, KOFIA freesis, 시장 전체 합계 단일
시계열)는 공매도의 전조 지표(빌린 주식 잔고)일 뿐 실제 공매도 거래/잔고와는
다른 데이터라 — 완전히 새로 조사했다.

## 실측 경과 (2026-07-27)

### 1. 네이버 — 종목 상세 PC 페이지의 "공매도현황" 탭

``finance.naver.com/item/main.naver?code=005930``의 탭 목록(HTML `<li>` 실측)에
`<a href="/item/short_trade.naver?code=005930" ...><span>공매도현황</span></a>`이
있다. 그 페이지(``finance.naver.com/item/short_trade.naver?code=005930``, EUC-KR
인코딩)를 열어보면 자체 데이터가 없고 **KRX 화면을 그대로 iframe으로 삽입**한다::

    <iframe src="https://data.krx.co.kr/comm/srt/srtLoader/index.cmd?screenId=MDCSTAT300&isuCd=005930"
            width="681" height="1237" title="종목별 공매도 종합 현황"></iframe>

즉 네이버 자체는 데이터를 갖고 있지 않고 KRX 화면을 그대로 보여줄 뿐이다 — 네이버가
아니라 이 iframe이 가리키는 KRX 정보데이터시스템이 진짜 소스다. 이 클라이언트는
그 소스(KRX)를 네이버를 거치지 않고 직접 호출한다.

### 2. 네이버 — 시장 전체(코스피/코스닥) 공매도 페이지

``finance.naver.com/sise/`` 메인 페이지를 전량 fetch해 href를 확인한 결과
"공매도" 관련 시장 전체 통계 페이지/링크가 없다(등락/거래상위/업종/테마
섹션만 있고 공매도 섹션 자체가 없음, 페이지 전체 grep 결과 "공매도" 문자열
1건 — 무관한 문맥). **네이버는 시장 전체 공매도 페이지를 제공하지 않는다.**

### 3. KRX 정보데이터시스템(data.krx.co.kr) — 공매도 통계 포털

위 iframe URL(``/comm/srt/srtLoader/index.cmd?screenId=MDCSTAT300``)을 직접
GET하면 완전한 HTML 페이지가 돌아온다(로그인 없이, 200) — 이게 이 프로젝트가
PLAN.md §7에서 이미 403으로 막혀 있다고 기록한 "KRX Open API"(공식 REST,
승인 필요)나, §7 pykrx 리스크 행이 언급한 "2026-02 개편 후 로그인 필수가 된
data.krx.co.kr 무료회원 다운로드"(``/comm/bldAttendant/...`` 계열 중
``MDC/MDI/mdiLoader``로 진입하는 일반 통계 화면)와 **다른, 별개의 서비스**임을
아래처럼 실측으로 직접 확인했다:

- 메인 메뉴 트리(``data.krx.co.kr/contents/MDC/MAIN/main/index.cmd``, 588KB
  실측 fetch)에서 공매도 카테고리(menuId=MDC0203 "공매도 통계") 하위 항목을
  전부 grep으로 뽑아보면: 개별종목 공매도 종합정보(screen-no 31001),
  개별종목 공매도 거래(32001), 개별업종 공매도 거래(32002), 투자자별 공매도
  거래(32003), 공매도 거래 상위 50종목(32004), 시장별 공매도 예외거래(32005),
  개별종목 공매도 순보유잔고(33001), 개별업종 공매도 순보유잔고(33002),
  공매도 순보유잔고 대량보유자(33003), 공매도 순보유잔고 상위 50종목(33004).
- 이 항목들을 **일반 메뉴 경로**(``/contents/MDC/MDI/mdiLoader/index.cmd?menuId=...``)로
  열면 전부 "로그인 또는 회원가입이 필요합니다" alert + 로그인 페이지 리다이렉트가
  뜬다(실측: menuId=MDC02030203/04/05 등) — 이게 §7이 이미 기록한 로그인 장벽이다.
- 하지만 네이버 iframe이 실제로 쓰는 **``/comm/srt/srtLoader/index.cmd?screenId=...``
  경로**(공매도 전용 별도 로더)는 로그인 없이 200으로 열린다 — screenId를
  MDCSTAT280~330 범위로 스캔한 결과 실제로 존재하는 페이지는 정확히
  **MDCSTAT300(개별종목 종합정보)/301(개별종목 거래)/302(개별업종 거래)/
  305(개별종목 순보유잔고)/306(개별업종 순보유잔고)** 5개뿐이고, 투자자별/
  상위50/예외거래/대량보유자 계열은 이 무료 경로에 없다(로그인이 필요한
  일반 메뉴로만 존재하는 것으로 보임 — 이번 스코프에선 사용하지 않는다).
- 화면 렌더 데이터는 ``POST /comm/bldAttendant/getJsonData.cmd``
  (``bld=dbms/MDC_OUT/STAT/srt/MDCSTAT30001_OUT`` 등, 화면 소스의
  ``mdc.module.setModule`` 코드에서 그대로 읽음)에서 온다 — pykrx 등이 흔히
  쓰는 "OTP 발급(``generate.cmd``) → 다운로드(``download.cmd``)" 2단계는
  **필요 없다**(그건 화면의 "다운로드" 버튼 전용 플로우이고, 화면 그리드
  자체는 ``getJsonData.cmd``를 세션/쿠키/OTP 없이 콜드 POST로 직접 호출해
  200을 받는다 — KOFIA freesis(``clients/kofia.py``)와 같은 "무인증 콜드
  POST" 패턴). **단, ``Referer`` 헤더는 필수**다 — 이게 없으면 정상 데이터
  대신 별도 안내 HTML 페이지가 돌아온다(실측 확인, 아래 ``_REFERER_*``
  상수가 각 화면의 실제 URL을 그대로 넣는 이유).

**결론(실호출로 확정)**: KRX 정보데이터시스템의 "공매도 통계" 서브포털은
일반 통계 포털(로그인 필요, §7 리스크)과 별개의 인증 불필요 서비스다. 이
클라이언트는 이 서브포털만 쓴다.

## 종목별 — MDCSTAT30001_OUT (개별종목 공매도 종합정보)

화면 헤더(실측 HTML)가 명시하는 필드 매핑::

    TRD_DD                  일자
    CVSRTSELL_TRDVOL         공매도 거래량(전체, 주)
    UPTICKRULE_APPL_TRDVOL   업틱룰적용 거래량(주)
    UPTICKRULE_EXCPT_TRDVOL  업틱룰예외 거래량(주)
    STR_CONST_VAL1           순보유잔고수량(주) — T+2 지연, 보고의무 미달 시 "-"
    CVSRTSELL_TRDVAL         공매도 거래대금(전체, 원)
    UPTICKRULE_APPL_TRDVAL   업틱룰적용 거래대금(원)
    UPTICKRULE_EXCPT_TRDVAL  업틱룰예외 거래대금(원)
    STR_CONST_VAL2           순보유잔고금액(원) — T+2 지연, 보고의무 미달 시 "-"

파라미터 ``isuCd``는 6자리 종목코드가 아니라 **12자리 ISIN**(예:
"KR7005930003")이어야 한다(6자리 코드로 호출하면 빈 배열이 조용히 옴 — 에러가
아니라서 최초엔 놓치기 쉬웠다, 실측 확인). ISIN은 별도 finder API
(``bld=dbms/comm/finder/get_srtisu``, GET, 파라미터 ``isuCd=<6자리 코드>``)로
얻는다 — 실측: ``isuCd=005930`` → ``{"output":[{"tbox":"005930/삼성전자",
"code":"KR7005930003","codeNm":"삼성전자","marketCode":"STK"}]}``. 이 클라이언트는
6자리 코드를 받아 내부적으로 이 finder를 먼저 불러 ISIN으로 변환한다(모듈
전역 딕셔너리에 캐싱 — 코드-ISIN 매핑은 상장폐지/재상장 외엔 바뀌지 않는다).

**단위 확정**: 화면의 단위 선택 셀렉트박스 기본값이 공유(``share``)=1("주"),
금액(``money``)=1("원") — 이 클라이언트는 그 기본값 그대로 요청해 raw 정수를
받는다. 이 프로젝트의 금액 컬럼 관례(models.py 모듈 docstring, "단위: 백만 원")에
맞춰 ``value``/``balance_value``는 저장 직전 ÷1,000,000 변환까지 마쳐서
반환한다(호출자가 다시 변환할 필요 없음) — ``volume``/``balance_qty``(주)는
변환 없이 그대로.

**실측 예시(2026-07-27, 005930 삼성전자, 2026-07-01~07-27)**::

    {"TRD_DD":"2026/07/24","CVSRTSELL_TRDVOL":"793,057","STR_CONST_VAL1":"-",
     "CVSRTSELL_TRDVAL":"203,400,170,750","STR_CONST_VAL2":"-"}
    {"TRD_DD":"2026/07/22","CVSRTSELL_TRDVOL":"726,133","STR_CONST_VAL1":"1,464,868",
     "CVSRTSELL_TRDVAL":"196,540,713,250","STR_CONST_VAL2":"381,598,114,000"}

가장 최근 2거래일(07/23·07/24)은 ``STR_CONST_VAL1/2``가 "-"이고 07/22부터는
채워져 있다 — 화면 각주("공매도 순보유잔고 정보는 ... 보고의무 발생일(T)로부터
2일째 되는 날(T+2)까지 보고하므로, 공매도잔고 정보는 당일 기준으로 2일전
내역까지 확인할 수 있습니다")와 정확히 일치하는 T+2 지연이다 — 파싱 버그가
아니라 소스 자체의 구조적 지연이므로 ``None``으로 그대로 둔다(억지로 채우지
않음, §5 "정직한 표시" 원칙).

## 시장 전체(코스피/코스닥) — MDCSTAT30201_OUT (개별업종 공매도 거래, "업종" 대신
"시장 대표지수"를 조회해 재활용)

KRX 공매도 통계 포털엔 "시장 전체" 전용 화면이 무료 경로에 없지만(위 조사
결과 §5.32-1 참고), **개별업종 공매도 거래(MDCSTAT302) 화면이 업종분류 옵션
중 "대표"(indAggClssCd=001)를 선택하면 그 시장 자신을 가리키는 지수(코스피
자신=idxIndCd 001 "코스피", 코스닥 자신=idxIndCd 001 "코스닥")를 고를 수 있다**
— 즉 "업종별" 화면의 최상위 옵션이 사실상 "시장 전체"와 같다(실측:
``executeForResourceBundle.cmd``로 옵션 목록 조회 — indTpCd=1(코스피) +
indAggClssCd=001 → ``[{"value":"001","name":"코스피"},{"value":"028","name":
"코스피 200"}]``, indTpCd=2(코스닥) + indAggClssCd=001 → ``[{"value":"001",
"name":"코스닥"}, ...]``). 이 클라이언트는 이 조합(idxIndCd="001")으로 시장
전체 공매도 거래 현황을 얻는다 — 새 화면을 찾은 게 아니라 기존 "업종별" 화면을
"업종 대신 시장 자신"으로 조회하는 재활용이다(``naver_value_rank.py``가
"거래량 상위" API를 "거래대금 상위" 용도로 재활용한 것과 같은 성격의 판단).

화면 헤더 필드 매핑(개별종목판과 달리 시장 전체 거래량/거래대금·비중까지
같이 나온다)::

    TRD_DD                   일자
    CVSRTSELL_TRDVOL          공매도 거래량(전체, 주)
    UPTICKRULE_APPL_TRDVOL    업틱룰적용 거래량(주)
    UPTICKRULE_EXCPT_TRDVOL   업틱룰예외 거래량(주)
    ACC_TRDVAL/ACC_TRDVOL     시장 전체 거래대금(원)/거래량(주) — 공매도가 아닌 전체
    TRDVOL_WT                 공매도 거래량 비중(%) = CVSRTSELL_TRDVOL/ACC_TRDVOL*100
    CVSRTSELL_TRDVAL          공매도 거래대금(전체, 원)
    TRDVAL_WT                 공매도 거래대금 비중(%) = CVSRTSELL_TRDVAL/ACC_TRDVAL*100

**실측 예시(2026-07-27, 코스피, 2026-07-01~07-27)**::

    {"TRD_DD":"2026/07/24","CVSRTSELL_TRDVOL":"17,728,078","ACC_TRDVOL":"438,777,893",
     "TRDVOL_WT":"4.04","CVSRTSELL_TRDVAL":"1,708,956,089,320",
     "ACC_TRDVAL":"40,282,837,389,194","TRDVAL_WT":"4.24"}

**과거 데이터로 교차검증(중요)**: 2025-01 구간을 조회하면 코스피/코스닥/개별
종목 전부 ``UPTICKRULE_APPL_TRDVOL=0``이고 공매도가 전부 "업틱룰예외"로만
잡힌다 — 이는 실제로 2023-11 전면 금지 이후 2025-03-31 재개(코스피200·
코스닥150 구성종목만, 업틱룰 예외 없이는 사실상 거래 자체가 없었던 시기)라는
알려진 사실과 정확히 일치한다. 이 데이터가 실제 시장 상황을 그대로 반영하는
진짜 소스임을 강하게 뒷받침한다.

**날짜 범위 제약(실측)**: 약 2년(730일) 안팎까지는 한 번에 조회되지만
(2024-07-27~2026-07-27, 483행 정상), 3.5년(2023-01-01~2026-07-27)을 한 번에
요청하면 HTTP 400이 온다 — 정확한 상한은 확인하지 않았고(수집기가 필요로
하는 범위보다 훨씬 넉넉함), 수집기는 self-healing 목적의 짧은 최근 구간만
요청하므로 문제되지 않는다.

**세션/쿠키 불필요, Referer 필수** — KOFIA(``clients/kofia.py``)와 동일한
무인증 콜드 POST 패턴이지만, Referer 헤더가 없으면 정상 데이터 대신 접근
안내 페이지가 온다(실측 확인) — 그래서 이 클라이언트는 항상 각 화면의 실제
URL을 ``Referer``로 채워 보낸다.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://data.krx.co.kr"
DATA_ENDPOINT = f"{BASE_URL}/comm/bldAttendant/getJsonData.cmd"

_REFERER_STOCK = f"{BASE_URL}/comm/srt/srtLoader/index.cmd?screenId=MDCSTAT300"
_REFERER_MARKET = f"{BASE_URL}/comm/srt/srtLoader/index.cmd?screenId=MDCSTAT302"

_BLD_STOCK_COMPREHENSIVE = "dbms/MDC_OUT/STAT/srt/MDCSTAT30001_OUT"
_BLD_MARKET_BY_INDEX = "dbms/MDC_OUT/STAT/srt/MDCSTAT30201_OUT"
_BLD_FINDER_ISIN = "dbms/comm/finder/get_srtisu"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# market -> (mktTpCd, idxIndCd) — indAggClssCd="001"(대표 분류)에서 그 시장
# 자신을 가리키는 지수 코드가 항상 "001"이다(코스피 자신="코스피", 코스닥
# 자신="코스닥" — 모듈 docstring "시장 전체" 절의 실측 결과).
_MARKET_PARAMS = {
    "kospi": {"mktTpCd": "1", "idxIndCd": "001"},
    "kosdaq": {"mktTpCd": "2", "idxIndCd": "001"},
}

_WON_TO_MILLION = 1_000_000  # 1,000,000원 = 1백만원

# 6자리 종목코드 -> 12자리 ISIN 캐시 (상장폐지/재상장 외엔 바뀌지 않는 정적 매핑).
_ISIN_CACHE: dict[str, str] = {}


class KrxShortSellingError(Exception):
    """Raised when the KRX short-selling endpoints return an unparsable/empty payload."""


def _parse_int(raw: object) -> int | None:
    """"793,057"/"0"/"-"(값 없음) 형태를 정수로 변환한다. "-"는 T+2 지연으로
    아직 보고되지 않았거나 보고의무 미달인 경우(모듈 docstring 참고) — None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        logger.warning("krx_short_selling: 숫자 필드 파싱 실패, None 처리: %r", raw)
        return None


def _parse_won_to_million(raw: object) -> int | None:
    value = _parse_int(raw)
    if value is None:
        return None
    return round(value / _WON_TO_MILLION)


def _parse_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        logger.warning("krx_short_selling: 실수 필드 파싱 실패, None 처리: %r", raw)
        return None


def _parse_trd_dd(raw: object) -> dt.date | None:
    """"2026/07/24" -> date(2026, 7, 24)."""
    if not isinstance(raw, str):
        return None
    try:
        return dt.datetime.strptime(raw, "%Y/%m/%d").date()
    except ValueError:
        return None


def resolve_isin(code: str, timeout: int = 15) -> str:
    """6자리 종목코드 -> 12자리 ISIN (finder API, 모듈 docstring "종목별" 절 참고).
    결과는 프로세스 메모리에 캐싱한다. 존재하지 않는 코드는 KrxShortSellingError."""
    cached = _ISIN_CACHE.get(code)
    if cached is not None:
        return cached

    resp = requests.get(
        DATA_ENDPOINT,
        params={"bld": _BLD_FINDER_ISIN, "isuCd": code, "locale": "ko_KR"},
        headers={"User-Agent": USER_AGENT, "Referer": _REFERER_STOCK},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    output = data.get("output") or []
    if not output or not output[0].get("code"):
        raise KrxShortSellingError(f"ISIN을 찾을 수 없음: code={code!r}, response={data!r}")

    isin = output[0]["code"]
    _ISIN_CACHE[code] = isin
    return isin


def fetch_stock_short_selling(
    code: str, start: dt.date, end: dt.date, timeout: int = 15
) -> list[dict]:
    """종목 code의 [start, end] 공매도 거래·순보유잔고 일별 시계열(MDCSTAT30001_OUT).

    Returns ``[{"date": dt.date, "volume": int|None, "value": int|None(백만원),
    "balance_qty": int|None, "balance_value": int|None(백만원)}, ...]`` (날짜
    오름차순). ``balance_*``는 보고의무 발생 종목만 채워지고(T+2 지연 포함),
    그 외엔 None(모듈 docstring 참고 — 억지로 채우지 않음).
    """
    isin = resolve_isin(code, timeout=timeout)

    resp = requests.post(
        DATA_ENDPOINT,
        data={
            "bld": _BLD_STOCK_COMPREHENSIVE,
            "locale": "ko_KR",
            "isuCd": isin,
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
            "share": "1",
            "money": "1",
        },
        headers={"User-Agent": USER_AGENT, "Referer": _REFERER_STOCK},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("OutBlock_1")
    if rows is None:
        raise KrxShortSellingError(f"unexpected KRX response shape for {code}: {list(data.keys())}")

    out: list[dict] = []
    for row in rows:
        row_date = _parse_trd_dd(row.get("TRD_DD"))
        if row_date is None:
            continue
        out.append(
            {
                "date": row_date,
                "volume": _parse_int(row.get("CVSRTSELL_TRDVOL")),
                "value": _parse_won_to_million(row.get("CVSRTSELL_TRDVAL")),
                "balance_qty": _parse_int(row.get("STR_CONST_VAL1")),
                "balance_value": _parse_won_to_million(row.get("STR_CONST_VAL2")),
            }
        )
    out.sort(key=lambda r: r["date"])
    return out


def fetch_market_short_selling(
    market: str, start: dt.date, end: dt.date, timeout: int = 15
) -> list[dict]:
    """market(kospi/kosdaq)의 [start, end] 시장 전체 공매도 거래 일별 시계열
    (MDCSTAT30201_OUT, "업종별" 화면을 시장 자신으로 재활용 — 모듈 docstring
    "시장 전체" 절 참고).

    Returns ``[{"date": dt.date, "short_volume": int|None, "short_value":
    int|None(백만원), "total_volume": int|None, "total_value": int|None(백만원),
    "volume_ratio": float|None(%), "value_ratio": float|None(%)}, ...]`` (날짜
    오름차순).
    """
    params = _MARKET_PARAMS.get(market)
    if params is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(_MARKET_PARAMS)}")

    resp = requests.post(
        DATA_ENDPOINT,
        data={
            "bld": _BLD_MARKET_BY_INDEX,
            "locale": "ko_KR",
            "mktTpCd": params["mktTpCd"],
            "indAggClssCd": "001",  # "대표" 분류 — 시장 자신을 가리키는 지수만 있음
            "idxIndCd": params["idxIndCd"],
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
            "share": "1",
            "money": "1",
        },
        headers={"User-Agent": USER_AGENT, "Referer": _REFERER_MARKET},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("OutBlock_1")
    if rows is None:
        raise KrxShortSellingError(f"unexpected KRX response shape for {market}: {list(data.keys())}")

    out: list[dict] = []
    for row in rows:
        row_date = _parse_trd_dd(row.get("TRD_DD"))
        if row_date is None:
            continue
        out.append(
            {
                "date": row_date,
                "short_volume": _parse_int(row.get("CVSRTSELL_TRDVOL")),
                "short_value": _parse_won_to_million(row.get("CVSRTSELL_TRDVAL")),
                "total_volume": _parse_int(row.get("ACC_TRDVOL")),
                "total_value": _parse_won_to_million(row.get("ACC_TRDVAL")),
                "volume_ratio": _parse_float(row.get("TRDVOL_WT")),
                "value_ratio": _parse_float(row.get("TRDVAL_WT")),
            }
        )
    out.sort(key=lambda r: r["date"])
    return out
