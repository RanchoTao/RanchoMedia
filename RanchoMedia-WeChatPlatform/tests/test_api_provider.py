from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rancho_wechat.api_provider import APIPolicy, CompatibleProvider, ModelError, RoleConfig
from rancho_wechat.store import Store


def response(answer: dict | None = None, *, usage: bool = True) -> bytes:
    result = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(answer or {"note": "contract-ok"})},
            }
        ]
    }
    if usage:
        result["usage"] = {"prompt_tokens": 123, "completion_tokens": 45}
    return json.dumps(result).encode()


@contextmanager
def server(replies: list[tuple[int, bytes, float]]):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                }
            )
            status, body, delay = replies.pop(0)
            time.sleep(delay)
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1", received
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def client(tmp_path: Path, monkeypatch, endpoint: str, **policy) -> CompatibleProvider:
    monkeypatch.setenv("WECHAT_TEST_KEY", "synthetic-secret-not-a-real-key")
    roles = {
        role: RoleConfig(
            endpoint, "test-" + role, "WECHAT_TEST_KEY", Decimal("1"), Decimal("2"), 100
        )
        for role in ("research", "write", "review")
    }
    return CompatibleProvider(
        Store(tmp_path / "db.sqlite"),
        roles,
        replace(APIPolicy(enabled=True), **policy),
        allow_test_http=True,
        sleeper=lambda _: None,
    )


def test_real_http_contract_roles_usage_and_restart_cache(tmp_path: Path, monkeypatch):
    with server(
        [
            (200, response(), 0),
            (200, response({"body": "draft"}), 0),
            (200, response({"status": "COMPLETE"}), 0),
        ]
    ) as (url, received):
        provider = client(tmp_path, monkeypatch, url)
        provider.generate_json("research", {"sources": []})
        provider.generate_json(
            "draft",
            {
                "style": {
                    "preferences": [{"text": "unconfirmed-pattern"}],
                    "confirmed_rules": [{"text": "confirmed-only"}],
                }
            },
        )
        provider.generate_json("review", {"body": "draft"})
        reopened = client(tmp_path, monkeypatch, url)
        assert reopened.generate_json("research", {"sources": []}) == {"note": "contract-ok"}
    assert len(received) == 3
    assert [request["body"]["model"] for request in received] == [
        "test-research",
        "test-write",
        "test-review",
    ]
    assert all(request["path"] == "/v1/chat/completions" for request in received)
    assert received[0]["body"]["response_format"] == {"type": "json_object"}
    assert "unconfirmed-pattern" not in json.dumps(received[1]["body"])
    assert "confirmed-only" in json.dumps(received[1]["body"])
    usage = reopened.usage()
    assert usage["attempts"] == 3 and usage["tokens_charged_or_reserved"] == 504
    assert "synthetic-secret" not in json.dumps(usage)


def test_rate_limit_is_bounded_and_each_send_counts(tmp_path: Path, monkeypatch):
    with server([(429, b"", 0), (429, b"", 0)]) as (url, received):
        provider = client(tmp_path, monkeypatch, url)
        with pytest.raises(ModelError) as error:
            provider.generate_json("research", {})
        assert error.value.code == "RATE_LIMITED"
        assert len(received) == provider.usage()["attempts"] == 2


def test_timeout_unknown_is_not_resent_after_restart(tmp_path: Path, monkeypatch):
    with server([(200, response(), 1.2)]) as (url, received):
        provider = client(tmp_path, monkeypatch, url, timeout_seconds=1)
        with pytest.raises(ModelError) as error:
            provider.generate_json("research", {})
        assert error.value.code == "OUTCOME_UNKNOWN"
        reopened = client(tmp_path, monkeypatch, url)
        with pytest.raises(ModelError, match="不会自动重发"):
            reopened.generate_json("research", {})
        assert len(received) == 1
        call = reopened.usage()["calls"][0]
        assert call["status"] == "UNKNOWN"
        assert call["charged_micros"] > 0
        reopened.resolve_no_result(
            call["request_key"], "TEST ONLY：已在上游控制台核对没有输出，保留费用预留"
        )


@pytest.mark.parametrize(
    "budget", [{"daily_calls": 1}, {"daily_tokens": 10}, {"daily_cost": Decimal("0.000001")}]
)
def test_call_token_and_money_budgets_are_hard_local_gates(tmp_path: Path, monkeypatch, budget):
    with server([(200, response(), 0)]) as (url, received):
        provider = client(tmp_path, monkeypatch, url, **budget)
        if budget.get("daily_calls") == 1:
            provider.generate_json("research", {"call": "first"})
        with pytest.raises(ModelError) as error:
            provider.generate_json("research", {"call": "next"})
        assert error.value.code == "DAILY_BUDGET_EXCEEDED"
        assert len(received) <= 1


def test_missing_key_disabled_invalid_output_and_unknown_cost(tmp_path: Path, monkeypatch):
    with server([(200, b"not JSON", 0), (200, response(usage=False), 0)]) as (url, received):
        provider = client(tmp_path, monkeypatch, url)
        monkeypatch.delenv("WECHAT_TEST_KEY")
        with pytest.raises(ModelError) as missing:
            provider.generate_json("research", {})
        assert missing.value.code == "MISSING_API_KEY" and not received
        monkeypatch.setenv("WECHAT_TEST_KEY", "synthetic-secret")
        with pytest.raises(ModelError) as invalid:
            provider.generate_json("research", {})
        assert invalid.value.code == "INVALID_MODEL_OUTPUT"
        provider.generate_json("research", {"different": True})
        assert provider.usage()["calls"][1]["usage_json"] is None
        disabled = CompatibleProvider(
            provider.store, provider.roles, APIPolicy(), allow_test_http=True
        )
        with pytest.raises(ModelError) as error:
            disabled.generate_json("research", {})
        assert error.value.code == "LIVE_MODEL_DISABLED"


def test_500_and_redirect_do_not_expose_upstream_or_resend(tmp_path: Path, monkeypatch):
    with server([(500, b"echo synthetic-secret", 0)]) as (url, received):
        provider = client(tmp_path, monkeypatch, url)
        with pytest.raises(ModelError) as error:
            provider.generate_json("research", {})
        assert error.value.code == "OUTCOME_UNKNOWN"
        assert "synthetic-secret" not in str(error.value)
        assert len(received) == 1
