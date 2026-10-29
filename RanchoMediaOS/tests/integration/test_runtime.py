import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from rancho_core.domain import ACCOUNT_ID, DomainError, FrozenClock
from rancho_core.schemas import PublicationCreate
from rancho_core.service import MediaService
from rancho_core.settings import Settings
from rancho_db.models import (
    ContentBrief,
    ContentGenome,
    ContentVariant,
    MockMetricState,
    MockReply,
    Owner,
    PublicationJob,
    ReviewApproval,
)
from rancho_db.repositories import Repository, UnitOfWork
from rancho_mock.adapter import PersistentMockAdapter
from rancho_platform_sdk.contracts import PublishRequest, PublishResult
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tests.conftest import Runtime, open_runtime
from tests.helpers import approved, draft, post, publication_request


async def test_concurrent_same_request(runtime: Runtime, clock: FrozenClock) -> None:
    variant = await approved(runtime.client, clock)
    request = publication_request(variant, clock, "concurrent")
    # API handlers create independent transactions; no application mutex.
    replies = await asyncio.gather(
        *(runtime.client.post("/publications", json=request) for _ in range(8))
    )
    assert all(reply.status_code == 200 for reply in replies), [reply.text for reply in replies]
    ids = {reply.json()["id"] for reply in replies}
    assert len(ids) == 1
    await runtime.service.drain()
    final = (await runtime.client.get(f"/publications/{ids.pop()}")).json()
    assert final["outcome"] == "SUCCEEDED" and final["attempt_count"] == 1
    assert await runtime.adapter.external_post_count() == 1
    async with runtime.service.work() as work:
        assert await work.jobs.count() == await work.jobs.publications.count() == 1
        assert await work.jobs.effects.count() == await work.jobs.outbox.count() == 1


async def test_reject_edit_invalidate_approval(runtime: Runtime, clock: FrozenClock) -> None:
    variant = await approved(runtime.client, clock)
    edited = await post(
        runtime.client,
        f"/review/{variant['id']}/edit",
        {"title": "Revised fixture", "text": "Immutable new revision"},
    )
    assert edited["status"] == "REQUIRES_REVIEW" and edited["revision"] == 2
    original = (await runtime.client.get(f"/review/{variant['id']}")).json()
    assert original["status"] == "SUPERSEDED" and original["text"] == variant["text"]
    for current in (variant, edited):
        blocked = await runtime.client.post(
            "/publications", json=publication_request(current, clock)
        )
        assert blocked.status_code == 409
    rejected = await post(runtime.client, f"/review/{edited['id']}/reject")
    assert rejected["status"] == "REJECTED"
    assert (
        await runtime.client.post("/publications", json=publication_request(edited, clock))
    ).status_code == 409
    async with runtime.service.work() as work:
        approval = await work.content.approvals.find(
            ReviewApproval.variant_id == UUID(variant["id"])
        )
        assert approval.revoked
        assert {"edit", "reject"} <= {row.action for row in await work.audit.list()}
    assert await runtime.adapter.external_post_count() == 0


async def test_approval_binds_account_schedule_payload(
    runtime: Runtime, clock: FrozenClock
) -> None:
    variant = await approved(runtime.client, clock)
    request = publication_request(variant, clock)
    changed_time = {**request, "scheduled_at": (clock.now() + timedelta(hours=1)).isoformat()}
    response = await runtime.client.post("/publications", json=changed_time)
    assert response.json()["error"]["code"] == "APPROVAL_MISMATCH"
    await post(runtime.client, "/publications", request)
    response = await runtime.client.post("/publications", json=changed_time)
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_future_schedule_and_revocation(runtime: Runtime, clock: FrozenClock) -> None:
    content = await draft(runtime.client)
    variant = content["variants"][0]
    scheduled = clock.now() + timedelta(hours=6)
    await post(
        runtime.client,
        f"/review/{variant['id']}/approve",
        {"account_id": str(ACCOUNT_ID), "scheduled_at": scheduled.isoformat()},
    )
    request = {**publication_request(variant, clock), "scheduled_at": scheduled.isoformat()}
    queued = await post(runtime.client, "/publications", request)
    assert queued["status"] == "SCHEDULED" and await runtime.adapter.external_post_count() == 0
    await post(runtime.client, f"/review/{variant['id']}/reject")
    clock.value = scheduled
    result = await post(runtime.client, "/workflows/drain")
    assert result[0]["status"] == "CANCELLED"
    assert await runtime.adapter.external_post_count() == 0


