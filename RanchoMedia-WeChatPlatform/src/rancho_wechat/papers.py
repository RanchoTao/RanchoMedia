"""Bounded original-paper discovery via arXiv's documented Atom search API."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlparse

from rancho_wechat.editorial import EditorialDesk, EditorialError, now
from rancho_wechat.feeds import fetch_feed, parse_feed

ARXIV_HOST = "https://export.arxiv.org/api/query"


def discover_arxiv(
    desk: EditorialDesk,
    topic_id: str,
    query: str,
    fetch: Callable[[str], bytes] = fetch_feed,
) -> list[dict[str, object]]:
    """Manual search, at most three metadata entries and one request per three seconds."""
    desk.topic(topic_id)
    query = " ".join(query.split())
    if not (3 <= len(query) <= 100):
        raise EditorialError("INVALID_QUERY", "论文检索词长度需为 3–100")
    key = "arxiv:rate-limit"
    stamp = now()
    with desk.store.write() as db:
        last = db.execute("SELECT updated_at FROM tasks WHERE task_key=?", (key,)).fetchone()
        if last and datetime.fromisoformat(stamp) - datetime.fromisoformat(
            last["updated_at"]
        ) < timedelta(seconds=3):
            raise EditorialError("ARXIV_RATE_LIMIT", "arXiv API 至少间隔三秒")
        if last:
            db.execute("UPDATE tasks SET updated_at=? WHERE task_key=?", (stamp, key))
        else:
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                (key, "ARXIV_RATE_LIMIT", "DONE", "{}", 1, None, None, stamp, stamp),
            )
    url = f"{ARXIV_HOST}?search_query={quote_plus('all:' + query)}&start=0&max_results=3"
    entries = parse_feed(fetch(url))
    results: list[dict[str, object]] = []
    for item in entries:
        link = str(item["url"] or "")
        parsed = urlparse(link)
        if parsed.hostname not in {"arxiv.org", "export.arxiv.org"}:
            continue
        link = parsed._replace(scheme="https").geturl()
        excerpt = str(item["excerpt"] or "")
        if len(excerpt) < 20:
            continue
        source = desk.add_source(
            str(item["title"]),
            link,
            excerpt[:20_000],
            "PRIMARY",
            topic_id,
            str(item["published_at"]) if item["published_at"] else None,
        )
        results.append(
            {
                "source_id": source["id"],
                "title": source["title"],
                "url": link,
                "retrieved_at": source["retrieved_at"],
                "metadata_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "warning": "arXiv 摘要是作者提交的原始论文元数据，不等于同行评审或事实独立认证。",
            }
        )
    return results
