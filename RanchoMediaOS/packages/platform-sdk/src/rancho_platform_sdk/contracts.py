from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field
from rancho_core.domain import Schema


class UnsupportedCapabilityError(Exception):
    pass


class AuthenticationRequiredError(Exception):
    pass


class ExternalPlatformError(Exception):
    pass


class ExternalOutcomeUnknownError(ExternalPlatformError):
    pass


class RateLimitError(ExternalPlatformError):
    def __init__(self, retry_after_seconds: int = 60) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Rate limited")


class HumanInterventionRequiredError(ExternalPlatformError):
    pass


class BeforeSendError(ExternalPlatformError):
    """Explicitly proves no external request was sent."""


class TerminalPlatformError(ExternalPlatformError):
    pass


class Capability(StrEnum):
    AUTHENTICATE = "authenticate"
    HEALTH = "health"
    PUBLISH = "publish"
    SCHEDULE = "schedule"
    DELETE = "delete"
    UPDATE = "update"
    FETCH_POST = "fetch_post"
    METRICS = "metrics"
    COMMENTS = "comments"
    REPLY = "reply"
    SEARCH = "search"
    TRENDS = "trends"
    RECONCILE = "reconcile"


class PlatformCapabilities(Schema):
    supported: frozenset[Capability]
    native_idempotency: bool = False

    def require(self, operation: Capability) -> None:
        if operation not in self.supported:
            raise UnsupportedCapabilityError(operation.value)


class FailureMode(StrEnum):
    NONE = "NONE"
    FAIL_BEFORE_SEND = "FAIL_BEFORE_SEND"
    FAIL_AFTER_EXTERNAL_SUCCESS = "FAIL_AFTER_EXTERNAL_SUCCESS"
    RATE_LIMIT = "RATE_LIMIT"
    TERMINAL_ERROR = "TERMINAL_ERROR"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class PublishMetadata(Schema):
    simulation: bool = False
    topic: str
    failure_mode: FailureMode = FailureMode.NONE


class ApprovedPayload(Schema):
    platform: str = Field(default="mock", min_length=1, max_length=30)
    account_id: UUID
    content_variant_id: UUID
    text: str
    title: str
    media_assets: tuple[str, ...] = ()
    scheduled_at: AwareDatetime
    topic: str


class PublishRequest(ApprovedPayload):
    publication_job_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=120)
    metadata: PublishMetadata

    def approved_payload(self) -> ApprovedPayload:
        return ApprovedPayload.model_validate(
            self.model_dump(exclude={"publication_job_id", "idempotency_key", "metadata"})
        )


class ResultMetadata(Schema):
    simulation: bool = False
    adapter: str


class PublishResult(Schema):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    external_id: str
    external_url: str
    published_at: AwareDatetime
    raw_metadata: ResultMetadata


class MetricValues(Schema):
    views: int | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    followers_gained: int | None = Field(default=None, ge=0)
    watch_time: float | None = Field(default=None, ge=0)
    avg_watch_time: float | None = Field(default=None, ge=0)
    profile_visits: int | None = Field(default=None, ge=0)
    link_clicks: int | None = Field(default=None, ge=0)


class MetricsResult(MetricValues):
    external_id: str
    post_age_seconds: int = Field(ge=0)
    simulated_at: AwareDatetime
    simulation: bool = False


class Comment(Schema):
    id: UUID
    external_id: str
    text: str


class ReplyResult(Schema):
    id: UUID
    comment_id: UUID
    text: str


class PlatformAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities: ...

    async def authenticate(self) -> bool:
        raise UnsupportedCapabilityError("authenticate")

    async def health_check(self) -> bool:
        raise UnsupportedCapabilityError("health")

    async def publish(self, request: PublishRequest) -> PublishResult:
        raise UnsupportedCapabilityError("publish")

    async def lookup_by_idempotency_key(self, account: UUID, key: str) -> PublishResult | None:
        raise UnsupportedCapabilityError("reconcile")

    async def fetch_post(self, external_id: str) -> PublishResult:
        raise UnsupportedCapabilityError("fetch_post")

    async def fetch_metrics(self, external_id: str, age_seconds: int) -> MetricsResult:
        raise UnsupportedCapabilityError("metrics")

    async def fetch_comments(self, external_id: str) -> list[Comment]:
        raise UnsupportedCapabilityError("comments")

    async def reply_comment(self, comment_id: UUID, text: str, key: str) -> ReplyResult:
        raise UnsupportedCapabilityError("reply")

    async def schedule(self, request: PublishRequest) -> PublishResult:
        raise UnsupportedCapabilityError("schedule")

    async def delete(self, external_id: str) -> None:
        raise UnsupportedCapabilityError("delete")

    async def update(self, external_id: str, text: str) -> PublishResult:
        raise UnsupportedCapabilityError("update")

    async def search(self, query: str) -> list[PublishResult]:
        raise UnsupportedCapabilityError("search")

    async def trends(self, since: datetime) -> list[str]:
        raise UnsupportedCapabilityError("trends")
