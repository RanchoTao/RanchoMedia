from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from rancho_core.domain import WEBSITE_ACCOUNT_ID, DomainError, FrozenClock
from rancho_core.schemas import PublicationCreate, ReviewDecision, TrendCreate
from rancho_core.service import MediaService
from rancho_db.database import Database
from rancho_db.models import PlatformQualification
from rancho_platform_sdk.contracts import (
    BeforeSendError,
    ExternalOutcomeUnknownError,
    HumanInterventionRequiredError,
    PublishMetadata,
    PublishRequest,
)
from rancho_website.adapter import WebsiteAdapter


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def website_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "site"
    repo.mkdir()
    git(repo, "init")
    (repo / "README.md").write_text("Site\n", encoding="utf-8")
    git(repo, "-c", "core.autocrlf=false", "add", "README.md")
    git(
        repo,
        "-c",
        "core.autocrlf=false",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "initial",
    )
    return repo


async def approved_job(service: MediaService, key: str) -> PublicationCreate:
    trend = await service.create_trend(TrendCreate(topic="Local", title="Local test"))
    idea = await service.create_idea(trend.id)
    brief = await service.create_brief(idea.id)
    content = await service.generate(brief.id)
    variant = content.variants[0]
    await service.approve(
        variant.id,
        ReviewDecision(account_id=WEBSITE_ACCOUNT_ID, scheduled_at=service.clock.now()),
    )
    return PublicationCreate(
        content_variant_id=variant.id,
        account_id=WEBSITE_ACCOUNT_ID,
        idempotency_key=key,
        scheduled_at=service.clock.now(),
    )


@pytest.mark.parametrize("format", ["md", "mdx", "json"])
async def test_website_real_git_approval_idempotency_and_recovery(
    tmp_path: Path, format: str
) -> None:
    repo = website_repo(tmp_path)
    adapter = WebsiteAdapter(repo, format=format)
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{(tmp_path / 'core.db').as_posix()}"
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'core.db').as_posix()}")
    clock = FrozenClock(datetime(2026, 10, 15, tzinfo=UTC))
    service = MediaService(database, adapter, clock, platform="website")
    try:
        await service.seed()
        first = await approved_job(service, "website-first")
        with pytest.raises(DomainError) as blocked:
            await service.publish(first)
        assert blocked.value.code == "PLATFORM_UNQUALIFIED"
        assert git(repo, "rev-list", "--count", "HEAD") == "1"
        async with service.work() as work:
            await work.qualifications.add(
                PlatformQualification(
                    id=uuid4(),
                    owner_id=service.owner,
                    created_at=clock.now(),
                    qualified_at=clock.now(),
                    adapter="WebsiteAdapter",
                    platform="website",
                    level="QUALIFIED_OFFLINE",
                    capabilities_json=["publish", "fetch_post", "reconcile"],
                    known_limitations_json=["no remote push", "update/delete not qualified"],
                    source_commit="local",
                    test_report="tests/integration/test_website_adapter.py",
                )
            )
        views = [await service.publish(first)]
        views.extend(await asyncio.gather(*(service.publish(first) for _ in range(4))))
        assert len({view.id for view in views}) == 1
        settled = (await service.publications(views[0].id))[0]
        assert settled.outcome == "SUCCEEDED"
        assert git(repo, "rev-list", "--count", "HEAD") == "2"
        target = repo / "content" / "rancho" / f"{first.content_variant_id}.{format}"
        assert target.exists()
        assert "approved_payload_sha256" in target.read_text(encoding="utf-8")
        assert (await adapter.fetch_post(settled.external_id)).external_id == settled.external_id
        conflict = await approved_job(service, "website-first")
        with pytest.raises(DomainError) as conflict_error:
            await service.publish(conflict)
        assert conflict_error.value.code == "IDEMPOTENCY_CONFLICT"

        production = MediaService(
            database, adapter, clock, platform="website", environment="production"
        )
        unqualified_live = await approved_job(service, "website-needs-live")
        with pytest.raises(DomainError) as live_block:
            await production.publish(unqualified_live)
        assert live_block.value.code == "PLATFORM_UNQUALIFIED"
        assert git(repo, "rev-list", "--count", "HEAD") == "2"

        second = await approved_job(service, "website-unknown")
        actual_publish = adapter.publish

        async def response_lost(request: PublishRequest):
            await actual_publish(request)
            raise ExternalOutcomeUnknownError("lost response")

        adapter.publish = response_lost  # type: ignore[method-assign]
        unknown = await service.publish(second)
        assert unknown.outcome == "OUTCOME_UNKNOWN"
        adapter.publish = actual_publish  # type: ignore[method-assign]
        recovered = await service.reconcile(unknown.id)
        assert recovered.outcome == "SUCCEEDED"
        assert git(repo, "rev-list", "--count", "HEAD") == "3"
        assert git(repo, "status", "--porcelain") == ""
    finally:
        await database.close()


def synthetic_request() -> PublishRequest:
    return PublishRequest(
        platform="website",
        account_id=WEBSITE_ACCOUNT_ID,
        content_variant_id=uuid4(),
        publication_job_id=uuid4(),
        idempotency_key="synthetic-local",
        title="Local",
        text="Reviewed text",
        topic="Local",
        scheduled_at=datetime(2026, 10, 15, tzinfo=UTC),
        metadata=PublishMetadata(topic="Local", simulation=True),
    )


async def test_website_dry_run_and_dirty_checkout(tmp_path: Path) -> None:
    repo = website_repo(tmp_path)
    adapter = WebsiteAdapter(repo, mode="DRY_RUN")
    request = synthetic_request()
    relative, content = adapter.preview(request)
    assert relative.suffix == ".md" and "Reviewed text" in content
    with pytest.raises(BeforeSendError):
        await adapter.publish(request)
    assert git(repo, "rev-list", "--count", "HEAD") == "1"
    (repo / "dirty.txt").write_text("unrelated", encoding="utf-8")
    committing = WebsiteAdapter(repo)
    with pytest.raises(BeforeSendError):
        await committing.publish(request)
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


async def test_website_commit_ignores_checkout_hooks(tmp_path: Path) -> None:
    repo = website_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\nprintf ran > '{marker.as_posix()}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    adapter = WebsiteAdapter(repo)
    result = await adapter.publish(synthetic_request())
    assert len(result.external_id) == 40
    assert not marker.exists()
    assert git(repo, "rev-list", "--count", "HEAD") == "2"


def test_website_mdx_and_git_extensions_fail_closed(tmp_path: Path) -> None:
    repo = website_repo(tmp_path)
    adapter = WebsiteAdapter(repo, format="mdx")
    unsafe = synthetic_request().model_copy(update={"text": "<Danger />"})
    with pytest.raises(HumanInterventionRequiredError):
        adapter.preview(unsafe)
    git(repo, "config", "--local", "filter.inject.clean", "echo dangerous")
    with pytest.raises(ValueError, match="configuration"):
        WebsiteAdapter(repo)
