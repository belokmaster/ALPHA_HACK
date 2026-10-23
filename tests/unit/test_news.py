import asyncio
import hashlib
import ssl
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.ingestion.news.fetch import (
    MAX_BYTES,
    NewsError,
    SafeFetcher,
    Snapshot,
    safe_url,
    tls_context,
)
from app.ingestion.news.parse import FEEDS, Item, article, rss

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/news"
NOW = datetime(2026, 10, 22, tzinfo=UTC)
URL = FEEDS["cbr-press"][2]


async def public(_):
    return ["93.184.216.34"]


def test_rosstat_certificate_trust_is_scoped_and_verification_stays_required():
    base = tls_context(rosstat=False)
    extra = tls_context(rosstat=True)
    base_certs = {hashlib.sha256(c).hexdigest() for c in base.get_ca_certs(binary_form=True)}
    extra_certs = {hashlib.sha256(c).hexdigest() for c in extra.get_ca_certs(binary_form=True)}
    root = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
    assert root not in base_certs and root in extra_certs
    assert base_certs < extra_certs
    for context in (base, extra):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("host", ["rosstat.gov.ru", "www.rosstat.gov.ru", "www.cbr.ru"])
async def test_real_transport_selects_trust_by_logical_host(monkeypatch, host):
    contexts = []

    def transport(*, retries, verify):
        assert retries == 0
        contexts.append(verify)
        return httpx.MockTransport(lambda _: response(b"<html>ok</html>"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    await SafeFetcher(resolver=public).fetch("https://" + host + "/", kind="html")
    assert contexts == [tls_context(rosstat=host.endswith("rosstat.gov.ru"))]


def response(body, status=200, kind="text/html", headers=None):
    return httpx.Response(
        status, headers={"content-type": kind, **(headers or {})}, stream=httpx.ByteStream(body)
    )


@pytest.mark.parametrize("prefix", ["cbr", "rosstat"])
def test_saved_rss_and_html_are_normalized_without_invented_facts(prefix):
    feed_url = URL if prefix == "cbr" else FEEDS["rosstat"][2]
    item = rss((FIXTURES / (prefix + ".xml")).read_bytes(), feed_url)[0]
    parsed = article(
        Snapshot(item.url, (FIXTURES / (prefix + ".html")).read_bytes(), "text/html"), item, NOW
    )
    assert parsed.published_at == datetime(2026, 10, 21, 9, tzinfo=UTC)
    assert parsed.guid and parsed.url.startswith("https://")
    assert "window.EVIL" not in parsed.text and "evil-frame" not in parsed.text
    assert "Меню сайта" not in parsed.text and "Не текст новости" not in parsed.text
    assert "<script" not in parsed.text and parsed.topics


@pytest.mark.parametrize(
    "raw", ["nonsense", "Wed, 21 Oct 2026 12:00:00", "Wed, 21 Oct 2030 12:00:00 +0300"]
)
def test_invalid_naive_or_future_dates_are_rejected(raw):
    from dataclasses import replace

    item = replace(rss((FIXTURES / "cbr.xml").read_bytes(), URL)[0], date=raw)
    with pytest.raises(NewsError):
        article(Snapshot(item.url, (FIXTURES / "cbr.html").read_bytes(), "text/html"), item, NOW)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.cbr.ru/",
        "https://127.0.0.1/",
        "https://www.cbr.ru.evil.test/",
        "https://user:pass@www.cbr.ru/",
        "https://www.cbr.ru:444/",
        "https://www.cbr.ru/\\evil",
    ],
)
def test_url_allowlist(url):
    with pytest.raises(NewsError):
        safe_url(url)


async def test_dns_is_pinned_with_original_tls_name_and_host():
    seen = []

    def handler(req):
        seen.append(req)
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "www.cbr.ru"
        assert req.extensions["sni_hostname"] == "www.cbr.ru"
        return response(b"<html>ok</html>")

    page = await SafeFetcher(resolver=public, transport=httpx.MockTransport(handler)).fetch(
        "https://www.cbr.ru/press/#fragment", kind="html"
    )
    assert page.url == "https://www.cbr.ru/press/" and len(seen) == 1


