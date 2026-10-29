from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial
from uuid import UUID, uuid4, uuid5

from rancho_analytics.strategy import exploratory_bonuses, strategy_notes
from rancho_content.fixture import FixtureGenerator
from rancho_db.analytics import AnalyticsRepository
from rancho_db.database import Database
from rancho_db.models import (
    AgentRun,
    AuditLog,
    ContentBrief,
    ContentGenome,
    ContentIdea,
    ContentItem,
    ContentVariant,
    EffectLedger,
    MetricSnapshot,
    NextTopicCandidate,
    OutboxEvent,
    Owner,
    PlatformAccount,
    PlatformQualification,
    Publication,
    PublicationJob,
    ReviewApproval,
    StrategySnapshot,
    WorkflowRun,
)
from rancho_db.repositories import UnitOfWork
from rancho_platform_sdk.contracts import (
    ApprovedPayload,
    BeforeSendError,
    Capability,
    ExternalPlatformError,
    HumanInterventionRequiredError,
    MetricValues,
    PlatformAdapter,
    PublishMetadata,
    PublishRequest,
    PublishResult,
    RateLimitError,
    TerminalPlatformError,
)
from rancho_workflows.runner import LocalWorkflowRunner, WorkflowRunner

from rancho_core.domain import (
    ACCOUNT_ID,
    OWNER_ID,
    POSTIZ_ACCOUNT_ID,
    WEBSITE_ACCOUNT_ID,
    Clock,
    ContentStatus,
    DomainError,
    EffectStatus,
    JobStatus,
    content_transition,
    payload_hash,
    utc,
)
from rancho_core.schemas import (
    BriefView,
    ContentView,
    EditVariant,
    GenomeView,
    IdeaView,
    MetricSnapshotView,
    ObservationWindow,
    PublicationCreate,
    PublicationView,
    ReviewDecision,
    StrategyView,
    TopicView,
    TrendCreate,
    TrendView,
    VariantView,
)

logger = logging.getLogger("rancho.operations")


