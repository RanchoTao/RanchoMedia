from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from rancho_platform_sdk.contracts import (
    AuthenticationRequiredError,
    BeforeSendError,
    ExternalOutcomeUnknownError,
    HumanInterventionRequiredError,
    PublishMetadata,
    PublishRequest,
    RateLimitError,
    TerminalPlatformError,
)
from rancho_postiz.adapter import PostizAdapter


def request() -> PublishRequest:
    return PublishRequest(
        platform="postiz",
        account_id=uuid4(),
        content_variant_id=uuid4(),
        publication_job_id=uuid4(),
        idempotency_key="postiz-key",
        text="Reviewed content",
        title="Reviewed",
        topic="Test",
        scheduled_at=datetime(2026, 10, 15, tzinfo=UTC),
        metadata=PublishMetadata(topic="Test"),
    )


def adapter(
    transport: httpx.AsyncBaseTransport, journal: Path, enabled: bool = True
) -> PostizAdapter:
    return PostizAdapter(
        base_url="http://localhost",
        api_key="dummy-local-key",
        integration_id="fixture-integration",
        provider="x",
        transport=transport,
        journal_path=journal,
        enabled=enabled,
        external_side_effects_enabled=enabled,
    )


async def test_postiz_draft_contract_and_candidate_reconciliation(tmp_path: Path) -> None:
    calls = []

    def server(message: httpx.Request) -> httpx.Response:
        calls.append(message)
        assert message.headers["authorization"] == "dummy-local-key"
        if message.method == "POST":
            body = __import__("json").loads(message.content)
            assert message.url.path == "/public/v1/posts"
            assert body["type"] == "draft"
            assert body["posts"][0]["integration"]["id"] == "fixture-integration"
            assert body["posts"][0]["value"][0]["content"] == "Reviewed content"
            return httpx.Response(201, json=[{"id": "internal-1", "state": "DRAFT"}])
        assert message.url.path == "/public/v1/posts"
        assert "startDate" in message.url.params and "endDate" in message.url.params
        return httpx.Response(
            200,
            json={
                "posts": [
                    {"id": "internal-1", "state": "DRAFT", "content": "Reviewed content"},
                    {"id": "other", "content": "Different"},
                ]
            },
        )

    async with adapter(httpx.MockTransport(server), tmp_path / "journal.db") as wrapper:
        item = request()
        receipt = await wrapper.create_draft(item)
        assert receipt.post_ids == ("internal-1",)
        assert receipt.state == "DRAFT_IN_POSTIZ"
        with pytest.raises(HumanInterventionRequiredError):
            await wrapper.create_draft(item)
        candidates = await wrapper.reconcile_candidates(item)
        assert candidates == [{"id": "internal-1", "state": "DRAFT"}]
        assert len(calls) == 2


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (400, TerminalPlatformError),
        (401, AuthenticationRequiredError),
        (403, AuthenticationRequiredError),
        (429, RateLimitError),
        (500, ExternalOutcomeUnknownError),
        (302, TerminalPlatformError),
    ],
)
async def test_postiz_http_errors(tmp_path: Path, code: int, expected: type[Exception]) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(code, headers={"retry-after": "7"}))
    async with adapter(transport, tmp_path / "journal.db") as wrapper:
        with pytest.raises(expected):
            await wrapper.create_draft(request())


async def test_postiz_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    count = 0

    def server(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("response lost")

    transport = httpx.MockTransport(server)
    async with adapter(transport, tmp_path / "journal.db") as wrapper:
        item = request()
        with pytest.raises(ExternalOutcomeUnknownError):
            await wrapper.create_draft(item)
        with pytest.raises(HumanInterventionRequiredError):
            await wrapper.create_draft(item)
        async with adapter(transport, tmp_path / "journal.db") as restarted:
            with pytest.raises(HumanInterventionRequiredError):
                await restarted.create_draft(item)
        assert count == 1


async def test_postiz_rate_limit_keeps_intent_after_restart(tmp_path: Path) -> None:
    count = 0

    def server(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(429, headers={"retry-after": "7"})

    transport = httpx.MockTransport(server)
    item = request()
    journal = tmp_path / "journal.db"
    async with adapter(transport, journal) as wrapper:
        with pytest.raises(RateLimitError):
            await wrapper.create_draft(item)
    async with adapter(transport, journal) as restarted:
        with pytest.raises(HumanInterventionRequiredError):
            await restarted.create_draft(item)
    assert count == 1


async def test_postiz_disabled_and_nonlocal_rejected(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with adapter(transport, tmp_path / "disabled.db", enabled=False) as disabled:
        with pytest.raises(ValueError):
            PostizAdapter(
                base_url="https://example.com",
                api_key="dummy",
                integration_id="i",
                provider="x",
                transport=transport,
                enabled=True,
                external_side_effects_enabled=True,
            )
        with pytest.raises(BeforeSendError):
            await disabled.create_draft(request())
