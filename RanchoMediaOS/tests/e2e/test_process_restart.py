"""A real uvicorn subprocess shutdown/restart with the same migrated database files."""

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from rancho_core.domain import OWNER_ID, FrozenClock
from rancho_core.settings import Settings
from rancho_db.database import Database
from rancho_mock.adapter import PersistentMockAdapter

from tests.helpers import post


@asynccontextmanager
async def server(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update(
        APP_ENV="test",
        DATABASE_URL=settings.database_url,
        MOCK_DATABASE_URL=settings.mock_database_url,
        LOG_LEVEL="ERROR",
    )
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "rancho_api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        "--log-level",
        "error",
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            trust_env=False,
            headers={"X-Actor-ID": str(OWNER_ID)},
            timeout=5,
        ) as client:
            ready = False
            for _ in range(100):
                assert process.returncode is None, "API subprocess exited before readiness"
                try:
                    if (await client.get("/health")).status_code == 200:
                        ready = True
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            assert ready, "API subprocess did not become ready"
            yield client
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()


async def test_api_subprocess_restart_recovers_unknown(
    settings: Settings, clock: FrozenClock
) -> None:
    async with server(settings) as client:
        # Deliberate past schedule: actual subprocess uses SystemClock, no real waiting.
        trend = await post(client, "/trends", {"topic": "Process durability", "title": "Fixture"})
        idea = await post(client, f"/trends/{trend['id']}/ideas")
        brief = await post(client, f"/ideas/{idea['id']}/briefs")
        content = await post(client, f"/briefs/{brief['id']}/drafts")
        variant = content["variants"][0]
        schedule = "2000-01-01T00:00:00Z"
        await post(client, f"/review/{variant['id']}/approve", {"scheduled_at": schedule})
        request = {
            "content_variant_id": variant["id"],
            "scheduled_at": schedule,
            "idempotency_key": "subprocess",
            "failure_mode": "FAIL_AFTER_EXTERNAL_SUCCESS",
        }
        initial = await post(client, "/publications", request)
        assert initial["status"] == "OUTCOME_UNKNOWN"
    async with server(settings) as restarted:
        recovered = await post(restarted, "/publications", request)
        assert recovered["outcome"] == "SUCCEEDED" and recovered["attempt_count"] == 1
        assert recovered["id"] == initial["id"]
        await post(restarted, f"/publications/{recovered['id']}/metrics/mock", {"age_hours": 1})
        assert (await restarted.get("/analytics/summary")).json()["publication_count"] == 1
    database = Database(settings.mock_database_url)
    try:
        assert await PersistentMockAdapter(database, clock, 7).external_post_count() == 1
    finally:
        await database.close()
    print("REAL_API_PROCESS_RESTART: PASS")