class MediaService:
    def __init__(
        self,
        database: Database,
        adapter: PlatformAdapter,
        clock: Clock,
        owner: UUID = OWNER_ID,
        runner: WorkflowRunner | None = None,
        platform: str = "mock",
        external_side_effects_enabled: bool = False,
        platform_enabled: bool = False,
        environment: str = "development",
    ) -> None:
        self.database, self.adapter, self.clock, self.owner = database, adapter, clock, owner
        if platform not in {"mock", "website", "postiz"}:
            raise ValueError("Unsupported platform")
        if environment not in {"development", "test", "staging", "production"}:
            raise ValueError("Unsupported environment")
        self.platform = platform
        self.environment = environment
        self.account_id = {
            "mock": ACCOUNT_ID,
            "website": WEBSITE_ACCOUNT_ID,
            "postiz": POSTIZ_ACCOUNT_ID,
        }[platform]
        self.external_side_effects_enabled = external_side_effects_enabled
        self.platform_enabled = platform_enabled
        self.analytics = AnalyticsRepository(database, owner)
        self.runner = runner or LocalWorkflowRunner(database, owner, clock)

    def work(self) -> UnitOfWork:
        return UnitOfWork(self.database, self.owner)

    async def seed(self) -> None:
        async with self.work() as work:
            await work.owners.insert_once(
                Owner(id=self.owner, created_at=self.clock.now(), name="Local fixture human"),
                ["id"],
            )
            await work.accounts.insert_once(
                PlatformAccount(
                    id=self.account_id,
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    platform=self.platform,
                    name=f"{self.platform} offline account",
                ),
                ["id"],
            )

    async def audit(
        self,
        work: UnitOfWork,
        action: str,
        entity_type: str,
        entity_id: UUID,
        actor: str = "SYSTEM",
        metadata: dict[str, str] | None = None,
    ) -> None:
        await work.audit.add(
            AuditLog(
                id=uuid4(),
                owner_id=self.owner,
                created_at=self.clock.now(),
                actor_type=actor,
                actor_id=self.owner,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                metadata_json=metadata or {},
            )
        )

    async def create_trend(self, request: TrendCreate) -> TrendView:
        from rancho_db.models import TrendItem

        async with self.work() as work:
            row = await work.trends.add(
                TrendItem(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    **request.model_dump(),
                )
            )
            await self.audit(work, "create_trend", "TrendItem", row.id, "HUMAN")
            return TrendView.model_validate(row)

    async def trends(self) -> list[TrendView]:
        async with self.work() as work:
            return [TrendView.model_validate(row) for row in await work.trends.list()]

    async def create_idea(self, trend_id: UUID) -> IdeaView:
        async with self.work() as work:
            trend = await work.trends.get(trend_id)
            strategy = await work.strategies.latest()
            row = await work.content.ideas.add(
                ContentIdea(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    trend_id=trend.id,
                    rationale="FIXTURE: explicit trend score plus exploratory strategy feedback",
                    strategy_id=strategy.id if strategy else None,
                )
            )
            await work.agents.add(
                AgentRun(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    role="TopicPlanner",
                    input_id=trend.id,
                    output_id=row.id,
                    tokens=None,
                    cost=None,
                )
            )
            return IdeaView.model_validate(row)

    async def create_brief(self, idea_id: UUID) -> BriefView:
        async with self.work() as work:
            idea = await work.content.ideas.get(idea_id)
            trend = await work.trends.get(idea.trend_id)
            row = await work.content.briefs.add(
                ContentBrief(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    idea_id=idea_id,
                    topic=trend.topic,
                    objective="Exercise a traceable, reviewed simulation pipeline",
                )
            )
            return BriefView.model_validate(row)

    def genome(self, topic: str, source: str, strategy: str | None) -> ContentGenome:
        return ContentGenome(
            id=uuid4(),
            owner_id=self.owner,
            created_at=self.clock.now(),
            topic=topic,
            subtopic="workflow",
            angle="method",
            hook_type="question",
            format="text",
            platform=self.platform,
            tone="plain",
            length_bucket="short",
            technical_depth="introductory",
            novelty="FIXTURE",
            cta_type="review",
            trend_source=source,
            prompt_version="fixture-v1",
            strategy_version=strategy,
            model="deterministic-fixture-v1",
            extra_metadata={"simulation": True},
        )

    async def generate(self, brief_id: UUID) -> ContentView:
        async def operation() -> ContentView:
            async with self.work() as work:
                brief = await work.content.briefs.get(brief_id)
                idea = await work.content.ideas.get(brief.idea_id)
                trend = await work.trends.get(idea.trend_id)
                generated = FixtureGenerator().generate(brief.topic, brief.objective)
                canonical_genome = await work.content.genomes.add(
                    self.genome(
                        brief.topic,
                        trend.source,
                        str(idea.strategy_id) if idea.strategy_id else None,
                    )
                )
                item = await work.content.items.add(
                    ContentItem(
                        id=uuid4(),
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        brief_id=brief_id,
                        genome_id=canonical_genome.id,
                        title=generated.title,
                        text=generated.text,
                    )
                )
                variant_genome = await work.content.genomes.add(
                    self.genome(brief.topic, trend.source, canonical_genome.strategy_version)
                )
                variant = await work.content.variants.add(
                    ContentVariant(
                        id=uuid4(),
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        content_id=item.id,
                        genome_id=variant_genome.id,
                        title=item.title,
                        text=item.text,
                        revision=1,
                        status="REQUIRES_REVIEW",
                    )
                )
                await work.agents.add(
                    AgentRun(
                        id=uuid4(),
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        role="Writer",
                        input_id=brief.id,
                        output_id=item.id,
                        tokens=None,
                        cost=None,
                    )
                )
                await self.audit(work, "generate", "ContentItem", item.id, "MOCK_AGENT")
                return await self.content_view(work, item, [variant])

        return await self.runner.execute("CreateContentWorkflow", brief_id, operation)

    async def variant_view(self, work: UnitOfWork, variant: ContentVariant) -> VariantView:
        genome = GenomeView.model_validate(await work.content.genomes.get(variant.genome_id))
        return VariantView.model_validate(
            {
                "id": variant.id,
                "content_id": variant.content_id,
                "genome_id": variant.genome_id,
                "revision": variant.revision,
                "title": variant.title,
                "text": variant.text,
                "status": variant.status,
                "genome": genome,
            }
        )

    async def content_view(
        self, work: UnitOfWork, item: ContentItem, variants: list[ContentVariant]
    ) -> ContentView:
        return ContentView(
            id=item.id,
            brief_id=item.brief_id,
            title=item.title,
            text=item.text,
            genome=GenomeView.model_validate(await work.content.genomes.get(item.genome_id)),
            variants=[await self.variant_view(work, variant) for variant in variants],
        )

    async def content(self, content_id: UUID | None = None) -> list[ContentView]:
        async with self.work() as work:
            items = (
                [await work.content.items.get(content_id)]
                if content_id
                else await work.content.items.list()
            )
            return [
                await self.content_view(
                    work,
                    item,
                    await work.content.variants.list(ContentVariant.content_id == item.id),
                )
                for item in items
            ]

    async def review(self, variant_id: UUID | None = None) -> list[VariantView]:
        async with self.work() as work:
            variants = (
                [await work.content.variants.get(variant_id)]
                if variant_id
                else await work.content.variants.list(ContentVariant.status == "REQUIRES_REVIEW")
            )
            return [await self.variant_view(work, variant) for variant in variants]

    async def approved_payload(
        self, work: UnitOfWork, variant: ContentVariant, account: UUID, scheduled_at: object
    ) -> ApprovedPayload:
        genome = await work.content.genomes.get(variant.genome_id)
        return ApprovedPayload.model_validate(
            {
                "account_id": account,
                "platform": self.platform,
                "content_variant_id": variant.id,
                "text": variant.text,
                "title": variant.title,
                "scheduled_at": scheduled_at,
                "topic": genome.topic,
            }
        )

    async def approve(self, variant_id: UUID, decision: ReviewDecision) -> VariantView:
        async with self.work() as work:
            variant = await work.content.variants.lock(variant_id)
            account = await work.accounts.get(decision.account_id)
            if account.platform != self.platform:
                raise DomainError("ACCOUNT_PLATFORM_MISMATCH", "Account is not for this adapter")
            if variant.status != "REQUIRES_REVIEW":
                raise DomainError("INVALID_STATE_TRANSITION", "Only pending review can be approved")
            payload = await self.approved_payload(
                work, variant, decision.account_id, utc(decision.scheduled_at)
            )
            await work.content.approvals.add(
                ReviewApproval(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    variant_id=variant_id,
                    account_id=decision.account_id,
                    scheduled_at=utc(decision.scheduled_at),
                    payload_hash=payload_hash(payload),
                    actor_id=self.owner,
                    revoked=False,
                )
            )
            content_transition(ContentStatus(variant.status), ContentStatus.APPROVED)
            variant.status = "APPROVED"
            await self.audit(
                work,
                "approve",
                "ContentVariant",
                variant_id,
                "HUMAN",
                {"payload_hash": payload_hash(payload)},
            )
            return await self.variant_view(work, variant)

    async def reject(self, variant_id: UUID) -> VariantView:
        async with self.work() as work:
            variant = await work.content.variants.lock(variant_id)
            if variant.status not in {"REQUIRES_REVIEW", "APPROVED"}:
                raise DomainError("INVALID_STATE_TRANSITION", "Revision cannot be rejected")
            approval = await work.content.approvals.find(ReviewApproval.variant_id == variant_id)
            if approval:
                approval.revoked = True
            content_transition(ContentStatus(variant.status), ContentStatus.REJECTED)
            variant.status = "REJECTED"
            await self.audit(work, "reject", "ContentVariant", variant_id, "HUMAN")
            return await self.variant_view(work, variant)

    async def edit(self, variant_id: UUID, request: EditVariant) -> VariantView:
        async with self.work() as work:
            previous = await work.content.variants.lock(variant_id)
            if previous.status == "SUPERSEDED":
                raise DomainError("INVALID_STATE_TRANSITION", "Edit latest revision")
            content_transition(ContentStatus(previous.status), ContentStatus.SUPERSEDED)
            previous.status = "SUPERSEDED"
            approval = await work.content.approvals.find(ReviewApproval.variant_id == variant_id)
            if approval:
                approval.revoked = True
            old_genome = await work.content.genomes.get(previous.genome_id)
            data = GenomeView.model_validate(old_genome).model_dump(exclude={"id"})
            genome = await work.content.genomes.add(
                ContentGenome(id=uuid4(), owner_id=self.owner, created_at=self.clock.now(), **data)
            )
            new = await work.content.variants.add(
                ContentVariant(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    content_id=previous.content_id,
                    genome_id=genome.id,
                    revision=previous.revision + 1,
                    status="REQUIRES_REVIEW",
                    **request.model_dump(),
                )
            )
            await self.audit(
                work, "edit", "ContentVariant", new.id, "HUMAN", {"previous_id": str(variant_id)}
            )
            return await self.variant_view(work, new)

    async def ensure_approval(
        self, work: UnitOfWork, variant: ContentVariant, account: UUID, payload: ApprovedPayload
    ) -> ReviewApproval:
        if variant.status != "APPROVED":
            raise DomainError("CONTENT_NOT_APPROVED", "Human approval of this revision required")
        approval = await work.content.approvals.find(ReviewApproval.variant_id == variant.id)
        if (
            approval is None
            or approval.revoked
            or approval.account_id != account
            or approval.payload_hash != payload_hash(payload)
        ):
            raise DomainError(
                "APPROVAL_MISMATCH", "Approval must match account, payload and schedule"
            )
        return approval

    async def prepare_job(self, request: PublicationCreate) -> UUID:
        async with self.work() as work:
            variant = await work.content.variants.lock(request.content_variant_id)
            account = await work.accounts.get(request.account_id)
            if account.platform != self.platform:
                raise DomainError("ACCOUNT_PLATFORM_MISMATCH", "Account is not for this adapter")
            payload = await self.approved_payload(
                work, variant, request.account_id, utc(request.scheduled_at)
            )
            existing = await work.jobs.find(
                PublicationJob.idempotency_key == request.idempotency_key
            )
            if existing:
                if existing.payload_hash != payload_hash(payload):
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT", "Same key has different approved payload"
                    )
                return existing.id
            approval = await self.ensure_approval(work, variant, request.account_id, payload)
            job_id = uuid4()
            external_request = PublishRequest(
                **payload.model_dump(),
                publication_job_id=job_id,
                idempotency_key=request.idempotency_key,
                metadata=PublishMetadata(
                    simulation=True, topic=payload.topic, failure_mode=request.failure_mode
                ),
            )
            inserted = await work.jobs.insert_once(
                PublicationJob(
                    id=job_id,
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    updated_at=self.clock.now(),
                    platform=self.platform,
                    account_id=request.account_id,
                    content_variant_id=variant.id,
                    approval_id=approval.id,
                    idempotency_key=request.idempotency_key,
                    payload_hash=payload_hash(payload),
                    request_payload=external_request.model_dump(mode="json"),
                    status="SCHEDULED",
                    attempt_count=0,
                    last_error=None,
                    retry_at=None,
                    lease_expires_at=None,
                    scheduled_at=utc(request.scheduled_at),
                ),
                ["owner_id", "idempotency_key"],
            )
            if not inserted:
                existing = await work.jobs.find(
                    PublicationJob.idempotency_key == request.idempotency_key
                )
                assert existing is not None
                if existing.payload_hash != payload_hash(payload):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Concurrent request changed payload")
                return existing.id
            await work.jobs.effects.add(
                EffectLedger(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    job_id=job_id,
                    status="PREPARED",
                    external_id=None,
                )
            )
            await work.jobs.outbox.add(
                OutboxEvent(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    job_id=job_id,
                    delivered=False,
                )
            )
            await work.workflows.add(
                WorkflowRun(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    name="PublishContentWorkflow",
                    entity_id=job_id,
                    status="WAITING",
                    finished_at=None,
                )
            )
            await self.audit(work, "schedule", "PublicationJob", job_id, "HUMAN")
            return job_id

    async def publish(self, request: PublicationCreate) -> PublicationView:
        try:
            job_id = await self.prepare_job(request)
        except DomainError as error:
            async with self.work() as work:
                await self.audit(
                    work,
                    "publish_blocked",
                    "ContentVariant",
                    request.content_variant_id,
                    "HUMAN",
                    {"code": error.code},
                )
            raise
        return await self.runner.execute(
            "PublishContentWorkflow", job_id, lambda: self.dispatch(job_id)
        )

    async def dispatch(self, job_id: UUID) -> PublicationView:
        should_send = False
        async with self.work() as work:
            initial = await work.jobs.get(job_id)
            variant = await work.content.variants.lock(initial.content_variant_id)
            job = await work.jobs.lock(job_id)
            request = PublishRequest.model_validate(job.request_payload)
            if job.platform != self.platform or request.platform != self.platform:
                raise DomainError(
                    "ADAPTER_PLATFORM_MISMATCH", "Job requires its configured adapter"
                )
            state = JobStatus(job.status)
            if state in {JobStatus.SCHEDULED, JobStatus.FAILED_RETRYABLE}:
                if job.scheduled_at > self.clock.now() or (
                    job.retry_at and job.retry_at > self.clock.now()
                ):
                    return await self.job_view(work, job)
                try:
                    await self.ensure_approval(
                        work, variant, job.account_id, request.approved_payload()
                    )
                except DomainError:
                    await work.jobs.change_state(job, JobStatus.CANCELLED, self.clock.now())
                    await work.jobs.change_effect(job_id, EffectStatus.FAILED)
                    await self.audit(work, "cancel_invalid_approval", "PublicationJob", job_id)
                    return await self.job_view(work, job)
                if self.platform != "mock":
                    qualification = await work.qualifications.find(
                        PlatformQualification.platform == self.platform
                    )
                    required = {
                        "development": "QUALIFIED_OFFLINE",
                        "test": "QUALIFIED_OFFLINE",
                        "staging": "QUALIFIED_STAGING",
                        "production": "QUALIFIED_LIVE",
                    }[self.environment]
                    levels = (
                        "UNQUALIFIED",
                        "QUALIFIED_OFFLINE",
                        "QUALIFIED_STAGING",
                        "QUALIFIED_LIVE",
                    )
                    if (
                        qualification is None
                        or levels.index(qualification.level) < levels.index(required)
                        or "publish" not in qualification.capabilities_json
                    ):
                        raise DomainError(
                            "PLATFORM_UNQUALIFIED", "Platform lacks required qualification"
                        )
                    if self.platform == "postiz" and not (
                        self.external_side_effects_enabled and self.platform_enabled
                    ):
                        raise DomainError(
                            "EXTERNAL_EFFECTS_DISABLED", "Postiz dispatch is disabled"
                        )
                self.adapter.capabilities.require(Capability.PUBLISH)
                should_send = await work.jobs.change_state(
                    job, JobStatus.DISPATCHING, self.clock.now()
                )
                if should_send:
                    job.attempt_count += 1
                    job.lease_expires_at = self.clock.now() + timedelta(seconds=30)
                    await work.jobs.change_effect(job_id, EffectStatus.IN_FLIGHT)
                    await self.audit(work, "publish_attempt", "PublicationJob", job_id)
            elif state not in {JobStatus.DISPATCHING, JobStatus.OUTCOME_UNKNOWN}:
                return await self.job_view(work, job)
        # No core write transaction is open here.
        if not should_send:
            self.adapter.capabilities.require(Capability.RECONCILE)
            result = await self.adapter.lookup_by_idempotency_key(
                request.account_id, request.idempotency_key
            )
            if result:
                return await self.confirm(job_id, result, "reconcile")
            async with self.work() as work:
                job = await work.jobs.lock(job_id)
                if (
                    job.status == "DISPATCHING"
                    and job.lease_expires_at
                    and job.lease_expires_at <= self.clock.now()
                ):
                    await work.jobs.change_state(job, JobStatus.OUTCOME_UNKNOWN, self.clock.now())
                    await work.jobs.change_effect(job_id, EffectStatus.UNKNOWN)
                    job.last_error = "EXPIRED_LEASE_UNKNOWN"
                    await self.audit(work, "outcome_unknown", "PublicationJob", job_id)
                return await self.job_view(work, job)
        try:
            result = await self.adapter.publish(request)
        except ExternalPlatformError as error:
            return await self.failed(job_id, error)
        return await self.confirm(job_id, result, "publish")

    async def reconcile(self, job_id: UUID) -> PublicationView:
        async with self.work() as work:
            job = await work.jobs.get(job_id)
            if job.status not in {"DISPATCHING", "OUTCOME_UNKNOWN"}:
                return await self.job_view(work, job)
        try:
            return await self.dispatch(job_id)
        except HumanInterventionRequiredError:
            async with self.work() as work:
                job = await work.jobs.lock(job_id)
                if job.status in {"DISPATCHING", "OUTCOME_UNKNOWN"}:
                    was_dispatching = job.status == "DISPATCHING"
                    await work.jobs.change_state(job, JobStatus.NEEDS_HUMAN, self.clock.now())
                    if was_dispatching:
                        await work.jobs.change_effect(job_id, EffectStatus.UNKNOWN)
                    job.last_error = "RECONCILIATION_NEEDS_HUMAN"
                    await self.audit(work, "reconcile_needs_human", "PublicationJob", job_id)
                return await self.job_view(work, job)

    async def failed(self, job_id: UUID, error: ExternalPlatformError) -> PublicationView:
        target, effect = JobStatus.OUTCOME_UNKNOWN, EffectStatus.UNKNOWN
        if isinstance(error, BeforeSendError) or (
            self.platform == "mock" and isinstance(error, RateLimitError)
        ):
            target, effect = JobStatus.FAILED_RETRYABLE, EffectStatus.FAILED
        elif isinstance(error, TerminalPlatformError):
            target, effect = JobStatus.FAILED_TERMINAL, EffectStatus.FAILED
        elif isinstance(error, HumanInterventionRequiredError):
            target, effect = JobStatus.NEEDS_HUMAN, EffectStatus.UNKNOWN
        async with self.work() as work:
            job = await work.jobs.lock(job_id)
            if job.status == "DISPATCHING":
                await work.jobs.change_state(job, target, self.clock.now())
                await work.jobs.change_effect(job_id, effect)
                job.last_error = type(
                    error
                ).__name__  # Never store arbitrary external error bodies.
                if isinstance(error, RateLimitError):
                    job.retry_at = self.clock.now() + timedelta(seconds=error.retry_after_seconds)
                await self.audit(
                    work,
                    "publish_result",
                    "PublicationJob",
                    job_id,
                    metadata={"status": target.value, "code": type(error).__name__},
                )
            return await self.job_view(work, job)

    async def confirm(self, job_id: UUID, result: PublishResult, operation: str) -> PublicationView:
        async with self.work() as work:
            job = await work.jobs.lock(job_id)
            if job.status != "PUBLISHED":
                await work.jobs.change_state(job, JobStatus.PUBLISHED, self.clock.now())
                await work.jobs.change_effect(job_id, EffectStatus.CONFIRMED, result.external_id)
                await work.jobs.publications.insert_once(
                    Publication(
                        id=uuid4(),
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        job_id=job_id,
                        account_id=job.account_id,
                        variant_id=job.content_variant_id,
                        platform=job.platform,
                        external_id=result.external_id,
                        external_url=result.external_url,
                        published_at=utc(result.published_at),
                        simulation=True,
                    ),
                    ["owner_id", "job_id"],
                )
                outbox = await work.jobs.outbox.find(OutboxEvent.job_id == job_id)
                assert outbox is not None
                outbox.delivered = True
                job.last_error, job.retry_at, job.lease_expires_at = None, None, None
                await self.audit(work, operation, "PublicationJob", job_id)
            view = await self.job_view(work, job)
            logger.info(
                "media_operation",
                extra={
                    "operation": operation,
                    "status": job.status,
                    "publication_job_id": str(job_id),
                    "publication_id": str(view.publication_id),
                    "run_id": str(job_id),
                    "platform": job.platform,
                    "adapter": type(self.adapter).__name__,
                },
            )
            return view

    async def job_view(self, work: UnitOfWork, job: PublicationJob) -> PublicationView:
        publication = await work.jobs.publications.find(Publication.job_id == job.id)
        outcomes = {
            "PUBLISHED": "SUCCEEDED",
            "OUTCOME_UNKNOWN": "OUTCOME_UNKNOWN",
            "NEEDS_HUMAN": "NEEDS_HUMAN",
            "FAILED_TERMINAL": "FAILED",
            "CANCELLED": "FAILED",
        }
        return PublicationView.model_validate(
            {
                "id": job.id,
                "status": job.status,
                "outcome": outcomes.get(job.status, "PENDING"),
                "attempt_count": job.attempt_count,
                "last_error": job.last_error,
                "publication_id": publication.id if publication else None,
                "external_id": publication.external_id if publication else None,
                "external_url": publication.external_url if publication else None,
                "published_at": publication.published_at if publication else None,
            }
        )

    async def publications(self, job_id: UUID | None = None) -> list[PublicationView]:
        async with self.work() as work:
            jobs = [await work.jobs.get(job_id)] if job_id else await work.jobs.list()
            return [await self.job_view(work, job) for job in jobs]

    async def persisted_publication(self, identifier: UUID) -> Publication:
        async with self.work() as work:
            publication = await work.jobs.publications.find(Publication.id == identifier)
            if publication is None:
                publication = await work.jobs.publications.find(Publication.job_id == identifier)
            if publication is None:
                raise DomainError(
                    "PUBLICATION_NOT_CONFIRMED", "Confirmed publication required", 404
                )
            return publication

    async def observe(self, identifier: UUID, age_hours: int) -> MetricSnapshotView:
        publication = await self.persisted_publication(identifier)

        async def operation() -> MetricSnapshotView:
            result = await self.adapter.fetch_metrics(publication.external_id, age_hours * 3600)
            async with self.work() as work:
                await work.metrics.insert_once(
                    MetricSnapshot(
                        id=uuid4(),
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        publication_id=publication.id,
                        captured_at=self.clock.now(),
                        simulated_at=utc(result.simulated_at),
                        post_age_seconds=result.post_age_seconds,
                        simulation=True,
                        **result.model_dump(include=set(MetricValues.model_fields)),
                    ),
                    ["owner_id", "publication_id", "post_age_seconds"],
                )
                row = await work.metrics.find(
                    MetricSnapshot.publication_id == publication.id,
                    MetricSnapshot.post_age_seconds == result.post_age_seconds,
                )
                assert row is not None
                return MetricSnapshotView.model_validate(row)

        # Each target age gets its own durable workflow identity.
        from uuid import uuid5

        return await self.runner.execute(
            "ObserveMetricsWorkflow", uuid5(publication.id, str(age_hours)), operation
        )

    async def recompute(self) -> StrategyView:
        strategy_id = uuid4()

        async def operation() -> StrategyView:
            facts, size, start, end = await self.analytics.facts(self.clock.now())
            view = StrategyView(
                id=strategy_id,
                created_at=self.clock.now(),
                sample_size=size,
                observation_window=ObservationWindow(start=start, end=end),
                top_topics=[fact.value for fact in facts if fact.dimension == "topic"],
                top_hook_types=[fact.value for fact in facts if fact.dimension == "hook_type"],
                top_formats=[fact.value for fact in facts if fact.dimension == "format"],
                metric_basis=(
                    "SQL latest snapshot per publication; strict engagement / views; simulated"
                ),
                quantitative_facts=facts,
                qualitative_notes=strategy_notes(size),
            )
            async with self.work() as work:
                await work.serialize_owner()
                previous = await work.strategies.latest()
                await work.strategies.add(
                    StrategySnapshot(
                        id=strategy_id,
                        owner_id=self.owner,
                        created_at=view.created_at,
                        sample_size=size,
                        supersedes_id=previous.id if previous else None,
                        observation_start=start,
                        observation_end=end,
                        result=view.model_dump(mode="json"),
                        status="PROPOSED",
                    )
                )
                await self.save_candidates(work, view)
                await self.audit(
                    work, "strategy_proposal", "StrategySnapshot", strategy_id, "MOCK_AGENT"
                )
            return view

        return await self.runner.execute("UpdateStrategyWorkflow", strategy_id, operation)

    async def latest_strategy(self) -> StrategyView | None:
        async with self.work() as work:
            row = await work.strategies.latest()
            return StrategyView.model_validate(row.result) if row else None

    async def save_candidates(self, work: UnitOfWork, strategy: StrategyView | None) -> None:
        bonuses = exploratory_bonuses(strategy.quantitative_facts) if strategy else {}
        for trend in await work.trends.list():
            bonus = bonuses.get(trend.topic, 0.0)
            await work.strategies.candidates.insert_once(
                NextTopicCandidate(
                    id=uuid5(
                        self.owner, f"candidate:{trend.id}:{strategy.id if strategy else 'INITIAL'}"
                    ),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    trend_id=trend.id,
                    strategy_id=strategy.id if strategy else None,
                    score=trend.score + bonus,
                    topic_bonus=bonus,
                ),
                ["id"],
            )

    async def topics(self) -> list[TopicView]:
        async with self.work() as work:
            await work.serialize_owner()
            strategy = await work.strategies.latest()
            rows = await work.strategies.candidates.list(
                NextTopicCandidate.strategy_id == strategy.id
                if strategy
                else NextTopicCandidate.strategy_id.is_(None)
            )
            # Add new trends to this candidate set; retain historical sets.
            trends = await work.trends.list()
            missing = [trend for trend in trends if trend.id not in {row.trend_id for row in rows}]
            bonuses = (
                exploratory_bonuses(StrategyView.model_validate(strategy.result).quantitative_facts)
                if strategy
                else {}
            )
            for trend in missing:
                bonus = bonuses.get(trend.topic, 0.0)
                candidate_id = uuid5(
                    self.owner, f"candidate:{trend.id}:{strategy.id if strategy else 'INITIAL'}"
                )
                await work.strategies.candidates.insert_once(
                    NextTopicCandidate(
                        id=candidate_id,
                        owner_id=self.owner,
                        created_at=self.clock.now(),
                        trend_id=trend.id,
                        strategy_id=strategy.id if strategy else None,
                        score=trend.score + bonus,
                        topic_bonus=bonus,
                    ),
                    ["id"],
                )
                rows.append(await work.strategies.candidates.get(candidate_id))
            topic_names = {trend.id: trend.topic for trend in trends}
            return sorted(
                [
                    TopicView(
                        id=row.id,
                        trend_id=row.trend_id,
                        topic=topic_names[row.trend_id],
                        strategy_id=row.strategy_id,
                        score=row.score,
                        topic_bonus=row.topic_bonus,
                    )
                    for row in rows
                ],
                key=lambda row: (-row.score, str(row.trend_id)),
            )

    async def drain(self) -> list[PublicationView]:
        async with self.work() as work:
            events = await work.jobs.outbox.list(OutboxEvent.delivered.is_(False))
            job_ids = [event.job_id for event in events]
        return [
            await self.runner.execute(
                "PublishContentWorkflow", job_id, partial(self.dispatch, job_id)
            )
            for job_id in job_ids
        ]
