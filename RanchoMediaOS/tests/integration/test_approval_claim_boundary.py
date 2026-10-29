"""Approval/review linearization at the persisted offline dispatch claim."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from rancho_core.domain import FrozenClock
from rancho_core.schemas import PublicationCreate
from rancho_db.models import MockExternalPost, ReviewApproval
from rancho_db.repositories import Repository
from rancho_platform_sdk.contracts import PublishRequest, PublishResult
from sqlalchemy import event

from tests.conftest import Runtime
from tests.helpers import approved, post, publication_request


@pytest.mark.parametrize("action", ["edit", "reject"])
async def test_review_cannot_commit_between_approval_check_and_claim(
    runtime: Runtime, clock: FrozenClock, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    variant = await approved(runtime.client, clock)
    request = publication_request(variant, clock, f"approval-claim-{action}")
    job_id = await runtime.service.prepare_job(PublicationCreate.model_validate(request))
    approval_checked, release_claim, review_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    ensure_approval = runtime.service.ensure_approval

    async def paused_check(*args: Any, **kwargs: Any) -> ReviewApproval:
        approval = await ensure_approval(*args, **kwargs)
        approval_checked.set()
        await release_claim.wait()
        return approval

    monkeypatch.setattr(runtime.service, "ensure_approval", paused_check)
    review_task: asyncio.Task[Any] | None = None

    def review_sql_started(*args: Any) -> None:
        # ASGI middleware may execute the review handler in a child task.
        # Dispatch is paused, so only the concurrent review can issue core SQL here.
        if approval_checked.is_set() and not release_claim.is_set():
            review_started.set()

    engine = runtime.service.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", review_sql_started)
    dispatch_task = asyncio.create_task(runtime.service.dispatch(job_id))
    review_was_blocked = False
    try:
        await asyncio.wait_for(approval_checked.wait(), timeout=5)
        body = {"title": "Edited fixture", "text": "Requires a new human approval"}
        review_task = asyncio.create_task(
            post(
                runtime.client,
                f"/review/{variant['id']}/{action}",
                body if action == "edit" else None,
            )
        )
        # Observe actual SQL in the independent review transaction before checking blocking.
        await asyncio.wait_for(review_started.wait(), timeout=5)
        try:
            await asyncio.wait_for(asyncio.shield(review_task), timeout=0.5)
        except TimeoutError:
            review_was_blocked = True
    finally:
        release_claim.set()
        results = await asyncio.gather(
            dispatch_task, *([review_task] if review_task is not None else [])
        )
        event.remove(engine, "before_cursor_execute", review_sql_started)

    sent, reviewed = results
    external_count = await runtime.adapter.external_post_count()
    assert review_was_blocked, (
        f"{action} committed before the approved send was claimed; "
        f"job={sent.status}, external_posts={external_count}"
    )
    # A review change after the committed claim cannot undo that immutable send.
    assert sent.status == "PUBLISHED" and sent.attempt_count == 1
    assert external_count == 1
    async with runtime.adapter.database.sessions() as session:
        external = await Repository(session, MockExternalPost, None).get(UUID(sent.external_id))
        assert external.title == variant["title"] and external.text == variant["text"]
    async with runtime.service.work() as work:
        approval = await work.content.approvals.find(
            ReviewApproval.variant_id == UUID(variant["id"])
        )
        assert approval is not None and approval.revoked
        assert await work.jobs.count() == await work.jobs.publications.count() == 1
    if action == "edit":
        assert reviewed["status"] == "REQUIRES_REVIEW" and reviewed["revision"] == 2
        blocked = await runtime.client.post(
            "/publications", json=publication_request(reviewed, clock, "edited-unapproved")
        )
        assert blocked.status_code == 409
        assert await runtime.adapter.external_post_count() == 1


async def test_edit_committed_before_claim_cancels_without_sending(
    runtime: Runtime, clock: FrozenClock
) -> None:
    variant = await approved(runtime.client, clock)
    request = PublicationCreate.model_validate(publication_request(variant, clock, "edit-first"))
    job_id = await runtime.service.prepare_job(request)
    edited = await post(
        runtime.client,
        f"/review/{variant['id']}/edit",
        {"title": "New fixture revision", "text": "The old approval is no longer usable"},
    )
    cancelled = await runtime.service.dispatch(job_id)
    assert cancelled.status == "CANCELLED" and cancelled.attempt_count == 0
    assert (await runtime.service.publish(request)).status == "CANCELLED"
    blocked = await runtime.client.post(
        "/publications", json=publication_request(edited, clock, "new-revision")
    )
    assert blocked.status_code == 409
    assert await runtime.adapter.external_post_count() == 0


async def test_claim_lock_is_released_before_external_send(
    runtime: Runtime, clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = await approved(runtime.client, clock)
    request = PublicationCreate.model_validate(publication_request(variant, clock, "claim-only"))
    job_id = await runtime.service.prepare_job(request)
    send_started, release_send = asyncio.Event(), asyncio.Event()
    publish = runtime.adapter.publish

    async def paused_send(request: PublishRequest) -> PublishResult:
        send_started.set()
        await release_send.wait()
        return await publish(request)

    monkeypatch.setattr(runtime.adapter, "publish", paused_send)
    dispatch_task = asyncio.create_task(runtime.service.dispatch(job_id))
    try:
        await asyncio.wait_for(send_started.wait(), timeout=5)
        # The core claim is committed even while the independent mock call is in flight.
        edited = await asyncio.wait_for(
            post(
                runtime.client,
                f"/review/{variant['id']}/edit",
                {"title": "After claim", "text": "This revision still requires review"},
            ),
            timeout=5,
        )
        assert edited["status"] == "REQUIRES_REVIEW"
        async with runtime.service.work() as work:
            job = await work.jobs.get(job_id)
            assert job.status == "DISPATCHING" and job.attempt_count == 1
    finally:
        release_send.set()
        result = await dispatch_task
    assert result.status == "PUBLISHED" and result.attempt_count == 1
    assert await runtime.adapter.external_post_count() == 1
