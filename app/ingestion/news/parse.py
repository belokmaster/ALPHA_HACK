import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from app.ingestion.news.fetch import NewsError, Snapshot, safe_url

PARSER_VERSION = "news-3"
FEEDS = {
    "cbr-press": ("cbr", "Банк России", "https://www.cbr.ru/rss/RssPress"),
    "cbr-events": ("cbr", "Банк России", "https://www.cbr.ru/rss/eventrss"),
    "rosstat": ("rosstat", "Росстат", "https://rosstat.gov.ru/central-news/rss"),
}


@dataclass(frozen=True)
class Item:
    url: str
    guid: str | None
    title: str
    date: str | None


def rss(body: bytes, feed_url: str) -> list[Item]:
    if b"\0" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise NewsError("unsafe_xml")
    try:
        root = ET.fromstring(body)
        if root.tag != "rss" or root.find("channel") is None:
            raise ValueError
        result = []
        for node in root.findall("./channel/item")[:100]:
            link = node.findtext("link", "").strip()
            title = " ".join(node.findtext("title", "").split())
            guid = node.findtext("guid")
            result.append(
                Item(
                    urljoin(feed_url, link) if link else "",
                    guid.strip() if guid else None,
                    title,
                    node.findtext("pubDate"),
                )
            )
        return result
    except (ET.ParseError, ValueError):
        raise NewsError("invalid_rss") from None


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)

    def text(self):
        if self.tag in {
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "noscript",
            "iframe",
            "object",
            "svg",
            "form",
            "button",
            "template",
        }:
            return ""
        values = [c.text() if isinstance(c, Node) else c for c in self.children]
        if self.tag == "tr":
            return " [строка] " + " | ".join(v.strip() for v in values if v.strip()) + " [/строка] "
        if self.tag in {"td", "th"}:
            # Retain explicit span information, rather than silently assigning a
            # merged header/value to just one adjacent row/column.
            spans = " ".join(f"{k}={self.attrs[k]}" for k in ("rowspan", "colspan")
                             if self.attrs.get(k, "1") != "1")
            return (f"[{spans}] " if spans else "") + " ".join(values)
        if self.tag == "table":
            return " [таблица] " + " ".join(values) + " [/таблица] "
        return " ".join(values)


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 40_000 or len(self.stack) >= 256:
            raise NewsError("html_too_complex")
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in {
            "br",
            "hr",
            "img",
            "meta",
            "link",
            "input",
            "source",
            "wbr",
            "area",
            "embed",
            "param",
        }:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def normalize(text):
    return " ".join(text.replace("\xa0", " ").split())


def publication(raw: str, *, rss_date=False):
    try:
        if rss_date:
            value = parsedate_to_datetime(raw)
        else:
            raw = raw.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", raw):
                fmt = "%d.%m.%Y" if "." in raw else "%Y-%m-%d"
                return datetime.strptime(raw, fmt).replace(
                    tzinfo=ZoneInfo("Europe/Moscow")
                ).astimezone(UTC), "day"
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError
        return value.astimezone(UTC), "timestamp"
    except (ValueError, TypeError, OverflowError):
        raise NewsError("invalid_date") from None


@dataclass(frozen=True)
class Article:
    url: str
    guid: str | None
    title: str
    text: str
    published_at: datetime
    precision: str
    topics: list[str]


def article(snapshot: Snapshot, item: Item, now: datetime) -> Article:
    try:
        match = re.search(r"charset=([\w-]+)", snapshot.content_type, re.I)
        encoding = match.group(1).lower() if match else "utf-8"
        if encoding not in {"utf-8", "utf8", "windows-1251", "cp1251"}:
            raise NewsError("invalid_encoding")
        tree = Tree()
        decoded = snapshot.body.decode(encoding)
        if "\0" in decoded:
            raise NewsError("invalid_article_text")
        tree.feed(decoded)
    except (UnicodeError, LookupError):
        raise NewsError("invalid_encoding") from None
    titles = [n for n in tree.nodes if n.tag == "h1"]
    title = normalize(titles[0].text()) if titles else item.title
    candidates = [
        n
        for n in tree.nodes
        if n.attrs.get("itemprop") == "articleBody"
        or set(n.attrs.get("class", "").split())
        & {
            "news__text",
            "news-text",
            "news-detail__text",
            "news-detail",
            "landing-text",
            "article-body",
            "news__body",
            "news-content",
        }
    ]
    if not candidates:
        candidates = [n for n in tree.nodes if n.tag == "article"]
    # Rosstat publishes some releases as standalone LibreOffice XHTML documents.
    # Only this explicit source/path/format may use the full body as article text.
    # DCTERMS.issued/modified are authoring timestamps without a zone, not pubDate.
    url = urlsplit(snapshot.url)
    item_url = urlsplit(item.url)
    if (
        not candidates
        and url.hostname in {"rosstat.gov.ru", "www.rosstat.gov.ru"}
        and item_url.hostname in {"rosstat.gov.ru", "www.rosstat.gov.ru"}
        and re.fullmatch(r"/central-news/document/\d+", item_url.path)
        and re.fullmatch(
            r"/central-news/document/\d+|/storage/mediabank/[^/]+\.html", url.path
        )
        and any(
            n.tag == "meta"
            and n.attrs.get("name") == "DCTERMS.source"
            and n.attrs.get("content") == "http://xml.openoffice.org/odf2xhtml"
            for n in tree.nodes
        )
    ):
        candidates = [n for n in tree.nodes if n.tag == "body"]
    if not candidates:
        raise NewsError("article_body_missing")
    text = normalize(max((n.text() for n in candidates), key=len))
    if not title or len(text) < 30 or len(text) > 200_000:
        raise NewsError("invalid_article_text")
    dates = [
        n.attrs.get("content") or n.attrs.get("datetime")
        for n in tree.nodes
        if (
            n.tag == "meta"
            and (
                n.attrs.get("property") == "article:published_time"
                or n.attrs.get("itemprop") == "datePublished"
            )
        )
        or (n.tag == "time" and n.attrs.get("datetime"))
    ]
    pub, precision = publication(item.date, rss_date=True) if item.date else (None, None)
    if dates and dates[0]:
        html_pub, html_precision = publication(dates[0])
        if (
            pub
            and pub.astimezone(ZoneInfo("Europe/Moscow")).date()
            != html_pub.astimezone(ZoneInfo("Europe/Moscow")).date()
        ):
            raise NewsError("date_conflict")
        if pub is None:
            pub, precision = html_pub, html_precision
    if pub is None:
        raise NewsError("date_missing")
    if pub > now:
        raise NewsError("future_publication")
    # Transparent topic tags only; no entities, metrics or economic claims are inferred.
    lower = (title + " " + text).casefold()
    rules = {
        "rates": ("ключев", "процентн"),
        "inflation": ("инфляц", "потребительских цен"),
        "statistics": ("статистик", "росстат"),
        "regulation": ("регулирован", "норматив"),
    }
    topics = [key for key, words in rules.items() if any(word in lower for word in words)]
    return Article(safe_url(snapshot.url), item.guid, title, text, pub, precision, topics)
