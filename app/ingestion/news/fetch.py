import asyncio
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import httpx

HOSTS = frozenset({"www.cbr.ru", "cbr.ru", "rosstat.gov.ru", "www.rosstat.gov.ru"})
MAX_BYTES = 2_000_000
ROSSTAT_HOSTS = frozenset({"rosstat.gov.ru", "www.rosstat.gov.ru"})
CERTIFICATES = Path(__file__).resolve().parents[3] / "config/certs"


@lru_cache(maxsize=2)
def tls_context(*, rosstat: bool) -> ssl.SSLContext:
    # Match HTTPX's default CA bundle. Extra trust applies only to Rosstat requests,
    # never globally to the process, OS, other news publishers or model transport.
    context = ssl.create_default_context(cafile=certifi.where())
    if rosstat:
        for name in (
            "Russian_Trusted_Root_CA.pem",
            "Russian_Trusted_Sub_CA.pem",
            "Russian_Trusted_Sub_CA_2024.pem",
        ):
            context.load_verify_locations(cafile=CERTIFICATES / name)
    return context


class NewsError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def safe_url(value: str) -> str:
    try:
        u = urlsplit(value)
        if (
            len(value) > 2048
            or u.scheme != "https"
            or u.hostname not in HOSTS
            or u.username is not None
            or u.password is not None
            or u.port not in (None, 443)
            or any(ord(c) < 33 or c == "\\" for c in value)
        ):
            raise ValueError
        return urlunsplit(("https", u.hostname, u.path or "/", u.query, ""))
    except (ValueError, TypeError):
        raise NewsError("unsafe_url") from None


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos}, key=lambda s: (":" in s, s))


@dataclass(frozen=True)
class Snapshot:
    url: str
    body: bytes
    content_type: str


class SafeFetcher:
    def __init__(self, *, resolver=resolve, transport=None, timeout=10):
        self.resolver = resolver
        self.transport = transport
        if not 0 < timeout <= 10:
            raise ValueError("Fetch timeout must be within 10 seconds")
        self.timeout = timeout

    async def fetch(self, url: str, *, kind: str) -> Snapshot:
        try:
            async with asyncio.timeout(self.timeout):
                return await self._fetch(url, kind)
        except NewsError:
            raise
        except (httpx.HTTPError, OSError, TimeoutError, ValueError):
            raise NewsError("fetch_unavailable") from None

    async def _fetch(self, url, kind):
        current = safe_url(url)
        for hop in range(4):
            host = urlsplit(current).hostname
            addresses = await self.resolver(host)
            if not addresses or any(
                not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
                for ip in addresses
            ):
                raise NewsError("unsafe_address")
            # Pin the checked IP. Host/SNI preserve TLS hostname verification. No second DNS
            # lookup, implicit proxy, connection reuse across hosts or automatic redirects.
            ip = addresses[0]
            async with httpx.AsyncClient(
                transport=self.transport
                or httpx.AsyncHTTPTransport(
                    retries=0, verify=tls_context(rosstat=host in ROSSTAT_HOSTS)
                ),
                trust_env=False,
                follow_redirects=False,
                timeout=self.timeout,
            ) as client:
                req = client.build_request(
                    "GET",
                    current,
                    headers={
                        "Accept-Encoding": "identity",
                        "User-Agent": "AlfaNewsPrototype/1.0",
                        "Host": host,
                    },
                )
                req.url = req.url.copy_with(host=ip)
                req.extensions["sni_hostname"] = host
                response = await client.send(req, stream=True)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if hop == 3 or not response.headers.get("location"):
                            raise NewsError("redirect_limit")
                        target = safe_url(urljoin(current, response.headers["location"]))
                        # Keep each publisher inside its own allowlist family.
                        if (host.endswith("cbr.ru")) != (
                            urlsplit(target).hostname.endswith("cbr.ru")
                        ):
                            raise NewsError("cross_publisher_redirect")
                        current = target
                        continue
                    if response.status_code != 200:
                        raise NewsError("http_" + str(response.status_code))
                    media = (
                        response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                    )
                    allowed = (
                        {"application/rss+xml", "application/xml", "text/xml"}
                        if kind == "rss"
                        else {"text/html", "application/xhtml+xml"}
                    )
                    if media not in allowed:
                        raise NewsError("invalid_content_type")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise NewsError("encoded_body_rejected")
                    if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                        raise NewsError("body_too_large")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise NewsError("body_too_large")
                    return Snapshot(current, bytes(body), response.headers["content-type"])
                finally:
                    await response.aclose()
        raise NewsError("redirect_limit")
