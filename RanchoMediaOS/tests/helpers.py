from typing import Any

import httpx
from rancho_core.domain import ACCOUNT_ID, FrozenClock


async def post(client: httpx.AsyncClient, path: str, body: dict[str, Any] | None = None) -> Any:
    response = await client.post(path, json=body)
    assert response.status_code in {200, 201}, response.text
    return response.json()


async def draft(
    client: httpx.AsyncClient, topic: str = "Research", score: float = 0.5
) -> dict[str, Any]:
    trend = await post(
        client, "/trends", {"topic": topic, "title": f"{topic} trend", "score": score}
    )
    idea = await post(client, f"/trends/{trend['id']}/ideas")
    brief = await post(client, f"/ideas/{idea['id']}/briefs")
    return await post(client, f"/briefs/{brief['id']}/drafts")


def publication_request(
    variant: dict[str, Any], clock: FrozenClock, key: str = "first"
) -> dict[str, Any]:
    return {
        "content_variant_id": variant["id"],
        "account_id": str(ACCOUNT_ID),
        "idempotency_key": key,
        "scheduled_at": clock.now().isoformat(),
    }


async def approved(
    client: httpx.AsyncClient, clock: FrozenClock, topic: str = "Research", score: float = 0.5
) -> dict[str, Any]:
    content = await draft(client, topic, score)
    variant = content["variants"][0]
    await post(
        client,
        f"/review/{variant['id']}/approve",
        {"account_id": str(ACCOUNT_ID), "scheduled_at": clock.now().isoformat()},
    )
    return variant
