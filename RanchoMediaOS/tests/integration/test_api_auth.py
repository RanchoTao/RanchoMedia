from __future__ import annotations

from rancho_core.domain import FrozenClock
from rancho_core.settings import Settings

from tests.conftest import open_runtime


async def test_development_requires_local_secret_not_public_owner_id(
    settings: Settings, clock: FrozenClock
) -> None:
    development = Settings(
        app_env="development",
        database_url=settings.database_url,
        mock_database_url=settings.mock_database_url,
        local_api_token="fixture-secret-only",
        log_level="ERROR",
    )
    async with open_runtime(development, clock) as runtime:
        assert (await runtime.client.get("/health")).status_code == 200
        assert (await runtime.client.get("/trends")).status_code == 401
        assert (
            await runtime.client.get("/trends", headers={"X-Rancho-API-Token": "incorrect"})
        ).status_code == 401
        assert (
            await runtime.client.get(
                "/trends", headers={"X-Rancho-API-Token": "fixture-secret-only"}
            )
        ).status_code == 200
