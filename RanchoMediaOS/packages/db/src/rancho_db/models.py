from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from rancho_core.domain import DomainError, utc
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return utc(value) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else utc(value)


class Base(DeclarativeBase):
    pass


class MockBase(DeclarativeBase):
    pass


def fk(target: str, column: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["owner_id", column], [f"{target}.owner_id", f"{target}.id"])


def owned(*items: Any) -> tuple[Any, ...]:
    return (UniqueConstraint("owner_id", "id"), *items)


class Row:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class Owned(Row):
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("owners.id"), index=True)

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return owned()


class Owner(Row, Base):
    __tablename__ = "owners"
    name: Mapped[str] = mapped_column(String(80))


class PlatformAccount(Owned, Base):
    __tablename__ = "platform_accounts"
    platform: Mapped[str] = mapped_column(String(30), default="mock")
    name: Mapped[str] = mapped_column(String(100))
    __table_args__ = owned(
        CheckConstraint(
            "platform IN ('mock','website','postiz')", name="ck_platform_accounts_platform"
        )
    )


class PlatformQualification(Owned, Base):
    __tablename__ = "platform_qualifications"
    adapter: Mapped[str] = mapped_column(String(50))
    platform: Mapped[str] = mapped_column(String(30))
    level: Mapped[str] = mapped_column(String(30))
    qualified_at: Mapped[datetime] = mapped_column(UTCDateTime)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_TYPE)
    known_limitations_json: Mapped[list[str]] = mapped_column(JSON_TYPE)
    source_commit: Mapped[str] = mapped_column(String(64))
    test_report: Mapped[str] = mapped_column(String(300))
    __table_args__ = owned(
        UniqueConstraint("owner_id", "platform"),
        CheckConstraint(
            "level IN ('UNQUALIFIED','QUALIFIED_OFFLINE','QUALIFIED_STAGING','QUALIFIED_LIVE')"
        ),
    )


class TrendItem(Owned, Base):
    __tablename__ = "trend_items"
    topic: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(80))
    score: Mapped[float]
    __table_args__ = owned(CheckConstraint("score >= 0 AND score <= 1"))


class ContentIdea(Owned, Base):
    __tablename__ = "content_ideas"
    trend_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    rationale: Mapped[str]
    strategy_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    __table_args__ = owned(fk("trend_items", "trend_id"), fk("strategy_snapshots", "strategy_id"))


class ContentBrief(Owned, Base):
    __tablename__ = "content_briefs"
    idea_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    topic: Mapped[str]
    objective: Mapped[str]
    __table_args__ = owned(fk("content_ideas", "idea_id"))


class ContentGenome(Owned, Base):
    __tablename__ = "content_genomes"
    topic: Mapped[str] = mapped_column(String(200), index=True)
    subtopic: Mapped[str]
    angle: Mapped[str]
    hook_type: Mapped[str] = mapped_column(String(80), index=True)
    format: Mapped[str] = mapped_column(String(80), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    tone: Mapped[str]
    length_bucket: Mapped[str]
    technical_depth: Mapped[str]
    novelty: Mapped[str]
    cta_type: Mapped[str]
    trend_source: Mapped[str]
    prompt_version: Mapped[str]
    strategy_version: Mapped[str | None]
    model: Mapped[str]
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)


class ContentItem(Owned, Base):
    __tablename__ = "content_items"
    brief_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    genome_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str]
    text: Mapped[str]
    __table_args__ = owned(fk("content_briefs", "brief_id"), fk("content_genomes", "genome_id"))


class ContentVariant(Owned, Base):
    __tablename__ = "content_variants"
    content_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    genome_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    revision: Mapped[int]
    title: Mapped[str]
    text: Mapped[str]
    status: Mapped[str] = mapped_column(String(30))
    __table_args__ = owned(
        fk("content_items", "content_id"),
        fk("content_genomes", "genome_id"),
        UniqueConstraint("owner_id", "content_id", "revision"),
        CheckConstraint("revision >= 1"),
        CheckConstraint("status IN ('REQUIRES_REVIEW','APPROVED','REJECTED','SUPERSEDED')"),
    )


class ReviewApproval(Owned, Base):
    __tablename__ = "review_approvals"
    variant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    account_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    revoked: Mapped[bool] = mapped_column(default=False)
    __table_args__ = owned(
        fk("content_variants", "variant_id"),
        fk("platform_accounts", "account_id"),
        UniqueConstraint("owner_id", "variant_id"),
    )


class PublicationJob(Owned, Base):
    __tablename__ = "publication_jobs"
    idempotency_key: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(30))
    account_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    content_variant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    approval_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(30), index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None]
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = owned(
        fk("platform_accounts", "account_id"),
        fk("content_variants", "content_variant_id"),
        fk("review_approvals", "approval_id"),
        UniqueConstraint("owner_id", "idempotency_key"),
        CheckConstraint("attempt_count >= 0"),
        CheckConstraint(
            "status IN ('SCHEDULED','DISPATCHING','PUBLISHED','FAILED_RETRYABLE',"
            "'FAILED_TERMINAL','OUTCOME_UNKNOWN','NEEDS_HUMAN','CANCELLED')"
        ),
    )


class EffectLedger(Owned, Base):
    __tablename__ = "effect_ledgers"
    job_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str | None]
    __table_args__ = owned(
        fk("publication_jobs", "job_id"),
        UniqueConstraint("owner_id", "job_id"),
        CheckConstraint("status IN ('PREPARED','IN_FLIGHT','UNKNOWN','CONFIRMED','FAILED')"),
    )


