from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue
from rancho_platform_sdk.contracts import FailureMode, MetricValues

from rancho_core.domain import ACCOUNT_ID, OWNER_ID, Schema


class HealthResponse(Schema):
    status: Literal["ok"] = "ok"
    simulation: Literal[True] = True
    owner_id: UUID = OWNER_ID
    account_id: UUID = ACCOUNT_ID
    database: Literal["sqlite_offline", "postgresql"]
    workflow_runner: Literal["in_process"] = "in_process"


class ErrorBody(Schema):
    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorResponse(Schema):
    error: ErrorBody


class TrendCreate(Schema):
    topic: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(default="manual_fixture", min_length=1, max_length=80)
    score: float = Field(default=0.5, ge=0, le=1)


class TrendView(TrendCreate):
    id: UUID
    created_at: AwareDatetime


class IdeaView(Schema):
    id: UUID
    trend_id: UUID
    rationale: str
    strategy_id: UUID | None


class BriefView(Schema):
    id: UUID
    idea_id: UUID
    topic: str
    objective: str


class GenomeView(Schema):
    id: UUID
    topic: str
    subtopic: str
    angle: str
    hook_type: str
    format: str
    platform: str
    tone: str
    length_bucket: str
    technical_depth: str
    novelty: str
    cta_type: str
    trend_source: str
    prompt_version: str
    strategy_version: str | None
    model: str
    extra_metadata: dict[str, JsonValue]


class VariantView(Schema):
    id: UUID
    content_id: UUID
    genome_id: UUID
    revision: int
    title: str
    text: str
    status: Literal["REQUIRES_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED"]
    genome: GenomeView


class ContentView(Schema):
    id: UUID
    brief_id: UUID
    title: str
    text: str
    genome: GenomeView
    variants: list[VariantView]


class ReviewDecision(Schema):
    account_id: UUID = ACCOUNT_ID
    scheduled_at: AwareDatetime


class EditVariant(Schema):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10000)


class PublicationCreate(Schema):
    content_variant_id: UUID
    account_id: UUID = ACCOUNT_ID
    idempotency_key: str = Field(min_length=1, max_length=120)
    scheduled_at: AwareDatetime
    failure_mode: FailureMode = FailureMode.NONE


class PublicationView(Schema):
    id: UUID
    status: str
    outcome: Literal["SUCCEEDED", "PENDING", "OUTCOME_UNKNOWN", "FAILED", "NEEDS_HUMAN"]
    attempt_count: int
    last_error: str | None
    publication_id: UUID | None = None
    external_id: str | None = None
    external_url: str | None = None
    published_at: AwareDatetime | None = None
    simulation: Literal[True] = True


class MetricsCreate(Schema):
    age_hours: int = Field(ge=0, le=720)


class MetricSnapshotView(MetricValues):
    id: UUID
    publication_id: UUID
    captured_at: AwareDatetime
    simulated_at: AwareDatetime
    post_age_seconds: int
    simulation: Literal[True] = True


class DerivedMetrics(Schema):
    engagement_rate: float | None
    like_rate: float | None
    comment_rate: float | None
    share_rate: float | None
    save_rate: float | None
    follow_conversion: float | None
    view_velocity: float | None
    engagement_velocity: float | None


class AnalyticsView(Schema):
    publication_id: UUID
    snapshot_id: UUID | None
    raw: MetricValues | None
    derived: DerivedMetrics
    baseline_status: Literal["INSUFFICIENT_BASELINE", "AVAILABLE"]
    baseline_sample_size: int
    baseline_mean_engagement: float | None
    percentile: float | None
    simulation: Literal[True] = True


class PerformanceFact(Schema):
    kind: Literal["FACT"] = "FACT"
    dimension: Literal["topic", "hook_type", "format"]
    value: str
    sample_size: int
    mean_engagement_rate: float | None
    snapshot_ids: list[UUID]


class StrategyNote(Schema):
    kind: Literal["HYPOTHESIS", "RECOMMENDATION"]
    text: str


class ObservationWindow(Schema):
    start: AwareDatetime | None
    end: AwareDatetime | None


class StrategyView(Schema):
    id: UUID
    created_at: AwareDatetime
    status: Literal["PROPOSED"] = "PROPOSED"
    observation_window: ObservationWindow
    sample_size: int
    top_topics: list[str]
    top_hook_types: list[str]
    top_formats: list[str]
    metric_basis: str
    quantitative_facts: list[PerformanceFact]
    qualitative_notes: list[StrategyNote]
    simulation: Literal[True] = True


class TopicView(Schema):
    id: UUID
    trend_id: UUID
    topic: str
    strategy_id: UUID | None
    score: float
    topic_bonus: float
    basis: Literal["EXPLORATORY_SIMULATION"] = "EXPLORATORY_SIMULATION"


class SummaryView(Schema):
    publication_count: int
    snapshot_count: int
    mean_engagement_rate: float | None
    simulation: Literal[True] = True
