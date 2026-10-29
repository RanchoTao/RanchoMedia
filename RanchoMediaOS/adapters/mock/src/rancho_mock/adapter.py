from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from rancho_core.domain import Clock, DomainError, payload_hash
from rancho_db.database import Database
from rancho_db.models import MockComment, MockExternalPost, MockFailure, MockMetricState, MockReply
from rancho_db.repositories import Repository
from rancho_platform_sdk.contracts import (
    BeforeSendError,
    Capability,
    Comment,
    ExternalOutcomeUnknownError,
    FailureMode,
    HumanInterventionRequiredError,
    MetricsResult,
    MetricValues,
    PlatformAdapter,
    PlatformCapabilities,
    PublishRequest,
    PublishResult,
    RateLimitError,
    ReplyResult,
    ResultMetadata,
    TerminalPlatformError,
)
from sqlalchemy import select

MOCK_NAMESPACE = UUID("6db3e211-5f35-4d67-ad63-e8bd47ae5ef1")


class PersistentMockAdapter(PlatformAdapter):
    """Own database; platform commits survive lost responses and process restarts."""

    def __init__(self, database: Database, clock: Clock, seed: int = 7) -> None:
        self.database, self.clock, self.seed = database, clock, seed

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            supported=frozenset(
                {
                    Capability.AUTHENTICATE,
                    Capability.HEALTH,
                    Capability.PUBLISH,
                    Capability.FETCH_POST,
                    Capability.METRICS,
                    Capability.COMMENTS,
                    Capability.REPLY,
                    Capability.RECONCILE,
                }
            ),
            native_idempotency=True,
        )

    async def authenticate(self) -> bool:
        return True  # Fixture identity only; no credential exists.

    async def health_check(self) -> bool:
        async with self.database.sessions() as session:
            await session.execute(select(MockExternalPost.id).limit(1))
        return True

    def result(self, post: MockExternalPost) -> PublishResult:
        return PublishResult(
            external_id=str(post.id),
            external_url=f"mock://posts/{post.id}",
            published_at=post.created_at,
            raw_metadata=ResultMetadata(simulation=True, adapter="persistent_mock"),
        )

    async def lookup_by_idempotency_key(self, account: UUID, key: str) -> PublishResult | None:
        async with self.database.sessions() as session:
            post = await Repository(session, MockExternalPost, None).find(
                MockExternalPost.account_id == account, MockExternalPost.idempotency_key == key
            )
            return self.result(post) if post else None

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.capabilities.require(Capability.PUBLISH)
        digest = payload_hash(request.approved_payload())
        failure = request.metadata.failure_mode
        consumed = False
        # Persist one-shot fault consumption separately, even for before-send errors.
        if failure != FailureMode.NONE:
            async with self.database.sessions.begin() as session:
                consumed = await Repository(session, MockFailure, None).insert_once(
                    MockFailure(
                        id=uuid4(),
                        created_at=self.clock.now(),
                        account_id=request.account_id,
                        idempotency_key=request.idempotency_key,
                        mode=failure.value,
                    ),
                    ["account_id", "idempotency_key"],
                )
        existing = await self.lookup_by_idempotency_key(request.account_id, request.idempotency_key)
        if existing is None and consumed:
            if failure == FailureMode.FAIL_BEFORE_SEND:
                raise BeforeSendError("Mock refused before send")
            if failure == FailureMode.RATE_LIMIT:
                raise RateLimitError()
            if failure == FailureMode.TERMINAL_ERROR:
                raise TerminalPlatformError("Mock terminal rejection")
            if failure == FailureMode.HUMAN_REQUIRED:
                raise HumanInterventionRequiredError("Mock requires human review")
        post_id = uuid5(MOCK_NAMESPACE, f"{request.account_id}:{request.idempotency_key}")
        async with self.database.sessions.begin() as session:
            repository = Repository(session, MockExternalPost, None)
            inserted = await repository.insert_once(
                MockExternalPost(
                    id=post_id,
                    created_at=self.clock.now(),
                    account_id=request.account_id,
                    idempotency_key=request.idempotency_key,
                    payload_hash=digest,
                    topic=request.topic,
                    title=request.title,
                    text=request.text,
                ),
                ["account_id", "idempotency_key"],
            )
            post = await repository.get(post_id)
            if post.payload_hash != digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Same key has a different payload")
            if inserted:
                await Repository(session, MockComment, None).add(
                    MockComment(
                        id=uuid5(post_id, "comment:1"),
                        created_at=self.clock.now(),
                        post_id=post_id,
                        text="SIMULATED: Could you explain the method?",
                    )
                )
            result = self.result(post)
        # The external transaction has really committed before this exception.
        if consumed and failure == FailureMode.FAIL_AFTER_EXTERNAL_SUCCESS and existing is None:
            raise ExternalOutcomeUnknownError("Mock committed; response lost")
        return result

    async def fetch_post(self, external_id: str) -> PublishResult:
        async with self.database.sessions() as session:
            return self.result(
                await Repository(session, MockExternalPost, None).get(UUID(external_id))
            )

    def metric_values(self, post: MockExternalPost, age: int) -> MetricValues:
        # Stable seed + stable publication id + explicit age, no process RNG or wall-time reads.
        number = int(hashlib.sha256(f"{self.seed}:{post.id}".encode()).hexdigest()[:8], 16)
        views = (100 + number % 100) * age // 3600
        quality = 5 + int(hashlib.sha256(post.topic.encode()).hexdigest()[:2], 16) % 8
        return MetricValues(
            views=views,
            impressions=views * 2,
            likes=views * quality // 100,
            comments=views // 50,
            shares=views // 100,
            saves=views // 80,
            followers_gained=views // 200,
            watch_time=None,
            avg_watch_time=None,
            profile_visits=views // 40,
            link_clicks=views // 100,
        )

    async def fetch_metrics(self, external_id: str, age_seconds: int) -> MetricsResult:
        if age_seconds < 0:
            raise ValueError("Negative age")
        post_id = UUID(external_id)
        async with self.database.sessions.begin() as session:
            post = await Repository(session, MockExternalPost, None).get(post_id)
            values = self.metric_values(post, age_seconds)
            repo = Repository(session, MockMetricState, None)
            await repo.insert_once(
                MockMetricState(
                    id=uuid5(post_id, f"metrics:{age_seconds}:{self.seed}"),
                    created_at=self.clock.now(),
                    post_id=post_id,
                    post_age_seconds=age_seconds,
                    simulated_at=post.created_at + timedelta(seconds=age_seconds),
                    **values.model_dump(),
                ),
                ["post_id", "post_age_seconds"],
            )
            row = await repo.find(
                MockMetricState.post_id == post_id, MockMetricState.post_age_seconds == age_seconds
            )
            assert row is not None
            return MetricsResult(
                external_id=external_id,
                post_age_seconds=age_seconds,
                simulated_at=row.simulated_at,
                simulation=True,
                **MetricValues.model_validate(row).model_dump(),
            )

    async def fetch_comments(self, external_id: str) -> list[Comment]:
        async with self.database.sessions() as session:
            await Repository(session, MockExternalPost, None).get(UUID(external_id))
            rows = await Repository(session, MockComment, None).list(
                MockComment.post_id == UUID(external_id)
            )
            return [Comment(id=row.id, external_id=external_id, text=row.text) for row in rows]

    async def reply_comment(self, comment_id: UUID, text: str, key: str) -> ReplyResult:
        async with self.database.sessions.begin() as session:
            await Repository(session, MockComment, None).get(comment_id)
            repo = Repository(session, MockReply, None)
            row_id = uuid5(comment_id, key)
            await repo.insert_once(
                MockReply(
                    id=row_id,
                    created_at=self.clock.now(),
                    comment_id=comment_id,
                    idempotency_key=key,
                    text=text,
                ),
                ["comment_id", "idempotency_key"],
            )
            row = await repo.get(row_id)
            if row.text != text:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Reply key has different payload")
            return ReplyResult.model_validate(row)

    async def external_post_count(self) -> int:
        async with self.database.sessions() as session:
            return await Repository(session, MockExternalPost, None).count()