@pytest.mark.parametrize(
    ("mode", "status", "count"),
    [
        ("FAIL_BEFORE_SEND", "FAILED_RETRYABLE", 0),
        ("RATE_LIMIT", "FAILED_RETRYABLE", 0),
        ("TERMINAL_ERROR", "FAILED_TERMINAL", 0),
        ("HUMAN_REQUIRED", "NEEDS_HUMAN", 0),
    ],
)
async def test_failure_states(
    runtime: Runtime, clock: FrozenClock, mode: str, status: str, count: int
) -> None:
    variant = await approved(runtime.client, clock)
    request = {**publication_request(variant, clock, mode), "failure_mode": mode}
    initial = await post(runtime.client, "/publications", request)
    assert initial["status"] == status and await runtime.adapter.external_post_count() == count
    repeated = await post(runtime.client, "/publications", request)
    if mode == "FAIL_BEFORE_SEND":
        assert repeated["outcome"] == "SUCCEEDED" and repeated["attempt_count"] == 2
    elif mode == "RATE_LIMIT":
        assert repeated["attempt_count"] == 1
        clock.value += timedelta(seconds=60)
        final = await post(runtime.client, "/publications", request)
        assert final["outcome"] == "SUCCEEDED" and final["attempt_count"] == 2
    else:
        assert repeated["status"] == status and repeated["attempt_count"] == 1
        assert await runtime.adapter.external_post_count() == 0


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterExternalCommit(PersistentMockAdapter):
    async def publish(self, request: PublishRequest) -> PublishResult:
        await super().publish(request)
        raise SimulatedProcessCrash()


async def test_crash_between_external_commit_and_local_confirmation(
    settings: Settings, clock: FrozenClock
) -> None:
    async with open_runtime(settings, clock) as runtime:
        variant = await approved(runtime.client, clock)
        request = PublicationCreate.model_validate(publication_request(variant, clock, "crash"))
        crash_adapter = CrashAfterExternalCommit(runtime.adapter.database, clock, 7)
        service = MediaService(runtime.service.database, crash_adapter, clock)
        with pytest.raises(SimulatedProcessCrash):
            await service.publish(request)
        async with service.work() as work:
            job = (await work.jobs.list())[0]
            assert job.status == "DISPATCHING" and await work.jobs.publications.count() == 0
        assert await crash_adapter.external_post_count() == 1
    async with open_runtime(settings, clock) as restarted:
        recovered = await restarted.service.publish(request)
        assert recovered.outcome == "SUCCEEDED" and recovered.attempt_count == 1
        assert await restarted.adapter.external_post_count() == 1


async def test_expired_lease_absence_never_resends(runtime: Runtime, clock: FrozenClock) -> None:
    variant = await approved(runtime.client, clock)
    job_id = await runtime.service.prepare_job(
        PublicationCreate.model_validate(publication_request(variant, clock))
    )
    async with runtime.service.work() as work:
        job = await work.jobs.get(job_id)
        from rancho_core.domain import EffectStatus, JobStatus

        await work.jobs.change_state(job, JobStatus.DISPATCHING, clock.now())
        await work.jobs.change_effect(job_id, EffectStatus.IN_FLIGHT)
        job.attempt_count, job.lease_expires_at = 1, clock.now() - timedelta(seconds=1)
    final = await runtime.service.dispatch(job_id)
    assert final.status == "OUTCOME_UNKNOWN" and final.attempt_count == 1
    await runtime.service.dispatch(job_id)
    assert await runtime.adapter.external_post_count() == 0


