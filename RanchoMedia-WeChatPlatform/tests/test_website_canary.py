"""Isolated website-canary regressions; every Git, gh and HTTP operation is synthetic."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from rancho_wechat.editorial import EditorialDesk
from rancho_wechat.network import Director
from rancho_wechat.providers import MockProvider
from rancho_wechat.store import Store


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/publish_website_canary.py"
    spec = importlib.util.spec_from_file_location("synthetic_website_publisher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("No real subprocess, HTTP call, or sleep is allowed in canary tests")

    monkeypatch.setattr(module.subprocess, "check_output", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(module.time, "sleep", forbidden)
    return module


@pytest.fixture
def canary(
    tmp_path: Path, publisher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    repo = tmp_path / "isolated-site"
    relative = "content/rancho/00000000-0000-0000-0000-000000000001.md"
    file = repo / relative
    file.parent.mkdir(parents=True)
    file.write_text("TEST ONLY new technical explainer", encoding="utf-8")
    auth = {
        "repository": "RanchoTao/RanchoTao.github.io",
        "account": "RanchoTao",
        "permission": "SINGLE_ARTICLE_PUBLIC_CANARY",
        "path": relative,
        "public_url": "https://ranchotao.com/" + relative.removesuffix(".md") + "/",
        "base": "a" * 40,
        "head": "b" * 40,
        "file_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
    }
    state = SimpleNamespace(
        repo=repo,
        auth=auth,
        output=tmp_path / "receipt.json",
        calls=[],
        remote=auth["base"],
        names=[relative],
        push_error=False,
        http_calls=[],
    )

    def command(args: list[str], cwd: Path) -> str:
        assert cwd == repo
        state.calls.append(args)
        if args[:3] == ["git", "status", "--porcelain"]:
            return ""
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return auth["head"]
        if args[:3] == ["git", "diff", "--name-only"]:
            return "\n".join(state.names)
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return "https://github.com/RanchoTao/RanchoTao.github.io.git"
        if args[:3] == ["git", "push", "origin"]:
            durable = json.loads(state.output.read_text(encoding="utf-8"))
            assert durable["status"] == "AUTHORIZED_PREPARED"
            assert durable["pushed"] is False
            if state.push_error:
                raise subprocess.TimeoutExpired(args, 45)
            return "SYNTHETIC push response"
        if args[:2] == ["gh", "api"]:
            if "/git/ref/" in args[2]:
                return state.remote
            if "/actions/runs?" in args[2]:
                return json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "name": "Build and Deploy Jekyll Site",
                                "html_url": "https://example.invalid/SYNTHETIC-run",
                                "status": "completed",
                                "conclusion": "success",
                            }
                        ]
                    }
                )
        pytest.fail(f"Unexpected synthetic command: {args}")

    monkeypatch.setattr(publisher, "command", command)
    return state


def pushes(canary: SimpleNamespace) -> list[list[str]]:
    return [call for call in canary.calls if call[:2] == ["git", "push"]]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "different/unauthorized"),
        ("account", "different-account"),
        ("permission", "PUBLISH_EVERYTHING"),
        ("path", "README.md"),
        ("public_url", "https://example.invalid/other/"),
    ],
)
def test_authorization_mismatch_rejected_before_any_command(
    publisher: ModuleType, canary: SimpleNamespace, field: str, value: str
) -> None:
    canary.auth[field] = value
    with pytest.raises(ValueError):
        publisher.publish(canary.repo, canary.auth, canary.output)
    assert not canary.calls and not canary.output.exists()


def test_extra_changed_file_exceeds_single_article_authorization(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    canary.names.append("_config.yml")
    with pytest.raises(ValueError, match="single authorized article"):
        publisher.publish(canary.repo, canary.auth, canary.output)
    assert not pushes(canary) and not canary.output.exists()


def test_authorized_file_byte_change_stops_before_push(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    (canary.repo / canary.auth["path"]).write_text("CHANGED AFTER APPROVAL", encoding="utf-8")
    with pytest.raises(ValueError, match="file bytes changed"):
        publisher.publish(canary.repo, canary.auth, canary.output)
    assert not pushes(canary)


def test_remote_branch_advance_requires_human_review_without_push(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    canary.remote = "c" * 40
    with pytest.raises(ValueError, match="Remote advanced"):
        publisher.publish(canary.repo, canary.auth, canary.output)
    assert not pushes(canary) and not canary.output.exists()


def test_push_unknown_stops_after_one_mutation_without_polling(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    canary.push_error = True
    result = publisher.publish(canary.repo, canary.auth, canary.output)
    assert result["status"] == "UNKNOWN_REMOTE_STATUS_RECONCILE_ONLY"
    assert len(pushes(canary)) == 1
    assert not any("/actions/runs?" in " ".join(call) for call in canary.calls)
    assert json.loads(canary.output.read_text(encoding="utf-8"))["status"] == result["status"]


def test_existing_unknown_intent_cannot_be_resent_on_repeated_invocation(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    canary.push_error = True
    first = publisher.publish(canary.repo, canary.auth, canary.output)
    second = publisher.publish(canary.repo, canary.auth, canary.output)
    assert first["status"] == second["status"] == "UNKNOWN_REMOTE_STATUS_RECONCILE_ONLY"
    assert len(pushes(canary)) == 1


@pytest.mark.parametrize("channel", ["global", "website"])
def test_operator_pause_prevents_authorized_website_push(
    publisher: ModuleType, canary: SimpleNamespace, channel: str
) -> None:
    policy = {
        "paused": channel == "global",
        "channels": {"website": {"paused": channel == "website"}},
    }
    (canary.output.parent / "operator-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    result = publisher.publish(canary.repo, canary.auth, canary.output)
    assert result["status"] == "PAUSED" and result["pushed"] is False
    assert not pushes(canary)
    assert not any("/actions/runs?" in " ".join(call) for call in canary.calls)
    assert json.loads(canary.output.read_text(encoding="utf-8"))["status"] == "PAUSED"


def test_crash_after_durable_prepared_intent_is_reconcile_only(
    publisher: ModuleType, canary: SimpleNamespace
) -> None:
    canary.output.write_text(
        json.dumps({"status": "AUTHORIZED_PREPARED", "remote_id": canary.auth["head"]}),
        encoding="utf-8",
    )
    result = publisher.publish(canary.repo, canary.auth, canary.output)
    assert result["status"] == "UNKNOWN_REMOTE_STATUS_RECONCILE_ONLY"
    assert not pushes(canary)


def test_remote_already_at_head_only_reads_workflow_and_public_page(
    publisher: ModuleType, canary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary.remote = canary.auth["head"]
    page = (
        "<article>暂存区也称索引 一个假设性的Markdown修改 "
        "git-scm.com/docs/git-commit git-scm.com/docs/git-push</article>"
    )

    class SyntheticResponse:
        status = 200

        def __enter__(self) -> SyntheticResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 2_000_000
            return page.encode("utf-8")

    def read_only(request: Any, timeout: int) -> SyntheticResponse:
        assert request.full_url == canary.auth["public_url"] and timeout == 20
        canary.http_calls.append(request.full_url)
        return SyntheticResponse()

    monkeypatch.setattr(publisher.urllib.request, "urlopen", read_only)
    result = publisher.publish(canary.repo, canary.auth, canary.output)
    assert result["status"] == "PUBLICLY_VERIFIED" and result["public_url_verified"] is True
    assert not pushes(canary) and len(canary.http_calls) == 1
    assert all(result["content_checks"].values())
    assert result["public_html_sha256"] == hashlib.sha256(page.encode("utf-8")).hexdigest()


@pytest.fixture
def receipt_case(tmp_path: Path, publisher: ModuleType) -> SimpleNamespace:
    root = tmp_path / "network"
    director = Director(
        EditorialDesk(Store(tmp_path / "editor.db"), MockProvider(), tmp_path / "exports"), root
    )
    path = root / "synthetic-receipt.json"
    snapshot = path.with_suffix(".public.html")
    snapshot.write_text("SYNTHETIC public snapshot, not fetched", encoding="utf-8")
    auth = {
        "head": "b" * 40,
        "path": "content/rancho/00000000-0000-0000-0000-000000000001.md",
        "public_url": "https://ranchotao.com/SYNTHETIC/",
        "file_sha256": "a" * 64,
        "permission": "SINGLE_ARTICLE_PUBLIC_CANARY",
    }
    receipt = {
        "status": "PUBLICLY_VERIFIED",
        "remote_id": auth["head"],
        "path": auth["path"],
        "public_url": auth["public_url"],
        "repository": "RanchoTao/RanchoTao.github.io",
        "file_sha256": auth["file_sha256"],
        "pushed": True,
        "deployed": True,
        "http_status": 200,
        "public_url_verified": True,
        "content_checks": {"SYNTHETIC required passage": True},
        "public_html_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    }
    production = {"canonical_content_id": "SYNTHETIC-content", "version_id": "SYNTHETIC-version"}
    for name, value in (
        ("website-canary-authorization.json", auth),
        ("reviewed-production.json", production),
        (path.name, receipt),
    ):
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    payload = {"channel": "website", **production}
    with director.store.write() as db:
        db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "channel-intent:SYNTHETIC-website",
                "CHANNEL_INTENT",
                "WAITING_AUTHORIZATION",
                json.dumps(payload),
                0,
                None,
                None,
                "2020-01-01T00:00:00+00:00",
                "2020-01-01T00:00:00+00:00",
            ),
        )
    return SimpleNamespace(director=director, path=path, snapshot=snapshot, receipt=receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_url_verified", False),
        ("deployed", False),
        ("pushed", False),
        ("http_status", 404),
        ("content_checks", {}),
        ("content_checks", {"SYNTHETIC required passage": False}),
        ("remote_id", "c" * 40),
    ],
)
def test_receipt_without_complete_matching_public_verification_is_rejected(
    receipt_case: SimpleNamespace, field: str, value: Any
) -> None:
    receipt_case.receipt[field] = value
    receipt_case.path.write_text(json.dumps(receipt_case.receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt and authorization"):
        receipt_case.director.record_website_receipt(receipt_case.path)
    assert receipt_case.director.tasks()[0]["status"] == "WAITING_AUTHORIZATION"
    assert receipt_case.director.status()["channels"]["website"]["published"] is False


def test_modified_public_snapshot_cannot_be_recorded(receipt_case: SimpleNamespace) -> None:
    receipt_case.snapshot.write_text("Tampered saved page", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        receipt_case.director.record_website_receipt(receipt_case.path)
    assert receipt_case.director.tasks()[0]["status"] == "WAITING_AUTHORIZATION"


def test_valid_synthetic_receipt_updates_only_existing_intent_not_future_permissions(
    receipt_case: SimpleNamespace,
) -> None:
    director = receipt_case.director
    before = director.policy().model_dump()
    director.record_website_receipt(receipt_case.path)
    assert director.tasks()[0]["status"] == "PUBLICLY_VERIFIED"
    assert director.status()["channels"]["website"]["published"] is True
    assert director.policy().model_dump() == before
    assert director.policy().mode == "SHADOW"
    assert director.desk.audit("network", "channel-intent:SYNTHETIC-website")[-1]["action"] == (
        "SINGLE_WEBSITE_CANARY_VERIFIED"
    )