class OutboxEvent(Owned, Base):
    __tablename__ = "outbox_events"
    job_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    delivered: Mapped[bool] = mapped_column(default=False)
    __table_args__ = owned(fk("publication_jobs", "job_id"), UniqueConstraint("owner_id", "job_id"))


class Publication(Owned, Base):
    __tablename__ = "publications"
    job_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    account_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    variant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    platform: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    external_url: Mapped[str]
    published_at: Mapped[datetime] = mapped_column(UTCDateTime)
    simulation: Mapped[bool] = mapped_column(default=True)
    __table_args__ = owned(
        fk("publication_jobs", "job_id"),
        fk("platform_accounts", "account_id"),
        fk("content_variants", "variant_id"),
        UniqueConstraint("owner_id", "job_id"),
        UniqueConstraint("owner_id", "account_id", "external_id"),
    )


class MetricColumns:
    views: Mapped[int | None]
    impressions: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    followers_gained: Mapped[int | None]
    watch_time: Mapped[float | None]
    avg_watch_time: Mapped[float | None]
    profile_visits: Mapped[int | None]
    link_clicks: Mapped[int | None]


class MetricSnapshot(MetricColumns, Owned, Base):
    __tablename__ = "metric_snapshots"
    publication_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    simulated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    post_age_seconds: Mapped[int]
    simulation: Mapped[bool] = mapped_column(default=True)
    __table_args__ = owned(
        fk("publications", "publication_id"),
        UniqueConstraint("owner_id", "publication_id", "post_age_seconds"),
        CheckConstraint("post_age_seconds >= 0"),
    )


class StrategySnapshot(Owned, Base):
    __tablename__ = "strategy_snapshots"
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    sample_size: Mapped[int]
    observation_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    observation_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(30), default="PROPOSED")
    __table_args__ = owned(
        fk("strategy_snapshots", "supersedes_id"), UniqueConstraint("owner_id", "supersedes_id")
    )


class NextTopicCandidate(Owned, Base):
    __tablename__ = "next_topic_candidates"
    trend_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    strategy_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    score: Mapped[float]
    topic_bonus: Mapped[float]
    __table_args__ = owned(fk("trend_items", "trend_id"), fk("strategy_snapshots", "strategy_id"))


class AgentRun(Owned, Base):
    __tablename__ = "agent_runs"
    role: Mapped[str]
    provenance: Mapped[str] = mapped_column(default="FIXTURE")
    input_id: Mapped[UUID] = mapped_column(Uuid)
    output_id: Mapped[UUID] = mapped_column(Uuid)
    model: Mapped[str] = mapped_column(default="deterministic-fixture-v1")
    tokens: Mapped[int | None]
    cost: Mapped[float | None]


class WorkflowRun(Owned, Base):
    __tablename__ = "workflow_runs"
    name: Mapped[str]
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str]
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = owned(UniqueConstraint("owner_id", "name", "entity_id"))


class AuditLog(Owned, Base):
    __tablename__ = "audit_logs"
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str]
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    __table_args__ = owned(CheckConstraint("actor_type IN ('SYSTEM','HUMAN','MOCK_AGENT')"))


class MockExternalPost(Row, MockBase):
    __tablename__ = "mock_external_posts"
    account_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    payload_hash: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str]
    title: Mapped[str]
    text: Mapped[str]
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)


class MockMetricState(MetricColumns, Row, MockBase):
    __tablename__ = "mock_metric_states"
    post_id: Mapped[UUID] = mapped_column(ForeignKey("mock_external_posts.id"), index=True)
    post_age_seconds: Mapped[int]
    simulated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("post_id", "post_age_seconds"),)


class MockComment(Row, MockBase):
    __tablename__ = "mock_comments"
    post_id: Mapped[UUID] = mapped_column(ForeignKey("mock_external_posts.id"), index=True)
    text: Mapped[str]


class MockReply(Row, MockBase):
    __tablename__ = "mock_replies"
    comment_id: Mapped[UUID] = mapped_column(ForeignKey("mock_comments.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    text: Mapped[str]
    __table_args__ = (UniqueConstraint("comment_id", "idempotency_key"),)


class MockFailure(Row, MockBase):
    __tablename__ = "mock_failures"
    account_id: Mapped[UUID] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str]
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)


# Index every composite FK in its actual column order (PostgreSQL does not create these).
for table in Base.metadata.tables.values():
    for constraint in table.foreign_key_constraints:
        names = [column.name for column in constraint.columns]
        if len(names) > 1:
            Index(f"ix_{table.name}_{'_'.join(names)}", *constraint.columns)


@event.listens_for(Session, "before_flush")
def immutable_records(session: Session, flush_context: Any, instances: Any) -> None:
    immutable = (ContentGenome, ContentItem, MetricSnapshot, StrategySnapshot, AuditLog, AgentRun)
    for obj in session.dirty | session.deleted:
        if isinstance(obj, immutable):
            raise DomainError("IMMUTABLE_RECORD", type(obj).__name__)
        if isinstance(obj, ContentVariant):
            from sqlalchemy import inspect

            state = inspect(obj)
            if obj in session.deleted or any(
                state.attrs[name].history.has_changes()
                for name in ("text", "title", "genome_id", "revision", "content_id", "owner_id")
            ):
                raise DomainError("IMMUTABLE_REVISION", "Create a new variant revision")
