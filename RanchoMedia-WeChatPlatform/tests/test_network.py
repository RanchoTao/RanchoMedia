"""Synthetic local Director state-machine tests; no real account or publication evidence."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rancho_wechat import network
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.network import CHANNELS, ChannelPolicy, Director, OperatorPolicy, fingerprint
from rancho_wechat.production import save_json
from rancho_wechat.providers import MockProvider
from rancho_wechat.store import Store

PAST = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def director(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Director:
    desk = EditorialDesk(Store(tmp_path / "editor.db"), MockProvider(), tmp_path / "exports")
    result = Director(desk, tmp_path / "network")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("State-machine regression must never invoke a real SDK subprocess")

    monkeypatch.setattr(network.subprocess, "run", forbidden)
    monkeypatch.setattr(result, "_wechat_draft", forbidden)
    return result


@pytest.fixture
def production(director: Director) -> dict[str, Any]:
    """A synthetic reviewed ledger row, not a claim of real model review or approval."""
    topic = director.desk.create_topic("SYNTHETIC Director regression article")
    director.desk.decide_topic(topic["id"], "ACCEPT")
    director.desk.add_source(
        "SYNTHETIC source, not fetched",
        "https://example.invalid/synthetic-source",
        "Synthetic local source text used only for persistent article-state regression testing.",
        "PRIMARY",
        topic["id"],
    )
    director.desk.research(topic["id"])
    article = director.desk.generate(topic["id"])
    result = {
        "article_id": article["id"],
        "version_id": article["current_version_id"],
        "canonical_content_id": "SYNTHETIC-content",
        "generation_job_key": "director-editorial:SYNTHETIC",
        "editorial_decision": "LOW_RISK_REVIEW_CLEARED",
        "scope": "software_explainer",
        "variants": {
            "x_posts": ["TEST ONLY post one", "TEST ONLY post two"],
            "xhs_title": "TEST ONLY",
            "xhs_body": "Synthetic note",
            "zhihu_title": "TEST ONLY",
            "zhihu_body": "Synthetic long-form body. " * 20,
            "wechat_title": "TEST ONLY",
            "wechat_body": "Synthetic sourced paragraph. " * 20,
            "video_scenes": ["TEST ONLY scene"] * 3,
        },
    }
    with director.store.write() as db:
        db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
            (
                result["generation_job_key"],
                "DIRECTOR_EDITORIAL",
                "REVIEWED",
                "{}",
                1,
                None,
                json.dumps(result),
                PAST,
                PAST,
            ),
        )
    return result


def configure(director: Director, **changes: Any) -> ChannelPolicy:
    account = ChannelPolicy(
        account_id="SYNTHETIC-wechat-account",
        enabled=True,
        authorized_by="SYNTHETIC_TEST_ONLY",
        actions=["draft"],
        content_scope=["software_explainer"],
        auto_approve_low_risk=True,
        access_token_env="DIRECTOR_TEST_TOKEN",
        app_id_env="DIRECTOR_TEST_APPID",
        thumb_media_id="SYNTHETIC-cover",
    )
    for key, value in changes.items():
        setattr(account, key, value)
    policy = OperatorPolicy(mode="CANARY", channels={"wechat": account})
    save_json(director.root / "operator-policy.json", policy.model_dump(mode="json"))
    return account


def wechat_task(director: Director) -> dict[str, Any]:
    return next(
        task
        for task in director.tasks()
        if task["kind"] == "CHANNEL_INTENT" and task["payload"]["channel"] == "wechat"
    )


def approve_variant(director: Director) -> None:
    policy = director.policy()
    policy.channels["wechat"].approved_variant_hashes = [
        fingerprint(wechat_task(director)["payload"]["variant"])
    ]
    save_json(director.root / "operator-policy.json", policy.model_dump(mode="json"))


def test_default_policy_and_tick_never_send(director: Director, production: dict) -> None:
    assert director.policy().mode == "SHADOW"
    assert all(not account.enabled for account in director.policy().channels.values())
    director.enqueue(production, PAST)
    director.tick()
    assert wechat_task(director)["status"] == "WAITING_CREDENTIALS"
    assert all(not channel["published"] for channel in director.status()["channels"].values())
    assert all(
        task["attempts"] == 0 for task in director.tasks() if task["kind"] == "CHANNEL_INTENT"
    )


@pytest.mark.parametrize("mode", ["SHADOW", "DRY_RUN"])
def test_enabled_accounts_still_cannot_send_in_shadow(
    director: Director, production: dict, mode: str
) -> None:
    configure(director)
    policy = director.policy()
    policy.mode = mode  # type: ignore[assignment]
    save_json(director.root / "operator-policy.json", policy.model_dump(mode="json"))
    director.enqueue(production, PAST)
    director.tick()
    task = wechat_task(director)
    assert task["status"] == "SHADOW_READY" and task["attempts"] == 0
    assert task["result"]["blocked_reason"] == "NO_REMOTE_WRITE_IN_SHADOW"


def test_same_slot_enqueue_survives_restart_without_duplicates(
    director: Director, production: dict
) -> None:
    first = director.enqueue(production, PAST)
    reopened = Director(
        EditorialDesk(Store(director.store.path), MockProvider(), director.desk.export_root),
        director.root,
    )
    # Same instant, different timezone spelling must select the same slot.
    second = reopened.enqueue(production, "2020-01-01T08:00:00+08:00")
    assert first == second and len(set(first)) == len(CHANNELS)
    assert len([task for task in reopened.tasks() if task["kind"] == "CHANNEL_INTENT"]) == len(
        CHANNELS
    )


def test_tampered_production_is_rejected_before_queue_creation(
    director: Director, production: dict
) -> None:
    changed = copy.deepcopy(production)
    changed["variants"]["wechat_body"] = "Tampered body"
    with pytest.raises(EditorialError) as error:
        director.enqueue(changed, PAST)
    assert error.value.code == "LEGACY_CONTENT_FORBIDDEN"
    assert not [task for task in director.tasks() if task["kind"] == "CHANNEL_INTENT"]


def test_existing_slot_payload_conflict_is_not_overwritten(
    director: Director, production: dict
) -> None:
    director.enqueue(production, PAST)
    original = wechat_task(director)
    changed = copy.deepcopy(original["payload"])
    changed["variant"]["html"] = "Tampered queued HTML"
    with director.store.write() as db:
        db.execute(
            "UPDATE tasks SET payload_json=? WHERE task_key=?",
            (json.dumps(changed), original["task_key"]),
        )
    with pytest.raises(EditorialError) as error:
        director.enqueue(production, PAST)
    assert error.value.code == "INTENT_CONFLICT"
    assert wechat_task(director)["payload"] == changed


@pytest.mark.parametrize("state", ["SENDING", "LEASED"])
def test_expired_mutation_lease_becomes_unknown_and_never_resends(
    director: Director, production: dict, state: str
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    key = wechat_task(director)["task_key"]
    with director.store.write() as db:
        db.execute(
            "UPDATE tasks SET status=?,attempts=1,lease_until=? WHERE task_key=?",
            (state, PAST, key),
        )
    director.tick()
    assert wechat_task(director)["status"] == "UNKNOWN_REMOTE_STATUS"
    # A new process reusing the same durable ledger must preserve the stop state.
    restarted = Director(director.desk, director.root)
    restarted.tick()
    task = wechat_task(restarted)
    assert task["status"] == "UNKNOWN_REMOTE_STATUS" and task["attempts"] == 1
    assert task["lease_until"] is None
    assert task["result"]["blocked_reason"] == "EXPIRED_MUTATION_LEASE_RECONCILE_ONLY"


@pytest.mark.parametrize("channel", [None, "wechat"])
def test_pause_preserves_pending_intent_and_is_audited(
    director: Director, production: dict, channel: str | None
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    director.set_pause(True, channel)
    result = director.tick()
    assert wechat_task(director)["status"] == "PENDING"
    if channel is None:
        assert result == {"status": "PAUSED", "processed": 0}
    assert director.desk.audit("network", channel or "all")[-1]["action"] == "PAUSE"
    director.set_pause(False, channel)
    assert not director.status()["channels"]["wechat"]["paused"]


def test_future_schedule_does_not_enter_send_or_waiting_states(
    director: Director, production: dict
) -> None:
    configure(director)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    director.enqueue(production, future)
    assert director.tick()["processed"] == 0
    assert wechat_task(director)["status"] == "PENDING"
    with pytest.raises(ValueError, match="timezone"):
        director.enqueue(production, "2020-01-01T00:00:00")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"content_scope": []}, "CONTENT_SCOPE_OR_REVIEW_REQUIRED"),
        ({"auto_approve_low_risk": False}, "CONTENT_SCOPE_OR_REVIEW_REQUIRED"),
        ({"actions": []}, "TOOL_ADAPTER_WRITE_NOT_QUALIFIED"),
        ({"approved_variant_hashes": []}, "CHANNEL_VARIANT_REVIEW_REQUIRED"),
    ],
)
def test_wechat_scope_action_and_variant_approval_gates(
    director: Director, production: dict, change: dict, reason: str
) -> None:
    configure(director, **change)
    director.enqueue(production, PAST)
    director.tick()
    task = wechat_task(director)
    assert task["status"] == "WAITING_AUTHORIZATION" and task["attempts"] == 0
    assert task["result"]["blocked_reason"] == reason


def test_account_change_requires_requeue_even_after_variant_approval(
    director: Director, production: dict
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    policy = director.policy()
    policy.channels["wechat"].account_id = "SYNTHETIC-different-account"
    save_json(director.root / "operator-policy.json", policy.model_dump(mode="json"))
    director.tick()
    assert wechat_task(director)["result"]["blocked_reason"] == "ACCOUNT_CHANGED_REQUEUE_REQUIRED"
    assert wechat_task(director)["attempts"] == 0


def test_missing_credentials_after_approval_still_never_calls_worker(
    director: Director, production: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    monkeypatch.delenv("DIRECTOR_TEST_TOKEN", raising=False)
    monkeypatch.delenv("DIRECTOR_TEST_APPID", raising=False)
    director.tick()
    assert wechat_task(director)["status"] == "WAITING_CREDENTIALS"
    assert wechat_task(director)["attempts"] == 0


def test_qualified_send_persists_intent_first_and_does_not_repeat(
    director: Director, production: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    monkeypatch.setenv("DIRECTOR_TEST_TOKEN", "SYNTHETIC_NOT_VALID")
    monkeypatch.setenv("DIRECTOR_TEST_APPID", "SYNTHETIC_NOT_VALID")
    calls = []

    def synthetic_send(account: ChannelPolicy, payload: dict) -> dict:
        pending = wechat_task(director)
        assert pending["status"] == "SENDING" and pending["attempts"] == 1
        assert pending["lease_until"] is not None
        calls.append(payload)
        return {"status": "REMOTE_DRAFT_CREATED", "remote_id": "SYNTHETIC-draft"}

    monkeypatch.setattr(director, "_wechat_draft", synthetic_send)
    director.tick()
    director.tick()
    assert len(calls) == 1
    assert wechat_task(director)["status"] == "REMOTE_DRAFT_CREATED"
    assert director.status()["channels"]["wechat"]["draft_created"] is True
    assert director.status()["channels"]["wechat"]["published"] is False


def test_worker_claim_without_remote_id_is_unknown_and_cannot_elevate_status(
    director: Director, production: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    monkeypatch.setenv("DIRECTOR_TEST_TOKEN", "SYNTHETIC_NOT_VALID")
    monkeypatch.setenv("DIRECTOR_TEST_APPID", "SYNTHETIC_NOT_VALID")
    executable = director.root.parent / "ecosystem/wechat-bili/.venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("SYNTHETIC FILE NEVER EXECUTED", encoding="utf-8")
    calls = []

    def fake_worker(*args: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        assert wechat_task(director)["status"] == "SENDING"
        return SimpleNamespace(returncode=0, stdout='{"status":"REMOTE_DRAFT_CREATED"}')

    monkeypatch.setattr(network.subprocess, "run", fake_worker)
    monkeypatch.setattr(director, "_wechat_draft", Director._wechat_draft.__get__(director))
    director.tick()
    director.tick()
    assert len(calls) == 1
    assert wechat_task(director)["status"] == "UNKNOWN_REMOTE_STATUS"
    assert wechat_task(director)["result"]["blocked_reason"] == "NO_REMOTE_ID"
    assert director.status()["channels"]["wechat"]["draft_created"] is False
    assert director.status()["channels"]["wechat"]["published"] is False


@pytest.mark.parametrize("claimed", ["REMOTE_DRAFT_CREATED", "PUBLICLY_VERIFIED"])
def test_forged_durable_receipt_without_identifier_is_not_remote_success(
    director: Director, production: dict, claimed: str
) -> None:
    director.enqueue(production, PAST)
    task = wechat_task(director)
    director._finish(task["task_key"], claimed, {"status": claimed})
    status = director.status()["channels"]["wechat"]
    assert not status["draft_created"] and not status["published"]


@pytest.mark.parametrize("change", ["new_version", "rejected"])
def test_article_changed_after_queue_requires_review_without_sending(
    director: Director, production: dict, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    monkeypatch.setenv("DIRECTOR_TEST_TOKEN", "SYNTHETIC_NOT_VALID")
    monkeypatch.setenv("DIRECTOR_TEST_APPID", "SYNTHETIC_NOT_VALID")
    if change == "new_version":
        version = director.desk.article(production["article_id"])["versions"][-1]
        changed = director.desk.edit(
            production["article_id"],
            version["title"],
            version["body"] + "\n\nSYNTHETIC author revision must receive a new review.",
            version["summary"],
            "SYNTHETIC regression edit",
        )
        assert changed["current_version_id"] != production["version_id"]
    else:
        director.desk.decide_article(production["article_id"], "REJECT", note="TEST ONLY reject")
    director.tick()
    director.tick()
    task = wechat_task(director)
    assert task["status"] == "REVIEW_REQUIRED" and task["attempts"] == 0
    assert task["result"]["blocked_reason"] == "ARTICLE_CHANGED_REVIEW_AGAIN"


def test_attempt_limit_moves_to_dead_letter_without_another_call(
    director: Director, production: dict
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    key = wechat_task(director)["task_key"]
    with director.store.write() as db:
        db.execute("UPDATE tasks SET attempts=3 WHERE task_key=?", (key,))
    director.tick()
    director.tick()
    task = wechat_task(director)
    assert task["status"] == "DEAD_LETTER" and task["attempts"] == 3
    assert task["result"]["blocked_reason"] == "ATTEMPT_LIMIT_MANUAL_RECOVERY"


@pytest.mark.parametrize("attempts", [1, 2])
def test_known_error_exponential_backoff_blocks_early_retry_and_allows_due_retry(
    director: Director, production: dict, attempts: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(director)
    director.enqueue(production, PAST)
    approve_variant(director)
    monkeypatch.setenv("DIRECTOR_TEST_TOKEN", "SYNTHETIC_NOT_VALID")
    monkeypatch.setenv("DIRECTOR_TEST_APPID", "SYNTHETIC_NOT_VALID")
    key = wechat_task(director)["task_key"]
    wait_seconds = 60 * 2 ** (attempts - 1)
    calls = []

    def known_error(account: ChannelPolicy, payload: dict) -> dict:
        calls.append(payload)
        assert wechat_task(director)["status"] == "SENDING"
        return {"status": "WAITING_CREDENTIALS", "blocked_reason": "SYNTHETIC_EXPIRED_TOKEN"}

    monkeypatch.setattr(director, "_wechat_draft", known_error)
    with director.store.write() as db:
        db.execute(
            "UPDATE tasks SET attempts=?,status='WAITING_CREDENTIALS',"
            "updated_at=? WHERE task_key=?",
            (attempts, (datetime.now(UTC) - timedelta(seconds=wait_seconds - 5)).isoformat(), key),
        )
    director.tick()
    assert not calls and wechat_task(director)["attempts"] == attempts
    with director.store.write() as db:
        db.execute(
            "UPDATE tasks SET updated_at=? WHERE task_key=?",
            ((datetime.now(UTC) - timedelta(seconds=wait_seconds + 5)).isoformat(), key),
        )
    director.tick()
    assert len(calls) == 1 and wechat_task(director)["attempts"] == attempts + 1
    director.tick()
    assert len(calls) == 1
    expected = "DEAD_LETTER" if attempts == 2 else "WAITING_CREDENTIALS"
    assert wechat_task(director)["status"] == expected


@pytest.mark.parametrize("channel", ["xiaohongshu", "douyin"])
def test_web_ui_public_receipt_preserves_account_and_zero_comment_read(
    director: Director, production: dict, channel: str
) -> None:
    director.enqueue(production, PAST)
    task = next(
        row
        for row in director.tasks()
        if row["kind"] == "CHANNEL_INTENT" and row["payload"]["channel"] == channel
    )
    director._finish(
        task["task_key"],
        "PUBLICLY_VERIFIED",
        {
            "status": "PUBLICLY_VERIFIED",
            "remote_id": "SYNTHETIC-post-id",
            "account_id": "SYNTHETIC-RanchoTao",
            "executor": "OFFICIAL_CREATOR_WEB_UI",
            "public_url": "https://example.invalid/SYNTHETIC-public-post",
            "comments_read": True,
            "comments": [],
            "comment_count": 0,
        },
    )
    before = director.policy().model_dump()
    status = director.status()["channels"][channel]
    assert status["account_id"] == "SYNTHETIC-RanchoTao"
    assert status["published"] is True and status["authenticated"] is True
    assert status["web_ui_authenticated"] is True and status["api_authenticated"] is False
    assert status["authentication_method"] == "OFFICIAL_CREATOR_WEB_UI"
    assert status["comments_read"] is True and status["comment_count"] == 0
    assert status["comments"] == []
    assert status["remote_url"] == "https://example.invalid/SYNTHETIC-public-post"
    assert director.policy().model_dump() == before


def test_website_repository_owner_does_not_leak_into_other_platform_account(
    director: Director, production: dict
) -> None:
    policy = director.policy()
    policy.channels["xiaohongshu"].account_id = "SYNTHETIC-policy-account"
    save_json(director.root / "operator-policy.json", policy.model_dump(mode="json"))
    director.enqueue(production, PAST)
    for task in director.tasks():
        if task["kind"] != "CHANNEL_INTENT":
            continue
        if task["payload"]["channel"] not in {"website", "xiaohongshu"}:
            continue
        director._finish(
            task["task_key"],
            "PUBLICLY_VERIFIED",
            {
                "status": "PUBLICLY_VERIFIED",
                "remote_id": "SYNTHETIC-post-id",
                "repository": "SYNTHETIC-site-owner/site",
            },
        )
    status = director.status()["channels"]
    assert status["website"]["account_id"] == "SYNTHETIC-site-owner"
    assert status["xiaohongshu"]["account_id"] == "SYNTHETIC-policy-account"
    assert status["xiaohongshu"]["api_authenticated"] is False
    assert status["xiaohongshu"]["web_ui_authenticated"] is False
    assert status["xiaohongshu"]["comments_read"] is False
    assert status["xiaohongshu"]["comment_count"] is None


def test_sdk_draft_receipt_proves_api_but_not_web_ui_authentication(
    director: Director, production: dict
) -> None:
    director.enqueue(production, PAST)
    task = wechat_task(director)
    director._finish(
        task["task_key"],
        "REMOTE_DRAFT_CREATED",
        {
            "status": "REMOTE_DRAFT_CREATED",
            "remote_id": "SYNTHETIC-draft-id",
            "evidence": {"sdk": "wechatpy"},
        },
    )
    status = director.status()["channels"]["wechat"]
    assert status["api_authenticated"] is True and status["web_ui_authenticated"] is False
    assert status["authentication_method"] == "OFFICIAL_API"
    assert status["draft_created"] is True and status["published"] is False
