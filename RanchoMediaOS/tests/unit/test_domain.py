import logging
from datetime import datetime

import pytest
from pydantic import ValidationError
from rancho_core.domain import (
    ContentStatus,
    DomainError,
    EffectStatus,
    JobStatus,
    content_transition,
    effect_transition,
    ratio,
    transition,
    utc,
)
from rancho_core.logging import SafeJsonFormatter
from rancho_core.settings import Settings


@pytest.mark.parametrize(
    ("state", "target"),
    [
        (JobStatus.SCHEDULED, JobStatus.DISPATCHING),
        (JobStatus.DISPATCHING, JobStatus.OUTCOME_UNKNOWN),
        (JobStatus.OUTCOME_UNKNOWN, JobStatus.PUBLISHED),
    ],
)
def test_job_transitions(state: JobStatus, target: JobStatus) -> None:
    transition(state, target)


@pytest.mark.parametrize(
    ("state", "target"),
    [
        (JobStatus.PUBLISHED, JobStatus.DISPATCHING),
        (JobStatus.OUTCOME_UNKNOWN, JobStatus.DISPATCHING),
        (JobStatus.SCHEDULED, JobStatus.PUBLISHED),
    ],
)
def test_illegal_job_transitions(state: JobStatus, target: JobStatus) -> None:
    with pytest.raises(DomainError, match="->"):
        transition(state, target)


def test_content_transition_guard() -> None:
    content_transition(ContentStatus.REQUIRES_REVIEW, ContentStatus.APPROVED)
    content_transition(ContentStatus.APPROVED, ContentStatus.SUPERSEDED)
    with pytest.raises(DomainError):
        content_transition(ContentStatus.SUPERSEDED, ContentStatus.APPROVED)


def test_effect_never_unknown_to_retry() -> None:
    with pytest.raises(DomainError):
        effect_transition(EffectStatus.UNKNOWN, EffectStatus.IN_FLIGHT)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(None, 1, None), (1, None, None), (1, 0, None), (1, -1, None), (0, 10, 0), (2, 10, 0.2)],
)
def test_null_and_zero_ratios(a: float | None, b: float | None, expected: float | None) -> None:
    assert ratio(a, b) == expected


def test_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        utc(datetime(2026, 9, 28))


def test_no_env_file_required_and_auto_approval_blocked() -> None:
    assert Settings().auto_approve_enabled is False
    with pytest.raises(ValidationError):
        Settings(auto_approve_enabled=True)
    with pytest.raises(ValidationError):
        Settings(app_env="production")


def test_logs_drop_sensitive_message_fields() -> None:
    marker = "SYNTHETIC_PRIVATE_MARKER"
    record = logging.LogRecord("rancho", logging.INFO, "example", 1, marker, (), None)
    record.authorization = marker
    record.token = marker
    record.cookie = marker
    result = SafeJsonFormatter().format(record)
    assert marker not in result
    for field in (
        "request_id",
        "run_id",
        "publication_job_id",
        "publication_id",
        "platform",
        "adapter",
        "operation",
        "status",
    ):
        assert field in result
