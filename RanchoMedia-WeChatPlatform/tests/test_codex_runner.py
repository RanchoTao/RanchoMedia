from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rancho_wechat import codex_runner
from rancho_wechat.api_provider import ModelError
from rancho_wechat.store import Store


def inputs(tmp_path):
    prompt = tmp_path / "prompt.txt"
    schema = tmp_path / "schema.json"
    prompt.write_text("Synthetic no-tools writing contract", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    return prompt, schema


def test_supported_cli_shape_budget_cache_and_tamper(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(codex_runner, "codex_command", lambda: ["synthetic-codex"])

    class Process:
        returncode = 0

        def __init__(self, args, **kwargs):
            calls.append(args)
            self.output = Path(args[args.index("--output-last-message") + 1])
            self.events = kwargs["stdout"]

        def communicate(self, text, timeout):
            assert "不要读取凭据" in text
            self.output.write_text('{"title":"contract"}', encoding="utf-8")
            self.events.write(
                json.dumps(
                    {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}
                )
                + "\n"
            )

    monkeypatch.setattr(codex_runner.subprocess, "Popen", Process)
    store = Store(tmp_path / "db.sqlite")
    prompt, schema = inputs(tmp_path)
    first = codex_runner.run_once(store, tmp_path / "runs", prompt, schema)
    assert first["status"] == "SUCCEEDED"
    assert first["billing_amount"] == "UNKNOWN"
    assert "--sandbox" in calls[0] and "read-only" in calls[0]
    assert "--dangerously-bypass-approvals-and-sandbox" not in calls[0]
    assert codex_runner.run_once(store, tmp_path / "runs", prompt, schema)["status"] == "CACHED"
    assert len(calls) == 1
    Path(first["output"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ModelError) as error:
        codex_runner.run_once(store, tmp_path / "runs", prompt, schema)
    assert error.value.code == "CODEX_OUTCOME_REQUIRES_REVIEW"
    prompt.write_text("A different request", encoding="utf-8")
    with pytest.raises(ModelError) as budget:
        codex_runner.run_once(store, tmp_path / "runs", prompt, schema)
    assert budget.value.code == "CODEX_DAILY_LIMIT"
    assert len(calls) == 1


def test_timeout_records_unknown_and_never_retries(tmp_path: Path, monkeypatch):
    calls, stopped = [], []
    monkeypatch.setattr(codex_runner, "codex_command", lambda: ["synthetic-codex"])
    monkeypatch.setattr(
        codex_runner.os, "killpg", lambda *args: stopped.append("killpg"), raising=False
    )

    class Process:
        pid = 900001
        returncode = None

        def __init__(self, args, **kwargs):
            calls.append(args)

        def communicate(self, text, timeout):
            raise codex_runner.subprocess.TimeoutExpired("synthetic", timeout)

        def kill(self):
            stopped.append("kill")

        def wait(self, timeout):
            stopped.append("wait")

    monkeypatch.setattr(codex_runner.subprocess, "Popen", Process)
    monkeypatch.setattr(
        codex_runner.subprocess,
        "run",
        lambda args, **kwargs: stopped.append(args) or SimpleNamespace(returncode=0),
    )
    store = Store(tmp_path / "db.sqlite")
    prompt, schema = inputs(tmp_path)
    result = codex_runner.run_once(store, tmp_path / "runs", prompt, schema, timeout=10)
    assert result["status"] == "UNKNOWN_TIMEOUT"
    assert "kill" in stopped
    with pytest.raises(ModelError):
        codex_runner.run_once(store, tmp_path / "runs", prompt, schema)
    assert len(calls) == 1


def test_completed_receipt_reconciles_db_crash_without_new_call(tmp_path, monkeypatch):
    import hashlib
    from datetime import UTC, datetime

    monkeypatch.setattr(codex_runner, "codex_command", lambda: ["synthetic-codex"])
    monkeypatch.setattr(codex_runner.subprocess, "Popen", lambda *a, **k: pytest.fail("new send"))
    prompt, schema = inputs(tmp_path)
    key = hashlib.sha256((prompt.read_text() + "\n" + schema.read_text()).encode()).hexdigest()
    run_root = tmp_path / "runs" / key
    run_root.mkdir(parents=True)
    answer = run_root / "answer.json"
    answer.write_text('{"title":"TEST ONLY"}', encoding="utf-8")
    (run_root / "receipt.json").write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "run_key": key,
                "output_sha256": hashlib.sha256(answer.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    store = Store(tmp_path / "db.sqlite")
    with store.write() as db:
        db.execute(
            "INSERT INTO codex_runs VALUES(?,?,?,?,?,?)",
            (key, "2026-10-21", "RUNNING", str(answer), None, datetime.now(UTC).isoformat()),
        )
    assert codex_runner.run_once(store, tmp_path / "runs", prompt, schema)["status"] == "CACHED"
    with store.connection() as db:
        assert db.execute("SELECT status FROM codex_runs").fetchone()[0] == "SUCCEEDED"
