"""Reusable SDK contract; replace adapter/contract_request fixtures for an authorized adapter.

Native idempotency and reconciliation are requirements for the selected LEVEL 2 profile.
Adapters lacking them must report unsupported capability instead of claiming this profile.
"""

import asyncio
from uuid import uuid4

import pytest
from rancho_core.domain import ACCOUNT_ID, DomainError, FrozenClock
from rancho_platform_sdk.contracts import (
    BeforeSendError,
    Capability,
    ExternalOutcomeUnknownError,
    FailureMode,
    HumanInterventionRequiredError,
    PlatformAdapter,
    PublishMetadata,
    PublishRequest,
    RateLimitError,
    TerminalPlatformError,
    UnsupportedCapabilityError,
)

from tests.conftest import Runtime


@pytest.fixture
def adapter(runtime: Runtime) -> PlatformAdapter:
    return runtime.adapter


@pytest.fixture
def contract_request(clock: FrozenClock) -> PublishRequest:
    return PublishRequest(
        publication_job_id=uuid4(),
        idempotency_key="contract",
        platform="mock",
        account_id=ACCOUNT_ID,
        content_variant_id=uuid4(),
        text="Fixture text",
        title="Fixture title",
        scheduled_at=clock.now(),
        topic="Research",
        metadata=PublishMetadata(simulation=True, topic="Research"),
    )


async def test_adapter_health(adapter: PlatformAdapter) -> None:
    adapter.capabilities.require(Capability.HEALTH)
    assert await adapter.health_check()
    assert await adapter.authenticate()


async def test_publish_contract(adapter: PlatformAdapter, contract_request: PublishRequest) -> None:
    result = await adapter.publish(contract_request)
    assert result.status == "SUCCEEDED"
    assert result.external_id and result.external_url
    assert result.published_at.tzinfo is not None
    assert (await adapter.fetch_post(result.external_id)) == result


async def test_publish_idempotency(
    adapter: PlatformAdapter, contract_request: PublishRequest, runtime: Runtime
) -> None:
    results = await asyncio.gather(*(adapter.publish(contract_request) for _ in range(5)))
    assert len({result.external_id for result in results}) == 1
    assert await runtime.adapter.external_post_count() == 1
    assert (
        await adapter.lookup_by_idempotency_key(
            contract_request.account_id, contract_request.idempotency_key
        )
        == results[0]
    )
    changed = contract_request.model_copy(update={"text": "Different fixture payload"})
    with pytest.raises(DomainError) as conflict:
        await adapter.publish(changed)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert await runtime.adapter.external_post_count() == 1


async def test_unsupported_capability(
    adapter: PlatformAdapter, contract_request: PublishRequest, clock: FrozenClock
) -> None:
    operations = {
        Capability.SCHEDULE: lambda: adapter.schedule(contract_request),
        Capability.DELETE: lambda: adapter.delete("absent"),
        Capability.UPDATE: lambda: adapter.update("absent", "text"),
        Capability.SEARCH: lambda: adapter.search("query"),
        Capability.TRENDS: lambda: adapter.trends(clock.now()),
    }
    for capability, operation in operations.items():
        assert capability not in adapter.capabilities.supported
        with pytest.raises(UnsupportedCapabilityError):
            adapter.capabilities.require(capability)
        with pytest.raises(UnsupportedCapabilityError):
            await operation()


async def test_fetch_metrics(adapter: PlatformAdapter, contract_request: PublishRequest) -> None:
    result = await adapter.publish(contract_request)
    previous = 0
    for age in (0, 3600, 21600, 86400):
        metrics = await adapter.fetch_metrics(result.external_id, age)
        assert metrics == await adapter.fetch_metrics(result.external_id, age)
        assert metrics.views is not None and metrics.views >= previous
        assert metrics.watch_time is None
        previous = metrics.views


async def test_comments_reply_persistence(
    adapter: PlatformAdapter, contract_request: PublishRequest
) -> None:
    result = await adapter.publish(contract_request)
    comments = await adapter.fetch_comments(result.external_id)
    assert comments == await adapter.fetch_comments(result.external_id)
    reply = await adapter.reply_comment(comments[0].id, "SIMULATED reply", "reply-one")
    assert reply == await adapter.reply_comment(comments[0].id, "SIMULATED reply", "reply-one")
    with pytest.raises(DomainError):
        await adapter.reply_comment(comments[0].id, "Different reply", "reply-one")


@pytest.mark.parametrize(
    ("failure", "exception", "external_count"),
    [
        (FailureMode.FAIL_BEFORE_SEND, BeforeSendError, 0),
        (FailureMode.FAIL_AFTER_EXTERNAL_SUCCESS, ExternalOutcomeUnknownError, 1),
        (FailureMode.RATE_LIMIT, RateLimitError, 0),
        (FailureMode.TERMINAL_ERROR, TerminalPlatformError, 0),
        (FailureMode.HUMAN_REQUIRED, HumanInterventionRequiredError, 0),
    ],
)
async def test_external_error_mapping(
    adapter: PlatformAdapter,
    contract_request: PublishRequest,
    runtime: Runtime,
    failure: FailureMode,
    exception: type[Exception],
    external_count: int,
) -> None:
    request = contract_request.model_copy(
        update={
            "metadata": PublishMetadata(
                simulation=True, topic=contract_request.topic, failure_mode=failure
            )
        }
    )
    with pytest.raises(exception):
        await adapter.publish(request)
    assert await runtime.adapter.external_post_count() == external_count
