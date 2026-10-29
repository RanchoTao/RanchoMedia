"""ASGI-only Director controls; no browser, external HTTP, SDK or real account."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rancho_wechat import network
from rancho_wechat.editorial import EditorialDesk
from rancho_wechat.network import Director
from rancho_wechat.providers import MockProvider
from rancho_wechat.store import Store
from rancho_wechat.web import create_app

TOKEN = "SYNTHETIC_EDITOR_TEST_TOKEN_32_CHARS"
HEADERS = {"X-Editor-Token": TOKEN}


@pytest.fixture
def controls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Director]:
    desk = EditorialDesk(Store(tmp_path / "editor.db"), MockProvider(), tmp_path / "exports")
    root = tmp_path / "isolated-network"
    monkeypatch.setenv("RANCHOMEDIA_NETWORK_ROOT", str(root))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Network-control tests must never send a real platform request")

    monkeypatch.setattr(network.subprocess, "run", forbidden)
    monkeypatch.setattr(Director, "_wechat_draft", forbidden)
    return TestClient(create_app(desk, TOKEN)), Director(desk, root)


@pytest.mark.parametrize("token", [None, "wrong-token"])
@pytest.mark.parametrize("route", ["status", "pause", "tick"])
def test_network_controls_require_editor_token_before_any_action(
    controls: tuple[TestClient, Director], token: str | None, route: str
) -> None:
    client, director = controls
    headers = {} if token is None else {"X-Editor-Token": token}
    if route == "status":
        response = client.get("/api/network/status", headers=headers)
    elif route == "pause":
        response = client.post("/api/network/pause", json={"paused": True}, headers=headers)
    else:
        response = client.post("/api/network/tick", headers=headers)
    assert response.status_code == 401
    assert not (director.root / "operator-policy.json").exists()
    assert not director.tasks()
    assert not director.desk.audit("network", "all")


def test_authorized_status_is_read_only_and_exposes_no_editor_token(
    controls: tuple[TestClient, Director],
) -> None:
    client, director = controls
    response = client.get("/api/network/status", headers=HEADERS)
    assert response.status_code == 200
    status = response.json()
    assert status["mode"] == "SHADOW" and status["legacy_approvals_changed"] is False
    assert not any(channel["published"] for channel in status["channels"].values())
    assert TOKEN not in response.text
    assert not (director.root / "operator-policy.json").exists()


def test_global_pause_persists_and_tick_obeys_it_without_dispatch(
    controls: tuple[TestClient, Director],
) -> None:
    client, director = controls
    paused = client.post("/api/network/pause", json={"paused": True}, headers=HEADERS)
    assert paused.status_code == 200 and paused.json()["paused"] is True
    reopened = Director(director.desk, director.root)
    assert reopened.policy().paused is True
    tick = client.post("/api/network/tick", headers=HEADERS)
    assert tick.status_code == 200 and tick.json() == {"status": "PAUSED", "processed": 0}
    assert director.desk.audit("network", "all")[-1]["action"] == "PAUSE"
    resumed = client.post("/api/network/pause", json={"paused": False}, headers=HEADERS)
    assert resumed.status_code == 200 and resumed.json()["paused"] is False
    assert director.desk.audit("network", "all")[-1]["action"] == "RESUME"
    assert director.policy().mode == "SHADOW"
    assert all(not account.enabled for account in director.policy().channels.values())


def test_channel_pause_changes_only_selected_channel(
    controls: tuple[TestClient, Director],
) -> None:
    client, director = controls
    response = client.post(
        "/api/network/pause", json={"paused": True, "channel": "wechat"}, headers=HEADERS
    )
    assert response.status_code == 200
    status = response.json()
    assert status["paused"] is False and status["channels"]["wechat"]["paused"] is True
    assert all(
        not channel["paused"] for name, channel in status["channels"].items() if name != "wechat"
    )
    assert director.desk.audit("network", "wechat")[-1]["action"] == "PAUSE"


@pytest.mark.parametrize("body", [{"paused": True, "channel": "unknown-platform"}, {}])
def test_invalid_pause_cannot_change_policy_or_create_audit(
    controls: tuple[TestClient, Director], body: dict
) -> None:
    client, director = controls
    response = client.post("/api/network/pause", json=body, headers=HEADERS)
    assert response.status_code == 422
    assert not (director.root / "operator-policy.json").exists()
    assert not director.desk.audit("network", "all")


def test_authorized_empty_tick_has_no_remote_or_permission_side_effect(
    controls: tuple[TestClient, Director],
) -> None:
    client, director = controls
    response = client.post("/api/network/tick", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "TICK_COMPLETE", "processed": 0, "results": []}
    assert not director.tasks()
    assert not (director.root / "operator-policy.json").exists()
