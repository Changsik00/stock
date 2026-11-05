"""뉴스 헤드라인 수집 클라이언트 (PLAN.md §5.90-2) — 인증 불필요한 공개 소스만.

## 채택 소스 (2026-11-05 컨테이너 실호출로 확인)

- 한국: 네이버 증권 주요뉴스 ``https://api.stock.naver.com/news/mainnews?pageSize=N``
  (JSON 배열: ``tit`` 제목, ``subcontent`` 스니펫, ``ohnm`` 언론사, ``oid``/``aid`` 기사 ID,
  ``dt`` YYYYMMDDHHMMSS(KST)). 기존 ``finance.naver.com/news/*``는 SPA로 바뀌었고
  ``m.stock.naver.com/api/news/*``(종목별 제외)는 404라서 이 API를 쓴다.
  기사 URL은 ``https://n.news.naver.com/mnews/article/{oid}/{aid}``.
- 미국/글로벌 RSS: CNBC(Top News, Finance), MarketWatch(Top Stories, MarketPulse),
  WSJ Markets, BBC Business — 모두 200 + 파싱 확인.
- 불채택: Yahoo Finance RSS(404/429), Reuters RSS(폐지).

소스별 실패는 격리한다 — ``fetch_all_news``는 소스마다 독립 시도하고 실패 소스는
``errors``에 담아 돌려줄 뿐 예외를 올리지 않는다. 블로킹 ``requests``라 호출부가
``asyncio.to_thread``로 감싼다."""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 10
KST = dt.timezone(dt.timedelta(hours=9))

NAVER_MAINNEWS_URL = "https://api.stock.naver.com/news/mainnews"
NAVER_ARTICLE_URL = "https://n.news.naver.com/mnews/article/{oid}/{aid}"

# (source 라벨, RSS URL)
RSS_SOURCES: list[tuple[str, str]] = [
    ("CNBC Top", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC Finance", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("MarketWatch Pulse", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]

SNIPPET_MAX = 300


@dataclass
class NewsRecord:
    source: str
    title: str
    url: str
    published_at: dt.datetime | None
    summary: str | None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(s: str | None, limit: int | None = None) -> str:
    s = html.unescape(_TAG_RE.sub(" ", s or ""))
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit] if limit else s


def parse_naver_mainnews(data: object) -> list[NewsRecord]:
    """네이버 mainnews JSON 배열 -> NewsRecord. 필수 필드가 없는 항목은 건너뛴다."""
    if not isinstance(data, list):
        raise ValueError("naver mainnews: 배열이 아님")
    out: list[NewsRecord] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        title = _clean_text(it.get("tit"), 500)
        oid, aid = it.get("oid"), it.get("aid")
        if not (title and oid and aid):
            continue
        published = None
        raw = str(it.get("dt") or "")
        if len(raw) >= 12:
            try:
                published = dt.datetime.strptime(raw[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=KST)
            except ValueError:
                published = None
        office = _clean_text(it.get("ohnm"))
        snippet = _clean_text(it.get("subcontent"), SNIPPET_MAX) or None
        out.append(
            NewsRecord(
                source=f"네이버증권/{office}" if office else "네이버증권",
                title=title,
                url=NAVER_ARTICLE_URL.format(oid=oid, aid=aid),
                published_at=published,
                summary=snippet,
            )
        )
    return out


def parse_rss(source: str, xml_text: str) -> list[NewsRecord]:
    """RSS 2.0 XML -> NewsRecord. DTD/ENTITY 선언이 있는 문서는 거부한다(XML 폭탄 방어)."""
    if "<!ENTITY" in xml_text.upper():
        raise ValueError(f"{source}: ENTITY 선언 포함 XML 거부")
    root = ET.fromstring(xml_text.strip())
    out: list[NewsRecord] = []
    for item in root.iter("item"):
        title = _clean_text(item.findtext("title"), 500)
        link = (item.findtext("link") or "").strip()
        if not (title and link.startswith(("http://", "https://"))):
            continue
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = email.utils.parsedate_to_datetime(pub.strip())
                if published.tzinfo is None:
                    published = published.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                published = None
        out.append(
            NewsRecord(
                source=source,
                title=title,
                url=link[:1000],
                published_at=published,
                summary=_clean_text(item.findtext("description"), SNIPPET_MAX) or None,
            )
        )
    return out


def fetch_naver_mainnews(page_size: int = 40) -> list[NewsRecord]:
    r = requests.get(
        NAVER_MAINNEWS_URL,
        params={"pageSize": page_size},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return parse_naver_mainnews(r.json())


def fetch_rss(source: str, url: str) -> list[NewsRecord]:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_rss(source, r.text)


def fetch_all_news() -> tuple[list[NewsRecord], dict[str, str]]:
    """모든 소스를 순차 시도. 반환: (레코드, {소스: 에러문구}). 소스 하나가 실패해도 계속한다."""
    records: list[NewsRecord] = []
    errors: dict[str, str] = {}
    fetchers = [("네이버증권", lambda: fetch_naver_mainnews())] + [
        (name, (lambda n=name, u=url: fetch_rss(n, u))) for name, url in RSS_SOURCES
    ]
    for name, fn in fetchers:
        try:
            got = fn()
            records.extend(got)
        except Exception as e:  # noqa: BLE001 - 소스별 실패 격리
            logger.warning("news: %s 수집 실패: %s", name, e)
            errors[name] = str(e)[:200]
    return records, errors
