from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from rancho_api.app import create_app
from rancho_core.domain import OWNER_ID, FrozenClock
from rancho_core.service import MediaService
from rancho_core.settings import Settings
from rancho_mock.adapter import PersistentMockAdapter


@pytest.fixture(scope="session")
def templates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("migrated-templates")
    files = (root / "core.db", root / "mock.db")
    for filename, configuration in zip(files, ("alembic.ini", "alembic-mock.ini"), strict=True):
        config = Config(configuration)
        config.attributes["database_url"] = f"sqlite+aiosqlite:///{filename.as_posix()}"
        command.upgrade(config, "head")
    return files


@pytest.fixture
def settings(tmp_path: Path, templates: tuple[Path, Path]) -> Settings:
    files = (tmp_path / "core.db", tmp_path / "mock.db")
    for source, destination in zip(templates, files, strict=True):
        copyfile(source, destination)
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{files[0].as_posix()}",
        mock_database_url=f"sqlite+aiosqlite:///{files[1].as_posix()}",
        log_level="ERROR",
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 28, 1, tzinfo=UTC))


@dataclass
class Runtime:
    app: FastAPI
    client: httpx.AsyncClient
    service: MediaService
    adapter: PersistentMockAdapter


@asynccontextmanager
async def open_runtime(settings: Settings, clock: FrozenClock) -> AsyncIterator[Runtime]:
    app = create_app(settings, clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Actor-ID": str(OWNER_ID)},
        ) as client:
            yield Runtime(app, client, app.state.service, app.state.adapter)


@pytest.fixture
async def runtime(settings: Settings, clock: FrozenClock) -> AsyncIterator[Runtime]:
    async with open_runtime(settings, clock) as active:
        yield active