@pytest.mark.parametrize(
    "target",
    ["https://localhost/", "http://www.cbr.ru/private", "https://rosstat.gov.ru/central-news/rss"],
)
async def test_dangerous_redirect_is_never_requested(target):
    seen = []

    def handler(req):
        seen.append(req)
        return response(b"", 302, headers={"location": target})

    with pytest.raises(NewsError):
        await SafeFetcher(resolver=public, transport=httpx.MockTransport(handler)).fetch(
            URL, kind="rss"
        )
    assert len(seen) == 1


async def test_dns_rebinding_on_allowed_redirect_is_rejected_before_connect():
    resolutions = []

    async def resolver(host):
        resolutions.append(host)
        return ["93.184.216.34"] if len(resolutions) == 1 else ["127.0.0.1"]

    seen = []

    def handler(req):
        seen.append(req)
        return response(b"", 302, headers={"location": "/new"})

    with pytest.raises(NewsError) as err:
        await SafeFetcher(resolver=resolver, transport=httpx.MockTransport(handler)).fetch(
            URL, kind="rss"
        )
    assert err.value.code == "unsafe_address" and len(seen) == 1


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"content-type": "application/pdf"}, b"pdf"),
        ({"content-encoding": "gzip"}, b"zip"),
        ({"content-length": str(MAX_BYTES + 1)}, b""),
        ({}, b"x" * (MAX_BYTES + 1)),
    ],
)
async def test_size_type_and_encoding_bounds(headers, body):
    with pytest.raises(NewsError):
        await SafeFetcher(
            resolver=public,
            transport=httpx.MockTransport(lambda _: response(body, headers=headers)),
        ).fetch(URL, kind="html")


async def test_wall_timeout_is_bounded():
    async def slow(_):
        await asyncio.sleep(10)

    with pytest.raises(NewsError):
        await SafeFetcher(resolver=public, transport=httpx.MockTransport(slow), timeout=0.03).fetch(
            URL, kind="html"
        )


def test_xml_entities_and_nonrss_rejected():
    for body in [b'<!DOCTYPE rss [<!ENTITY a "x">]><rss/>', b"<html>error</html>"]:
        with pytest.raises(NewsError):
            rss(body, URL)


def test_rosstat_libreoffice_uses_rss_publication_and_safe_body_only():
    url = "https://rosstat.gov.ru/central-news/document/292614"
    item = Item(url, None, "Учебная публикация", "Wed, 21 Oct 2026 17:25:00 +0300")
    body = (FIXTURES / "rosstat-libreoffice.html").read_bytes()
    final = "https://rosstat.gov.ru/storage/mediabank/163_21-10-2026.html"
    parsed = article(Snapshot(final, body, "application/xhtml+xml"), item, NOW)
    assert parsed.published_at == datetime(2026, 10, 21, 14, 25, tzinfo=UTC)
    assert parsed.precision == "timestamp" and parsed.title == item.title
    assert parsed.url == final
    assert parsed.text == (
        "Учебная статистическая публикация Росстата. [таблица] [строка] "
        "Учебный показатель | значение неизвестно [/строка] [/таблица]"
    )
    # No generic body fallback, even with the same authoring metadata.
    for other in ("https://www.cbr.ru/central-news/document/292614", "https://rosstat.gov.ru/"):
        with pytest.raises(NewsError, match="article_body_missing"):
            article(Snapshot(other, body, "text/html"), item, NOW)
    with pytest.raises(NewsError, match="date_missing"):
        article(Snapshot(url, body, "text/html"), Item(url, None, item.title, None), NOW)


def test_table_boundaries_merged_headers_missing_values_and_footnotes_survive():
    from app.ingestion.news.parse import Tree, normalize

    tree = Tree()
    tree.feed(
        '<table><tr><th rowspan="2">Ряд</th><th colspan="2">рублей за литр</th></tr>'
        "<tr><th>12 октября</th><th>19 октября</th></tr>"
        "<tr><td>Бензин</td><td>78,68</td><td>78,04</td></tr>"
        '<tr><td colspan="3">* Средневзвешенная цена; ... — нет наблюдения</td></tr></table>'
    )
    text = normalize(tree.root.text())
    assert text.count("[строка]") == 4 and "[rowspan=2]" in text and "[colspan=2]" in text
    assert "Бензин | 78,68 | 78,04" in text
    assert "* Средневзвешенная цена; ... — нет наблюдения" in text
