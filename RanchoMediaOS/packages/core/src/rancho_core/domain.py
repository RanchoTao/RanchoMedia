from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

OWNER_ID = UUID("86a938f2-a763-4a0c-8082-155426b59091")
ACCOUNT_ID = UUID("c768dc31-8dac-4a3b-957b-d2e3ff6f3adf")
WEBSITE_ACCOUNT_ID = UUID("f98a8ed4-0994-40d9-9d36-fc985f3ad30a")
POSTIZ_ACCOUNT_ID = UUID("0df10e69-1690-4fa2-ae88-7287a1d2dba7")


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, value: datetime) -> None:
        self.value = utc(value)

    def now(self) -> datetime:
        return self.value


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(UTC)


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class JobStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    DISPATCHING = "DISPATCHING"
    PUBLISHED = "PUBLISHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    CANCELLED = "CANCELLED"


JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.SCHEDULED: frozenset({JobStatus.DISPATCHING, JobStatus.CANCELLED}),
    JobStatus.DISPATCHING: frozenset(
        {
            JobStatus.PUBLISHED,
            JobStatus.FAILED_RETRYABLE,
            JobStatus.FAILED_TERMINAL,
            JobStatus.OUTCOME_UNKNOWN,
            JobStatus.NEEDS_HUMAN,
        }
    ),
    JobStatus.FAILED_RETRYABLE: frozenset({JobStatus.DISPATCHING, JobStatus.CANCELLED}),
    JobStatus.OUTCOME_UNKNOWN: frozenset({JobStatus.PUBLISHED, JobStatus.NEEDS_HUMAN}),
    JobStatus.NEEDS_HUMAN: frozenset(),
    JobStatus.FAILED_TERMINAL: frozenset(),
    JobStatus.PUBLISHED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class ContentStatus(StrEnum):
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


CONTENT_TRANSITIONS: dict[ContentStatus, frozenset[ContentStatus]] = {
    ContentStatus.REQUIRES_REVIEW: frozenset(
        {ContentStatus.APPROVED, ContentStatus.REJECTED, ContentStatus.SUPERSEDED}
    ),
    ContentStatus.APPROVED: frozenset({ContentStatus.REJECTED, ContentStatus.SUPERSEDED}),
    ContentStatus.REJECTED: frozenset({ContentStatus.SUPERSEDED}),
    ContentStatus.SUPERSEDED: frozenset(),
}


def content_transition(current: ContentStatus, target: ContentStatus) -> None:
    if target not in CONTENT_TRANSITIONS[current]:
        raise DomainError("INVALID_STATE_TRANSITION", f"{current} -> {target}")


class EffectStatus(StrEnum):
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


EFFECT_TRANSITIONS: dict[EffectStatus, frozenset[EffectStatus]] = {
    EffectStatus.PREPARED: frozenset({EffectStatus.IN_FLIGHT, EffectStatus.FAILED}),
    EffectStatus.IN_FLIGHT: frozenset(
        {EffectStatus.CONFIRMED, EffectStatus.UNKNOWN, EffectStatus.FAILED}
    ),
    EffectStatus.UNKNOWN: frozenset({EffectStatus.CONFIRMED}),
    EffectStatus.FAILED: frozenset({EffectStatus.IN_FLIGHT}),
    EffectStatus.CONFIRMED: frozenset(),
}


def transition(current: JobStatus, target: JobStatus) -> None:
    if target not in JOB_TRANSITIONS[current]:
        raise DomainError("INVALID_STATE_TRANSITION", f"{current} -> {target}")


def effect_transition(current: EffectStatus, target: EffectStatus) -> None:
    if target not in EFFECT_TRANSITIONS[current]:
        raise DomainError("INVALID_EFFECT_TRANSITION", f"{current} -> {target}")


def payload_hash(payload: BaseModel) -> str:
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator
