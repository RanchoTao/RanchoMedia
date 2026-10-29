"""Synthetic local-entry tests, without real token, material upload, or draft request."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rancho_wechat import wechat_channel


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    path = Path(__file__).resolve().parents[1] / "scripts/wechat_live_canary.py"
    spec = importlib.util.spec_from_file_location("synthetic_wechat_live", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    variants = tmp_path / "variants.json"
    variants.write_text(json.dumps({"wechat_title": "TEST ONLY", "wechat_body": "TEST ONLY body"}))
    monkeypatch.setattr(module, "VARIANTS", variants)
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"SYNTHETIC IMAGE BYTES NOT AN ACTUAL UPLOAD")
    monkeypatch.setattr(module, "COVER", cover)
    (tmp_path / "credentials.dpapi").write_bytes(b"SYNTHETIC credential presence sentinel")
    secret = {"app_id": "wxSYNTHETIC", "app_secret": "SYNTHETIC_SECRET_NOT_REAL"}
    monkeypatch.setattr(module, "credentials", lambda: dict(secret))
    monkeypatch.setattr(module, "store_credentials", lambda value: secret.update(value))
    module.dump(
        tmp_path / "authorization.json",
        {
            "account_name": "SYNTHETIC ACCOUNT",
            "account_self_confirmed": True,
            "app_id_sha256": module.digest(secret["app_id"].encode()),
            "variant_sha256": module.variant_sha(),
            "scope": "ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY",
        },
    )
    monkeypatch.setattr(
        module.importlib.metadata,
        "distribution",
        lambda name: SimpleNamespace(
            read_text=lambda filename: json.dumps({"vcs_info": {"commit_id": module.PIN}})
        ),
    )
    state = SimpleNamespace(
        module=module, calls=[], failure=None, secret=secret, token_error=None, routes=[]
    )

    def operation(name: str) -> None:
        state.calls.append(name)
        if state.failure == name:
            raise TimeoutError("https://invalid/?secret=SYNTHETIC_SECRET_NOT_REAL")

    class TokenClient:
        def __init__(self, appid: str, appsecret: str, **kwargs: Any) -> None:
            assert kwargs == {"timeout": 20, "auto_retry": False}
            self._http = SimpleNamespace(max_redirects=5, trust_env=True)

        def fetch_access_token(self) -> dict:
            assert self._http.max_redirects == 0 and self._http.trust_env is False
            operation("token")
            if state.token_error:
                raise state.token_error
            return {"access_token": "SYNTHETIC_NEW_TOKEN_NOT_REAL", "expires_in": 7200}

    class Channel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            state.routes.append(kwargs.get("use_env_proxy", False))
            self._client = lambda: SimpleNamespace(material=SimpleNamespace(add=self.material))

        def material(self, kind: str, image: Any) -> dict:
            assert kind == "image" and image.read()
            assert module.load(tmp_path / "receipt.json")["cover_state"] == "SENDING"
            operation("cover")
            return {"media_id": "SYNTHETIC-cover"}

        def list_drafts(self, count: int) -> dict:
            assert count == 1
            operation("probe")
            return {"status": "REMOTE_DRAFT_LIST_READ", "evidence": {"items": []}}

        def add_draft(self, request: dict) -> dict:
            assert module.load(tmp_path / "receipt.json")["draft_state"] == "SENDING"
            assert len(request["articles"]) == 1
            state.calls.append("draft")
            if state.failure == "draft":
                return {"status": "UNKNOWN_REMOTE_STATUS", "remote_id": None}
            return {"status": "REMOTE_DRAFT_CREATED", "remote_id": "SYNTHETIC-draft"}

        def get_draft(self, media_id: str) -> dict:
            assert media_id == "SYNTHETIC-draft"
            operation("get")
            return {"status": "REMOTE_DRAFT_CONFIRMED", "remote_id": media_id}

    monkeypatch.setitem(sys.modules, "wechatpy", SimpleNamespace(WeChatClient=TokenClient))
    monkeypatch.setattr(wechat_channel, "WechatDraftChannel", Channel)
    return state


def test_official_preparation_contract_persists_intent_and_uses_single_mutations(live) -> None:
    result = live.module.execute()
    assert result["status"] == "REMOTE_DRAFT_CONFIRMED"
    assert live.calls == ["token", "probe", "cover", "draft", "get"]
    assert result["public_publish_enabled"] is False and result["mass_send_enabled"] is False
    live.module.execute()
    assert live.calls == ["token", "probe", "cover", "draft", "get", "probe", "get"]
    assert "SYNTHETIC_SECRET_NOT_REAL" not in (live.module.STATE / "receipt.json").read_text()
    assert "SYNTHETIC_NEW_TOKEN_NOT_REAL" not in (live.module.STATE / "receipt.json").read_text()


@pytest.mark.parametrize("operation", ["token", "cover", "draft"])
def test_unknown_mutation_cannot_repeat_after_next_run(live, operation: str) -> None:
    live.failure = operation
    first = live.module.execute()
    assert first["status"] == "UNKNOWN_REMOTE_STATUS"
    live.failure = None
    second = live.module.execute()
    assert "UNKNOWN" in second["status"]
    assert live.calls.count(operation) == 1


def test_changed_approved_variant_is_blocked_before_sdk_operation(live) -> None:
    live.module.VARIANTS.write_text(
        json.dumps({"wechat_title": "CHANGED", "wechat_body": "CHANGED"})
    )
    result = live.module.execute()
    assert result["status"] == "WAITING_AUTHORIZATION" and not live.calls


def test_missing_local_credentials_is_honest_and_has_no_external_request(live) -> None:
    (live.module.STATE / "credentials.dpapi").unlink()
    result = live.module.execute()
    assert result == {"status": "WAITING_CREDENTIALS", "real_platform_requests": 0}
    assert not live.calls


def test_lock_blocks_parallel_run_instead_of_duplicate_upload(live) -> None:
    (live.module.STATE / "running.lock").write_text("SYNTHETIC existing process")
    result = live.module.run_locked()
    assert result["status"] == "HOLD_EXISTING_RUN_LOCK_MANUAL_CHECK_REQUIRED"
    assert not live.calls


@pytest.mark.parametrize("code", [40164, "40164"])
def test_token_only_rejection_retains_safe_diagnostics_and_repairs_string_codes(live, code) -> None:
    class Rejected(Exception):
        errcode = code
        errmsg = (
            "invalid ip 203.0.113.24, not in whitelist rid: synthetic-rid "
            "https://invalid/?secret=SYNTHETIC_SECRET_NOT_REAL "
            "access_token=SYNTHETIC_NEW_TOKEN_NOT_REAL"
        )

    live.token_error = Rejected()
    result = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-1", whitelist_confirmed=True
    )
    assert live.calls == ["token"]
    assert result["token_request_state"] == "REJECTED"
    detail = result["token_error"]
    assert detail["errcode"] == 40164 and detail["stage"] == "token"
    assert detail["rid"] == "synthetic-rid"
    assert detail["wechat_observed_ip"] == "203.0.113.24"
    text = json.dumps(result)
    for private in ["SYNTHETIC_SECRET_NOT_REAL", "SYNTHETIC_NEW_TOKEN_NOT_REAL", "https://"]:
        assert private not in text


def test_token_only_success_cannot_upload_and_same_permit_cannot_replay(live) -> None:
    result = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-2", whitelist_confirmed=True
    )
    assert result["token_request_state"] == "CONFIRMED"
    assert result["credentials_saved"] is True and live.calls == ["token"]
    again = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-2", whitelist_confirmed=True
    )
    assert again["status"] == "AUTHORIZATION_ALREADY_USED" and live.calls == ["token"]
    probe = live.module.run_locked("probe-read-only", authorization_id="synthetic-probe-1")
    assert probe["status"] == "REMOTE_DRAFT_LIST_READ"
    assert live.calls == ["token", "probe"] and "items" not in probe["evidence"]
    assert not (live.module.STATE / "draft-add.json").exists()
    assert not (live.module.STATE / "draft-get.json").exists()
    assert (
        live.module.run_locked("probe-read-only", authorization_id="synthetic-probe-1")["status"]
        == "AUTHORIZATION_REQUIRED_OR_ALREADY_USED"
    )
    assert live.calls == ["token", "probe"]


def test_token_and_probe_require_explicit_authorization_and_confirmed_token(live) -> None:
    assert (
        live.module.run_locked("token-only", authorization_id="synthetic-permit-3")["status"]
        == "WAITING_AUTHORIZATION"
    )
    assert (
        live.module.run_locked("probe-read-only", authorization_id="synthetic-probe-2")["status"]
        == "CONFIRMED_TOKEN_REQUIRED"
    )
    assert not live.calls


def test_real_uncertainty_blocks_fresh_permit_and_full_workflow(live) -> None:
    live.failure = "token"
    first = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-4", whitelist_confirmed=True
    )
    assert first["token_request_state"] == "UNKNOWN"
    live.failure = None
    second = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-5", whitelist_confirmed=True
    )
    assert second["status"] == "TOKEN_OUTCOME_UNKNOWN_MANUAL_RECOVERY_REQUIRED"
    live.module.execute()
    assert live.calls == ["token"]


def test_legacy_rejected_unknown_is_reconciled_without_touching_original_or_credentials(
    live, monkeypatch
) -> None:
    path = live.module.STATE / "receipt.json"
    original = {
        "status": "REMOTE_REJECTED",
        "token_request_state": "UNKNOWN",
        "app_id_sha256": live.module.digest(live.secret["app_id"].encode()),
        "variant_sha256": live.module.variant_sha(),
        "token_error": {"status": "REMOTE_REJECTED", "errcode": "40164"},
    }
    live.module.dump(path, original)
    before = path.read_bytes()
    credential_before = (live.module.STATE / "credentials.dpapi").read_bytes()
    monkeypatch.setattr(live.module, "credentials", lambda: pytest.fail("offline must not decrypt"))
    result = live.module.run_locked("repair-state")
    assert result["token_request_state"] == "REJECTED" and result["real_platform_requests"] == 0
    assert path.read_bytes() == before
    assert (live.module.STATE / "credentials.dpapi").read_bytes() == credential_before
    assert not live.calls


def test_legacy_rejection_requires_token_only_and_preserves_exact_failure_bytes(live) -> None:
    path = live.module.STATE / "receipt.json"
    live.module.dump(
        path,
        {
            "status": "REMOTE_REJECTED",
            "token_request_state": "UNKNOWN",
            "app_id_sha256": live.module.digest(live.secret["app_id"].encode()),
            "variant_sha256": live.module.variant_sha(),
            "token_error": {"status": "REMOTE_REJECTED", "errcode": 40164},
        },
    )
    original = path.read_bytes()
    result = live.module.execute()
    assert result["status"] == "TOKEN_REJECTED_EXPLICIT_TOKEN_ONLY_REQUIRED"
    assert not live.calls
    assert original in [p.read_bytes() for p in (live.module.STATE / "history").glob("*.json")]


def test_confirmed_token_local_save_error_does_not_become_remote_unknown(live, monkeypatch) -> None:
    def fail(value):
        raise OSError("SYNTHETIC_SECRET_NOT_REAL")

    monkeypatch.setattr(live.module, "store_credentials", fail)
    result = live.module.run_locked(
        "token-only", authorization_id="synthetic-permit-6", whitelist_confirmed=True
    )
    assert result["status"] == "TOKEN_CONFIRMED_LOCAL_SAVE_FAILED"
    assert result["token_request_state"] == "CONFIRMED"
    assert result["credentials_saved"] is False
    assert "SYNTHETIC_SECRET_NOT_REAL" not in json.dumps(result)
    assert (
        live.module.run_locked("probe-read-only", authorization_id="synthetic-probe-3")["status"]
        == "CONFIRMED_TOKEN_REQUIRED"
    )
    assert live.calls == ["token"]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"access_token": "", "expires_in": 7200},
        {"access_token": "SYNTHETIC", "expires_in": True},
    ],
)
def test_malformed_success_is_uncertain_and_never_probed(live, monkeypatch, response) -> None:
    monkeypatch.setattr(
        live.module,
        "pinned_client",
        lambda secret: SimpleNamespace(fetch_access_token=lambda: response),
    )
    result = live.module.run_locked(
        "token-only", authorization_id="synthetic-malformed", whitelist_confirmed=True
    )
    assert result["token_request_state"] == "UNKNOWN"
    assert (
        live.module.run_locked("probe-read-only", authorization_id="synthetic-no-probe")["status"]
        == "CONFIRMED_TOKEN_REQUIRED"
    )
    assert not live.calls


def test_rejected_token_cannot_trigger_read_only_probe(live) -> None:
    class Rejection(Exception):
        errcode = "40164"
        errmsg = "invalid ip 203.0.113.24, not in whitelist"

    live.token_error = Rejection()
    live.module.run_locked(
        "token-only", authorization_id="synthetic-rejected", whitelist_confirmed=True
    )
    assert (
        live.module.run_locked("probe-read-only", authorization_id="synthetic-no-probe")["status"]
        == "CONFIRMED_TOKEN_REQUIRED"
    )
    assert live.calls == ["token"]


def test_explicit_proxy_mode_is_persisted_and_read_only_probe_inherits_it(
    live, monkeypatch
) -> None:
    selections = []

    def client(secret, *, use_env_proxy=False):
        selections.append(use_env_proxy)

        def fetch():
            live.calls.append("token")
            return {"access_token": "SYNTHETIC_PROXY_TOKEN", "expires_in": 7200}

        return SimpleNamespace(fetch_access_token=fetch, _http=SimpleNamespace())

    monkeypatch.setattr(live.module, "pinned_client", client)
    result = live.module.run_locked(
        "token-only",
        authorization_id="synthetic-proxy-permit",
        whitelist_confirmed=True,
        use_env_proxy=True,
    )
    assert result["network_mode"] == "ENVIRONMENT_PROXY" and selections == [True]
    assert result["token_request_state"] == "CONFIRMED" and live.calls == ["token"]
    live.module.run_locked("probe-read-only", authorization_id="synthetic-proxy-probe")
    assert live.routes == [True] and live.calls == ["token", "probe"]
