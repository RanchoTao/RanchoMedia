"""Real DPAPI, narrow bridge and offline authorization regression tests."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rancho_wechat.desktop import (
    DesktopBridge,
    DesktopError,
    DesktopService,
    atomic_json,
    sha,
    trusted_document,
)
from rancho_wechat.desktop_worker import run

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI contract")
CONFIG = {
    "account_name": "桌面验收合成账号",
    "app_id": "wx0123456789abcdef",
    "app_secret": "synthetic-never-a-real-secret",
    "access_token": "",
    "thumb_media_id": "",
}


@pytest.fixture
def service(tmp_path, monkeypatch):
    instance = DesktopService(ROOT, tmp_path / "profile", no_network=True)
    inputs = tmp_path / "synthetic-inputs"
    inputs.mkdir()
    variants = inputs / "variants.json"
    atomic_json(variants, {"wechat_title": "Synthetic desktop draft", "wechat_body": "Test only."})
    cover = inputs / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-test-only")
    instance._worker.VARIANTS = variants
    instance._worker.COVER = cover
    # IPC contracts are independent of whether the local SDK venv is installed.
    monkeypatch.setattr(instance, "_sdk_ready", lambda: True)
    return instance


def request(service, operation="execute", confirmed=True):
    config = service.snapshot()["config"]
    return {
        "operation": operation,
        "confirmed": confirmed,
        **{key: config[key] for key in ("app_id_sha256", "variant_sha256", "cover_sha256")},
    }


def test_startup_reads_without_creating_or_changing_config(service):
    snapshot = service.snapshot()
    assert not service.state.exists()
    assert snapshot["config"]["has_secret"] is False
    assert snapshot["workspace"]["database_status"] in {"READ_ONLY", "NOT_FOUND"}


def test_real_dpapi_roundtrip_no_readback_or_plaintext(service):
    snapshot = service.save_config(CONFIG)
    raw = (service.state / "credentials.dpapi").read_bytes()
    assert CONFIG["app_secret"].encode() not in raw
    assert service._worker.credentials()["app_secret"] == CONFIG["app_secret"]
    assert snapshot["config"]["has_secret"] is True
    assert CONFIG["app_secret"] not in json.dumps(snapshot)
    assert CONFIG["app_secret"] not in (service.state / "desktop-events.jsonl").read_text()


def test_blank_secret_preserves_existing_and_restart(service):
    service.save_config(CONFIG)
    service.save_config({**CONFIG, "app_secret": "", "account_name": "已修改名称"})
    restarted = DesktopService(ROOT, service.state, no_network=True)
    assert restarted._worker.credentials()["app_secret"] == CONFIG["app_secret"]
    assert restarted.snapshot()["config"]["account_name"] == "已修改名称"


def test_rotation_clears_old_token(service):
    service.save_config({**CONFIG, "access_token": "synthetic-old-token"})
    service.save_config({**CONFIG, "app_secret": "synthetic-replacement"})
    assert service._worker.credentials()["access_token"] == ""


def test_validation_never_echoes_secret(service):
    with pytest.raises(DesktopError) as error:
        service.save_config({**CONFIG, "app_id": "invalid"})
    assert error.value.code == "INVALID_CONFIG"
    assert CONFIG["app_secret"] not in error.value.message
    assert not (service.state / "credentials.dpapi").exists()


def test_bound_account_cannot_be_silently_replaced(service):
    service.save_config(CONFIG)
    original = (service.state / "credentials.dpapi").read_bytes()
    atomic_json(service.state / "receipt.json", {"app_id_sha256": sha(CONFIG["app_id"].encode())})
    with pytest.raises(DesktopError, match="ACCOUNT_BOUND"):
        service.save_config({**CONFIG, "app_id": "wxabcdef0123456789"})
    assert original == (service.state / "credentials.dpapi").read_bytes()


def test_existing_worker_lock_blocks_config_changes(service):
    service.save_config(CONFIG)
    original = (service.state / "credentials.dpapi").read_bytes()
    (service.state / "running.lock").write_text("123")
    with pytest.raises(DesktopError, match="BUSY"):
        service.save_config(CONFIG)
    assert original == (service.state / "credentials.dpapi").read_bytes()


def test_no_network_mode_never_starts_a_process(service, monkeypatch):
    service.save_config(CONFIG)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected process"))
    with pytest.raises(DesktopError, match="NETWORK_DISABLED"):
        service.start_operation(request(service))
    assert not (service.state / "authorization.json").exists()


@pytest.mark.parametrize("operation,confirmed", [("publish", True), ("execute", False)])
def test_only_explicit_draft_scoped_operations(service, operation, confirmed):
    with pytest.raises(DesktopError, match="AUTHORIZATION_REQUIRED"):
        service.start_operation(request(service, operation, confirmed))


def test_confirmed_content_must_match_saved_state(service):
    service.save_config(CONFIG)
    service.no_network = False
    payload = request(service)
    payload["variant_sha256"] = "changed"
    with pytest.raises(DesktopError, match="CONTENT_CHANGED"):
        service.start_operation(payload)


def test_definite_40164_rejection_is_not_unknown_and_no_raw_error(service):
    service.state.mkdir()
    atomic_json(
        service.state / "receipt.json",
        {
            "status": "REMOTE_REJECTED",
            "token_request_state": "UNKNOWN",
            "token_error": {
                "status": "REMOTE_REJECTED",
                "errcode": 40164,
                "errmsg": "access_token=do-not-return-this",
            },
        },
    )
    before = (service.state / "receipt.json").read_bytes()
    receipt = service.snapshot()["receipt"]
    assert receipt["errcode"] == 40164
    assert receipt["unknown"] is False
    assert receipt["token_state"] == "REJECTED"
    assert "do-not-return-this" not in json.dumps(receipt)
    assert before == (service.state / "receipt.json").read_bytes()


def test_unknown_outcome_survives_restart_and_forbids_resend(service, monkeypatch):
    service.save_config(CONFIG)
    atomic_json(service.state / "receipt.json", {"draft_state": "SENDING"})
    restarted = DesktopService(ROOT, service.state)
    monkeypatch.setattr(restarted, "_sdk_ready", lambda: True)
    assert restarted.snapshot()["receipt"]["unknown"]
    with pytest.raises(DesktopError, match="OUTCOME_UNKNOWN"):
        restarted.start_operation(request(restarted))


def test_current_connection_success_keeps_old_rejection_only_in_history(service):
    import time

    service.save_config({**CONFIG, "access_token": "synthetic-current-token"})
    service._worker.store_credentials(
        {**service._worker.credentials(), "token_expires_at": time.time() + 3600}
    )
    atomic_json(
        service.state / "receipt.json",
        {
            "status": "REMOTE_REJECTED",
            "token_request_state": "UNKNOWN",
            "updated_at": "2026-10-29T01:12:00Z",
            "token_error": {"status": "REMOTE_REJECTED", "errcode": 40164},
        },
    )
    atomic_json(
        service.state / "token-validation.json",
        {
            "status": "TOKEN_OBTAINED",
            "token_request_state": "CONFIRMED",
            "updated_at": "2026-10-29T02:03:00Z",
            "network_mode": "ENVIRONMENT_PROXY",
            "credentials_saved": True,
            "app_id_sha256": sha(CONFIG["app_id"].encode()),
        },
    )
    atomic_json(
        service.state / "permission-probe.json",
        {
            "status": "REMOTE_DRAFT_LIST_READ",
            "updated_at": "2026-10-29T02:03:11Z",
            "evidence": {"http_responses": [{"http_status": 200, "elapsed_ms": 449.86}]},
        },
    )
    receipt = service.snapshot()["receipt"]
    assert receipt["token_state"] == "CONFIRMED" and receipt["permission_verified"]
    assert receipt["errcode"] is None and not receipt["unknown"]
    assert receipt["history"][0]["errcode"] == 40164 and receipt["http_status"] == 200


def test_cover_checks_bytes_and_size(service, tmp_path):
    invalid = tmp_path / "pretend.png"
    invalid.write_text("not an image")
    with pytest.raises(DesktopError, match="INVALID_COVER"):
        service.set_cover(invalid)
    invalid.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2_000_000)
    with pytest.raises(DesktopError, match="INVALID_COVER"):
        service.set_cover(invalid)


def test_worker_cover_change_and_reconcile_cannot_create(service, tmp_path):
    service.state.mkdir()
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"synthetic-image")
    atomic_json(
        service.state / "authorization.json",
        {
            "desktop_permit": "permit",
            "cover_sha256": "old",
        },
    )
    assert run(ROOT, service.state, cover, "execute", "permit")["status"] == "COVER_CHANGED"
    atomic_json(
        service.state / "authorization.json",
        {
            "desktop_permit": "permit",
            "cover_sha256": sha(cover.read_bytes()),
        },
    )
    atomic_json(service.state / "receipt.json", {})
    assert run(ROOT, service.state, cover, "reconcile", "permit")["status"] == "DRAFT_ID_REQUIRED"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/index.html",
        "http://127.0.0.1:99/index.html",
        "http://127.0.0.1:1420/other.html",
        "about:blank",
    ],
)
def test_untrusted_document_cannot_call_native_api(service, url):
    bridge = DesktopBridge(service)
    bridge._window = SimpleNamespace(
        real_url="http://127.0.0.1:1420/index.html", get_current_url=lambda: url
    )
    assert bridge.save_config(CONFIG)["error"]["code"] == "UNTRUSTED_DOCUMENT"
    assert not service.state.exists()


def test_trusted_bridge_generic_error_does_not_disclose_exception(service, monkeypatch):
    bridge = DesktopBridge(service)
    bridge._window = SimpleNamespace(
        real_url="file:///safe/index.html", get_current_url=lambda: "file:///safe/index.html"
    )

    def broken():
        raise RuntimeError("secret=never-echo-exception")

    monkeypatch.setattr(service, "snapshot", broken)
    assert bridge.get_snapshot()["error"]["code"] == "LOCAL_ERROR"
    assert "never-echo" not in json.dumps(bridge.get_snapshot())
    assert trusted_document("file:///safe/index.html#page", "file:///safe/index.html")


def test_dispatch_marker_survives_host_restart_and_blocks_config(service):
    service.save_config(CONFIG)
    (service.state / "desktop-dispatch.lock").write_text("")
    restarted = DesktopService(ROOT, service.state)
    assert restarted.snapshot()["run"]["locked"]
    with pytest.raises(DesktopError, match="BUSY"):
        restarted.save_config(CONFIG)
    with pytest.raises(DesktopError, match="BUSY"):
        restarted.start_operation(request(restarted))


def test_sdk_start_failure_releases_dispatch_without_claiming_success(service, monkeypatch):
    service.save_config(CONFIG)
    service.no_network = False
    monkeypatch.setattr(service, "_sdk_python", lambda: Path(sys.executable))

    def failed(*args, **kwargs):
        raise OSError("do not echo process parameters")

    monkeypatch.setattr(subprocess, "Popen", failed)
    with pytest.raises(DesktopError, match="WORKER_START_FAILED"):
        service.start_operation(request(service))
    assert not (service.state / "desktop-dispatch.lock").exists()
    assert not (service.state / "receipt.json").exists()


def test_worker_timeout_keeps_lock_and_never_logs_child_output(service):
    service.state.mkdir()
    (service.state / "desktop-dispatch.lock").write_text("")
    calls = []

    def communicate(timeout=None):
        if timeout:
            raise subprocess.TimeoutExpired("fixed-sdk-command", timeout)
        return "secret-child-output", "sensitive-sdk-error"

    service._process = SimpleNamespace(communicate=communicate, kill=lambda: calls.append("kill"))
    service._watch()
    assert calls == ["kill"]
    assert (service.state / "desktop-dispatch.lock").exists()
    assert (
        "UNKNOWN_PROCESS_TIMEOUT"
        in json.loads((service.state / "desktop-job.json").read_text())["status"]
    )
    logs = (service.state / "desktop-events.jsonl").read_text()
    assert "secret-child-output" not in logs and "sensitive-sdk-error" not in logs


def test_sdk_status_requires_pinned_metadata_not_only_python_executable(tmp_path, monkeypatch):
    instance = DesktopService(ROOT, tmp_path / "profile", no_network=True)
    executable = tmp_path / "venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(instance, "_sdk_python", lambda: executable)
    assert instance._sdk_ready() is False
    metadata = tmp_path / "venv/Lib/site-packages/wechatpy-0.1.dist-info/direct_url.json"
    atomic_json(metadata, {"vcs_info": {"commit_id": instance._worker.PIN}})
    assert instance._sdk_ready() is True
    atomic_json(metadata, {"vcs_info": {"commit_id": "changed"}})
    assert instance._sdk_ready() is False


@pytest.mark.parametrize("network_mode", ["DIRECT", "ENVIRONMENT_PROXY"])
def test_token_recheck_reuses_explicitly_selected_routing(tmp_path, monkeypatch, network_mode):
    import rancho_wechat.desktop_worker as desktop_worker

    atomic_json(tmp_path / "token-validation.json", {"network_mode": network_mode})
    atomic_json(tmp_path / "authorization.json", {"desktop_permit": "permit-123"})
    received = {}

    def run_locked(mode, **kwargs):
        received.update(mode=mode, **kwargs)
        return {"status": "TEST_CONTRACT_ONLY"}

    stub = SimpleNamespace(load=lambda path: json.loads(path.read_text()), run_locked=run_locked)
    monkeypatch.setattr(desktop_worker, "legacy_worker", lambda *args: stub)
    desktop_worker.run(ROOT, tmp_path, tmp_path / "not-used.png", "token-only", "permit-123")
    assert received["use_env_proxy"] is (network_mode == "ENVIRONMENT_PROXY")
    assert received["authorization_id"] == "permit-123"


@pytest.mark.parametrize("operation", ["token-only", "probe-read-only", "execute"])
def test_read_only_confirmation_never_grants_draft_write_scope(service, monkeypatch, operation):
    import rancho_wechat.desktop as desktop

    service.save_config(CONFIG)
    service.no_network = False
    variants = service.state / "synthetic-variants.json"
    atomic_json(variants, {"wechat_title": "合成测试稿", "wechat_body": "仅合成契约测试"})
    service._worker.VARIANTS = variants
    service._worker.COVER = service.state / "synthetic-cover.png"
    service._worker.COVER.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=123, poll=lambda: None)
    )
    monkeypatch.setattr(
        desktop.threading, "Thread", lambda **k: SimpleNamespace(start=lambda: None)
    )
    service.start_operation(request(service, operation=operation))
    auth = json.loads((service.state / "authorization.json").read_text(encoding="utf-8"))
    expected = (
        "ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY"
        if operation == "execute"
        else "DESKTOP_ACCOUNT_DIAGNOSTICS_ONLY"
    )
    assert auth["scope"] == expected
    assert auth["operation"] == operation
    if operation != "execute":
        assert service._worker.verified_account(service._worker.credentials()) is True
        # The old CLI cannot reuse a diagnostic permit to add a draft.
        assert service._worker.execute()["status"] == "WAITING_AUTHORIZATION"


@pytest.mark.parametrize("reason", ["missing_token", "expired", "account_changed"])
def test_historical_success_cannot_certify_changed_or_expired_credentials(service, reason):
    import time

    service.save_config({**CONFIG, "access_token": "synthetic-token"})
    secret = service._worker.credentials()
    secret["token_expires_at"] = time.time() + 3600
    if reason == "missing_token":
        secret["access_token"] = ""
    if reason == "expired":
        secret["token_expires_at"] = 1
    service._worker.store_credentials(secret)
    atomic_json(
        service.state / "token-validation.json",
        {
            "status": "TOKEN_OBTAINED",
            "token_request_state": "CONFIRMED",
            "credentials_saved": True,
            "app_id_sha256": "other"
            if reason == "account_changed"
            else sha(CONFIG["app_id"].encode()),
        },
    )
    atomic_json(service.state / "permission-probe.json", {"status": "REMOTE_DRAFT_LIST_READ"})
    receipt = service.snapshot()["receipt"]
    assert receipt["token_state"] == "NOT_CURRENT"
    assert receipt["permission_verified"] is False
