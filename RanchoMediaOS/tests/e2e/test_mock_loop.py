import json
from datetime import timedelta
from uuid import UUID

from rancho_core.domain import ACCOUNT_ID, FrozenClock
from rancho_core.settings import Settings
from rancho_db.models import EffectLedger, OutboxEvent

from tests.conftest import Runtime, open_runtime
from tests.helpers import approved, draft, post, publication_request


async def test_full_mock_media_loop(runtime: Runtime, clock: FrozenClock) -> None:
    client = runtime.client
    content = await draft(client, "Research", 0.50)
    await post(client, "/trends", {"topic": "Tools", "title": "Tool trend", "score": 0.51})
    await post(client, "/trends", {"topic": "Teaching", "title": "Lesson trend", "score": 0.49})
    variant = content["variants"][0]
    assert variant["status"] == "REQUIRES_REVIEW"
    assert content["genome"]["id"] != variant["genome"]["id"]
    before = (await client.get("/topics/next")).json()
    assert before[0]["topic"] == "Tools"
    request = publication_request(variant, clock)
    blocked = await client.post("/publications", json=request)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CONTENT_NOT_APPROVED"
    assert await runtime.adapter.external_post_count() == 0
    await post(
        client,
        f"/review/{variant['id']}/approve",
        {"account_id": str(ACCOUNT_ID), "scheduled_at": clock.now().isoformat()},
    )
    first = await post(client, "/publications", request)
    assert first["outcome"] == "SUCCEEDED"
    for _ in range(5):
        retry = await post(client, "/publications", request)
        assert retry["external_id"] == first["external_id"]
        assert retry["id"] == first["id"]
        assert retry["attempt_count"] == 1
    for age in (1, 6, 24):
        clock.value += timedelta(hours=1)
        snapshot = await post(
            client, f"/publications/{first['id']}/metrics/mock", {"age_hours": age}
        )
        assert snapshot["post_age_seconds"] == age * 3600
        assert snapshot["watch_time"] is None
    analytics = (await client.get(f"/analytics/publications/{first['id']}")).json()
    assert analytics["derived"]["engagement_rate"] > 0
    assert analytics["derived"]["view_velocity"] > 0
    assert analytics["baseline_status"] == "INSUFFICIENT_BASELINE"
    assert analytics["percentile"] is None
    strategy = await post(client, "/strategy/recompute")
    assert strategy["sample_size"] == 1
    assert strategy["quantitative_facts"][0]["kind"] == "FACT"
    assert len(strategy["quantitative_facts"][0]["snapshot_ids"]) == 1  # not 3 samples
    assert {note["kind"] for note in strategy["qualitative_notes"]} == {
        "HYPOTHESIS",
        "RECOMMENDATION",
    }
    after = (await client.get("/topics/next")).json()
    assert after[0]["topic"] == "Research"
    assert after[0]["topic_bonus"] > 0
    next_idea = await post(client, f"/trends/{after[0]['trend_id']}/ideas")
    assert next_idea["strategy_id"] == strategy["id"]
    next_brief = await post(client, f"/ideas/{next_idea['id']}/briefs")
    next_draft = await post(client, f"/briefs/{next_brief['id']}/drafts")
    assert next_draft["variants"][0]["status"] == "REQUIRES_REVIEW"
    assert next_draft["genome"]["strategy_version"] == strategy["id"]
    async with runtime.service.work() as work:
        assert await work.jobs.count() == await work.jobs.publications.count() == 1
        assert await work.metrics.count() == 3
        actions = {row.action for row in await work.audit.list()}
        assert {"approve", "schedule", "publish", "publish_blocked", "generate"} <= actions
        effect = await work.jobs.effects.find(EffectLedger.job_id == UUID(first["id"]))
        assert effect.status == "CONFIRMED"
        outbox = await work.jobs.outbox.find(OutboxEvent.job_id == UUID(first["id"]))
        assert outbox.delivered
    count = await runtime.adapter.external_post_count()
    assert count == 1
    print(
        json.dumps(
            {
                "EXTERNAL_POSTS_AFTER_5_IDENTICAL_RETRIES": count,
                "ranking_before": before[0]["topic"],
                "ranking_after": after[0]["topic"],
                "engagement_rate": analytics["derived"]["engagement_rate"],
            }
        )
    )


async def test_publish_unknown_outcome_recovery(settings: Settings, clock: FrozenClock) -> None:
    async with open_runtime(settings, clock) as first:
        variant = await approved(first.client, clock)
        request = {
            **publication_request(variant, clock, "lost-response"),
            "failure_mode": "FAIL_AFTER_EXTERNAL_SUCCESS",
        }
        unknown = await post(first.client, "/publications", request)
        assert unknown["status"] == "OUTCOME_UNKNOWN"
        assert unknown["publication_id"] is None
        assert await first.adapter.external_post_count() == 1
        async with first.service.work() as work:
            assert await work.jobs.publications.count() == 0
    # New engines, app, adapter and sessions, using the same files after shutdown.
    async with open_runtime(settings, clock) as restarted:
        recovered = await post(restarted.client, "/publications", request)
        assert recovered["outcome"] == "SUCCEEDED"
        assert recovered["attempt_count"] == 1
        assert (await restarted.client.get(f"/publications/{unknown['id']}")).json()[
            "external_id"
        ] == recovered["external_id"]
        assert await restarted.adapter.external_post_count() == 1
        async with restarted.service.work() as work:
            assert await work.jobs.count() == await work.jobs.publications.count() == 1
            assert "reconcile" in {row.action for row in await work.audit.list()}
        print(
            json.dumps(
                {
                    "UNKNOWN_OUTCOME_EXTERNAL_POSTS": await restarted.adapter.external_post_count(),
                    "outcome": recovered["outcome"],
                    "restart_persistence": "PASS",
                }
            )
        )
