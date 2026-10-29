from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.feeds import parse_feed
from rancho_wechat.papers import discover_arxiv
from rancho_wechat.providers import MockProvider, OllamaProvider
from rancho_wechat.store import Store
from rancho_wechat.web import create_app


def test_editor_ui_auth_and_review_gate(tmp_path: Path) -> None:
    desk = EditorialDesk(Store(tmp_path / "db.sqlite"), MockProvider(), tmp_path / "exports")
    client = TestClient(create_app(desk, "a" * 32))
    assert client.get("/").status_code == 200
    assert "微信编辑台" in client.get("/").text
    assert client.get("/api/topics").status_code == 401
    headers = {"X-Editor-Token": "a" * 32}
    created = client.post(
        "/api/topics", headers=headers, json={"title": "人工输入的可信选题", "idea": "研究方法"}
    )
    assert created.status_code == 200
    topic_id = created.json()["id"]
    assert client.post(f"/api/topics/{topic_id}/research", headers=headers).status_code == 409
    assert (
        client.post(
            f"/api/topics/{topic_id}/decision", headers=headers, json={"decision": "ACCEPT"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/topics/{topic_id}/research", headers=headers).status_code == 200
    article = client.post(
        f"/api/topics/{topic_id}/draft", headers=headers, json={"angle": "main"}
    ).json()
    assert article["status"] == "IN_REVIEW"
    assert client.get(f"/api/articles/{article['id']}/export", headers=headers).status_code == 409
    assert (
        client.post(
            f"/api/articles/{article['id']}/decision",
            headers=headers,
            json={"decision": "APPROVE", "fact_attested": True},
        ).status_code
        == 409
    )


def test_feed_parser_and_injection_bounds() -> None:
    rss = (
        b"<rss><channel><item><title>Paper released</title>"
        b"<link>https://example.org/paper</link>"
        b"<description>Original paper summary with useful context.</description>"
        b"<pubDate>Wed, 21 Oct 2026 08:00:00 GMT</pubDate>"
        b"</item></channel></rss>"
    )
    items = parse_feed(rss)
    assert items[0]["published_at"] == "2026-10-21T08:00:00+00:00"
    assert items[0]["url"] == "https://example.org/paper"
    try:
        parse_feed(b'<!DOCTYPE x [ <!ENTITY e SYSTEM "file:///etc/passwd"> ]><rss>&e;</rss>')
    except EditorialError as error:
        assert error.code == "UNSAFE_FEED"
    else:
        raise AssertionError("DTD should be rejected")


def test_model_provider_rejects_nonlocal_url() -> None:
    try:
        OllamaProvider("local-model", "https://remote.example")
    except ValueError:
        pass
    else:
        raise AssertionError("Remote model endpoint should be refused")


def test_arxiv_search_imports_bounded_original_metadata(tmp_path: Path) -> None:
    desk = EditorialDesk(Store(tmp_path / "db.sqlite"), MockProvider(), tmp_path / "exports")
    topic = desk.create_topic("论文元数据中的原始证据")
    atom = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b"<title>A paper about mathematics and models</title>"
        b'<link href="http://arxiv.org/abs/2601.00001"/>'
        b"<summary>Authors describe an experimental method and limitations "
        b"in the abstract.</summary>"
        b"<published>2026-01-02T12:00:00Z</published>"
        b"</entry></feed>"
    )
    called = []

    def fetch(url: str) -> bytes:
        called.append(url)
        return atom

    found = discover_arxiv(desk, topic["id"], "mathematics AI", fetch)
    assert len(found) == 1
    assert found[0]["url"] == "https://arxiv.org/abs/2601.00001"
    assert "max_results=3" in called[0]
    assert desk.sources(topic["id"])[0]["published_at"] == "2026-01-02T12:00:00+00:00"
    try:
        discover_arxiv(desk, topic["id"], "mathematics AI", fetch)
    except EditorialError as error:
        assert error.code == "ARXIV_RATE_LIMIT"
    else:
        raise AssertionError("Search should obey three-second minimum")