async def test_owner_scope_foreign_keys_and_local_identity(
    runtime: Runtime, clock: FrozenClock
) -> None:
    content = await draft(runtime.client)
    other = uuid4()
    async with UnitOfWork(runtime.service.database, other) as work:
        await work.owners.add(Owner(id=other, created_at=clock.now(), name="Other fixture"))
        with pytest.raises(DomainError):
            await work.content.items.get(UUID(content["id"]))
    async with runtime.service.database.sessions.begin() as session:
        with pytest.raises(IntegrityError):
            # A real composite FK rejects linking another owner's content idea.
            source = await session.scalar(
                select(ContentBrief).where(ContentBrief.id == UUID(content["brief_id"]))
            )
            session.add(
                ContentBrief(
                    id=uuid4(),
                    owner_id=other,
                    created_at=clock.now(),
                    idea_id=source.idea_id,
                    topic="Cross-owner",
                    objective="must fail",
                )
            )
            await session.flush()
    missing = await runtime.client.get("/trends", headers={"X-Actor-ID": str(other)})
    assert missing.status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app), base_url="http://test"
    ) as anonymous:
        assert (await anonymous.get("/review")).status_code == 401


async def test_immutable_payload_and_state_sql_guards(runtime: Runtime, clock: FrozenClock) -> None:
    variant = await approved(runtime.client, clock)
    publication = await post(runtime.client, "/publications", publication_request(variant, clock))
    for statement in (
        update(ContentVariant)
        .where(ContentVariant.id == UUID(variant["id"]))
        .values(text="tampered"),
        update(PublicationJob)
        .where(PublicationJob.id == UUID(publication["id"]))
        .values(status="DISPATCHING"),
        update(ContentGenome)
        .where(ContentGenome.id == UUID(variant["genome_id"]))
        .values(topic="tampered"),
    ):
        with pytest.raises(IntegrityError):
            async with runtime.service.database.sessions.begin() as session:
                await session.execute(statement)


async def test_metrics_dedup_and_restart(settings: Settings, clock: FrozenClock) -> None:
    async with open_runtime(settings, clock) as runtime:
        variant = await approved(runtime.client, clock)
        pub = await post(
            runtime.client, "/publications", publication_request(variant, clock, "metrics")
        )
        first = await post(
            runtime.client, f"/publications/{pub['id']}/metrics/mock", {"age_hours": 6}
        )
        same = await post(
            runtime.client, f"/publications/{pub['id']}/metrics/mock", {"age_hours": 6}
        )
        assert first == same
    async with open_runtime(settings, clock) as runtime:
        repeated = await post(
            runtime.client, f"/publications/{pub['id']}/metrics/mock", {"age_hours": 6}
        )
        assert first == repeated
        async with runtime.service.work() as work:
            assert await work.metrics.count() == 1
        async with runtime.adapter.database.sessions() as session:
            assert await Repository(session, MockMetricState, None).count() == 1
            assert await Repository(session, MockReply, None).count() == 0


async def test_typed_errors_naive_time(runtime: Runtime) -> None:
    response = await runtime.client.post(
        "/publications",
        json={
            "content_variant_id": str(uuid4()),
            "scheduled_at": "2026-09-28T10:00:00",
            "idempotency_key": "invalid",
        },
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"] == {}
    schema = runtime.app.openapi()
    assert schema["paths"]["/publications"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("PublicationCreate")


async def test_strategy_latest_with_identical_clock_and_candidate_concurrency(
    runtime: Runtime,
) -> None:
    await draft(runtime.client)
    before = await asyncio.gather(*(runtime.client.get("/topics/next") for _ in range(4)))
    assert all(len(response.json()) == 1 for response in before)
    assert len({response.json()[0]["id"] for response in before}) == 1
    first = await post(runtime.client, "/strategy/recompute")
    second = await post(runtime.client, "/strategy/recompute")
    assert first["created_at"] == second["created_at"]
    assert (await runtime.client.get("/strategy/latest")).json()["id"] == second["id"]
    assert (await runtime.client.get("/topics/next")).json()[0]["strategy_id"] == second["id"]
