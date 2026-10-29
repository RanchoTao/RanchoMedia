"""Bounded ingestion of explicitly configured RSS/Atom feeds."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

from rancho_wechat.editorial import EditorialDesk, EditorialError


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(raw: str) -> str:
    parser = _PlainText()
    parser.feed(raw)
    return " ".join("".join(parser.parts).split())


def configured_feeds(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise EditorialError("INVALID_FEED_CONFIG", "feeds.json 必须是 URL 数组")
    return data


def _public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise EditorialError("INVALID_FEED_URL", "RSS 必须是无凭据的 HTTPS 地址")
    if parsed.port not in {None, 443}:
        raise EditorialError("INVALID_FEED_PORT", "RSS 仅允许 HTTPS 默认端口")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise EditorialError("FEED_DNS_FAILED", "RSS 域名解析失败") from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise EditorialError("PRIVATE_FEED_ADDRESS", "RSS 解析到了非公网地址")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def fetch_feed(url: str) -> bytes:
    _public_https(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "RanchoMedia-WeChatPlatform/0.1"})
    try:
        with opener.open(request, timeout=12) as response:
            body = bytes(response.read(1_000_001))
    except (urllib.error.URLError, TimeoutError) as error:
        raise EditorialError("FEED_FETCH_FAILED", "RSS 获取失败") from error
    if len(body) > 1_000_000:
        raise EditorialError("FEED_TOO_LARGE", "RSS 超过 1 MB")
    return body


def _date(raw: str) -> str | None:
    if not raw:
        return None
    try:
        result = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return result.astimezone(UTC).isoformat() if result.tzinfo else None


def parse_feed(body: bytes) -> list[dict[str, str | None]]:
    if len(body) > 1_000_000 or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise EditorialError("UNSAFE_FEED", "RSS 包含不允许的 XML 声明或体积过大")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise EditorialError("INVALID_FEED", "RSS XML 无效") from error
    items: list[dict[str, str | None]] = []
    for item in root.findall(".//item")[:30]:
        items.append(
            {
                "title": _plain(item.findtext("title") or ""),
                "url": (item.findtext("link") or "").strip(),
                "excerpt": _plain(item.findtext("description") or ""),
                "published_at": _date(item.findtext("pubDate") or ""),
            }
        )
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//a:entry", ns)[:30]:
        link = item.find("a:link", ns)
        items.append(
            {
                "title": _plain(item.findtext("a:title", default="", namespaces=ns)),
                "url": link.get("href", "") if link is not None else "",
                "excerpt": _plain(item.findtext("a:summary", default="", namespaces=ns)),
                "published_at": _date(
                    item.findtext("a:published", default="", namespaces=ns)
                    or item.findtext("a:updated", default="", namespaces=ns)
                ),
            }
        )
    return items[:30]


def ingest_configured(desk: EditorialDesk, config: Path) -> dict[str, int]:
    feeds = configured_feeds(config)
    total, skipped = 0, 0
    for feed in feeds:
        for item in parse_feed(fetch_feed(feed)):
            try:
                desk.add_source(
                    str(item["title"]),
                    str(item["url"]),
                    str(item["excerpt"]),
                    "RSS",
                    published_at=item["published_at"],
                )
                total += 1
            except EditorialError:
                skipped += 1
    return {"feeds": len(feeds), "imported_or_existing": total, "skipped": skipped}
