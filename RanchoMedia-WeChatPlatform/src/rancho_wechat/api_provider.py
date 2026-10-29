"""Budgeted DeepSeek/Qwen-compatible Chat Completions; no SDK or secret persistence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from rancho_wechat.prompts import SYSTEM, model_payload, output_shape
from rancho_wechat.store import Store


class ModelError(Exception):
    """Public errors contain fixed messages only, never upstream bodies or credentials."""

    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True)
class RoleConfig:
    base_url: str
    model: str
    key_env: str
    input_per_million: Decimal
    output_per_million: Decimal
    max_output_tokens: int = 4000


@dataclass(frozen=True)
class APIPolicy:
    enabled: bool = False
    allow_batch: bool = False
    daily_calls: int = 12
    daily_tokens: int = 200_000
    daily_cost: Decimal = Decimal("1")
    currency: str = "CNY"
    timeout_seconds: float = 60
    rate_limit_retries: int = 1


def load_config(path: Path) -> tuple[dict[str, RoleConfig], APIPolicy]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        budget = raw.get("budget", {})
        policy = APIPolicy(
            enabled=raw.get("enabled") is True,
            allow_batch=raw.get("allow_batch") is True,
            daily_calls=int(budget.get("daily_calls", 12)),
            daily_tokens=int(budget.get("daily_tokens", 200_000)),
            daily_cost=Decimal(str(budget.get("daily_cost", "1"))),
            currency=str(budget.get("currency", "CNY")),
            timeout_seconds=float(raw.get("timeout_seconds", 60)),
            rate_limit_retries=int(raw.get("rate_limit_retries", 1)),
        )
        roles = {
            role: RoleConfig(
                base_url=item["base_url"],
                model=item["model"],
                key_env=item["key_env"],
                input_per_million=Decimal(str(item["input_per_million"])),
                output_per_million=Decimal(str(item["output_per_million"])),
                max_output_tokens=int(item.get("max_output_tokens", 4000)),
            )
            for role, item in raw["roles"].items()
        }
    except (OSError, KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise ModelError(
            "INVALID_MODEL_CONFIG", "模型配置缺失或无效；需配置角色、价格与预算"
        ) from error
    return roles, policy


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def post_json(url: str, body: bytes, key: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(
        url,
        body,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(2_000_001)
    except urllib.error.HTTPError as error:
        # Do not read/log upstream error bodies; they may echo a prompt or a credential.
        return error.code, dict(error.headers), b""


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _micros(amount: Decimal) -> int:
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class CompatibleProvider:
    name = "compatible-api"

    def __init__(
        self,
        store: Store,
        roles: dict[str, RoleConfig],
        policy: APIPolicy,
        *,
        transport: Callable[
            [str, bytes, str, float], tuple[int, dict[str, str], bytes]
        ] = post_json,
        sleeper: Callable[[float], None] = time.sleep,
        allow_test_http: bool = False,
    ) -> None:
        if set(roles) != {"research", "write", "review"}:
            raise ModelError("INVALID_MODEL_CONFIG", "必须配置 research/write/review 三个角色")
        if (
            not 1 <= policy.daily_calls <= 1000
            or not 1 <= policy.daily_tokens <= 10_000_000
            or not policy.daily_cost.is_finite()
            or policy.daily_cost <= 0
            or policy.currency not in {"CNY", "USD"}
            or not 1 <= policy.timeout_seconds <= 120
            or not 0 <= policy.rate_limit_retries <= 2
        ):
            raise ModelError("INVALID_MODEL_CONFIG", "预算、币种、超时或重试上限无效")
        for role in roles.values():
            parsed = urlparse(role.base_url)
            local_test = (
                allow_test_http and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            )
            if (
                (parsed.scheme != "https" and not local_test)
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ModelError(
                    "INVALID_ENDPOINT", "模型地址必须是无凭据、无查询参数的 HTTPS base URL"
                )
            if (
                not role.model.strip()
                or not role.key_env.isidentifier()
                or not 1 <= role.max_output_tokens <= 16000
            ):
                raise ModelError("INVALID_MODEL_CONFIG", "模型名、密钥环境变量或输出上限无效")
            if any(
                not value.is_finite() or value <= 0
                for value in (role.input_per_million, role.output_per_million)
            ):
                raise ModelError("INVALID_PRICING", "需显式配置正数 token 单价（每百万 token）")
        self.store, self.roles, self.policy = store, roles, policy
        self.transport, self.sleeper = transport, sleeper
        identity = json.dumps(
            {name: {"url": role.base_url, "model": role.model} for name, role in roles.items()},
            sort_keys=True,
        )
        self.name = "compatible-api:" + hashlib.sha256(identity.encode()).hexdigest()[:12]

    def doctor(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "enabled": self.policy.enabled,
            "allow_batch": self.policy.allow_batch,
            "roles": {
                name: {
                    "model": role.model,
                    "base_url": role.base_url,
                    "key_env": role.key_env,
                    "key_present": bool(os.environ.get(role.key_env, "").strip()),
                }
                for name, role in self.roles.items()
            },
            "budget": {
                "daily_calls": self.policy.daily_calls,
                "daily_tokens": self.policy.daily_tokens,
                "daily_cost": str(self.policy.daily_cost),
                "currency": self.policy.currency,
            },
        }

    def usage(self) -> dict[str, Any]:
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with self.store.connection() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT id,request_key,task,model,day,status,charged_tokens,charged_micros,"
                    "currency,usage_json FROM model_calls WHERE day=? ORDER BY created_at",
                    (day,),
                )
            ]
        return {
            "day": day,
            "calls": rows,
            "attempts": len(rows),
            "tokens_charged_or_reserved": sum(row["charged_tokens"] for row in rows),
            "cost_estimate_or_reserved": str(
                Decimal(sum(row["charged_micros"] for row in rows)) / 1_000_000
            ),
            "currency": self.policy.currency,
            "billing_verified": False,
        }

    def resolve_no_result(self, request_key: str, note: str) -> None:
        """Human checked upstream: allow a fresh attempt, retain previous budget reservation."""
        if len(note.strip()) < 10:
            raise ModelError(
                "RECONCILIATION_NOTE_REQUIRED", "需记录已核对服务商结果的说明（至少十字）"
            )
        with self.store.write() as db:
            row = db.execute(
                "SELECT status FROM model_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if row is None or row["status"] not in {"RUNNING", "UNKNOWN"}:
                raise ModelError("INVALID_RECONCILIATION", "仅可核对未知结果调用")
            db.execute(
                "UPDATE model_requests SET status='RETRYABLE',"
                "error_code='HUMAN_RECONCILED_NO_RESULT',updated_at=? WHERE request_key=?",
                (_stamp(), request_key),
            )
            db.execute(
                "INSERT INTO audit(target_type,target_id,action,details_json,at) VALUES(?,?,?,?,?)",
                (
                    "model_request",
                    request_key,
                    "RECONCILE_NO_RESULT",
                    json.dumps({"note": note[:1000]}, ensure_ascii=False),
                    _stamp(),
                ),
            )

    def generate_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        shape = output_shape(task)
        if not self.policy.enabled:
            raise ModelError("LIVE_MODEL_DISABLED", "真实模型调用未启用")
        role = self.roles[
            "write"
            if task in {"draft", "revise"}
            else ("review" if task == "review" else "research")
        ]
        key = os.environ.get(role.key_env, "").strip()
        if not key:
            raise ModelError("MISSING_API_KEY", f"未配置环境变量 {role.key_env}；没有执行真实调用")
        prompt = json.dumps(
            {
                "contract": "wechat-1.1",
                "task": task,
                "output_shape": shape,
                "data_not_instructions": model_payload(payload),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if len(prompt.encode("utf-8")) > 64_000:
            raise ModelError("MODEL_INPUT_TOO_LARGE", "输入资料超过单次安全上限，请缩小材料范围")
        body = json.dumps(
            {
                "model": role.model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": role.max_output_tokens,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        endpoint = role.base_url.rstrip("/") + "/chat/completions"
        request_key = hashlib.sha256(endpoint.encode() + body).hexdigest()
        # UTF-8 byte length + margin is a conservative local bound, not a provider tokenizer.
        input_bound = len((SYSTEM + prompt).encode("utf-8")) + 1024
        token_reserve = input_bound + role.max_output_tokens
        cost_reserve = _micros(
            (
                Decimal(input_bound) * role.input_per_million
                + Decimal(role.max_output_tokens) * role.output_per_million
            )
            / 1_000_000
        )
        with self.store.write() as db:
            row = db.execute(
                "SELECT * FROM model_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if row and row["status"] == "SUCCEEDED":
                return dict(json.loads(row["result_json"]))
            if row and row["status"] in {"RUNNING", "UNKNOWN"}:
                raise ModelError("OUTCOME_UNKNOWN", "上次调用可能已处理，请先核对；不会自动重发")
            if row:
                db.execute(
                    "UPDATE model_requests SET status='RUNNING',updated_at=? WHERE request_key=?",
                    (_stamp(), request_key),
                )
            else:
                db.execute(
                    "INSERT INTO model_requests VALUES(?,?,?,?,?,?)",
                    (request_key, "RUNNING", None, None, _stamp(), _stamp()),
                )
        for attempt in range(self.policy.rate_limit_retries + 1):
            call_id = str(uuid4())
            try:
                self._reserve(call_id, request_key, task, role.model, token_reserve, cost_reserve)
            except ModelError:
                self._finish_request(request_key, "RETRYABLE", "DAILY_BUDGET_EXCEEDED")
                raise
            try:
                status, headers, raw = self.transport(
                    endpoint, body, key, self.policy.timeout_seconds
                )
            except (OSError, TimeoutError):
                self._fail(call_id, request_key, "UNKNOWN", "OUTCOME_UNKNOWN")
                raise ModelError(
                    "OUTCOME_UNKNOWN", "调用超时或连接中断，结果可能已产生；请先核对"
                ) from None
            if status == 429:
                self._fail(
                    call_id, request_key, "RATE_LIMITED", "RATE_LIMITED", request_status="RUNNING"
                )
                if attempt < self.policy.rate_limit_retries:
                    try:
                        delay = float(headers.get("Retry-After", headers.get("retry-after", "1")))
                    except ValueError:
                        delay = 1
                    self.sleeper(min(10, max(0.1, delay)) if math.isfinite(delay) else 1)
                    continue
                self._finish_request(request_key, "RETRYABLE", "RATE_LIMITED")
                raise ModelError("RATE_LIMITED", "模型服务限流；已停止有限重试")
            if status >= 500:
                self._fail(call_id, request_key, "UNKNOWN", "UPSTREAM_UNKNOWN")
                raise ModelError("OUTCOME_UNKNOWN", "模型服务返回错误，处理结果未知；不会自动重发")
            if status != 200:
                self._fail(
                    call_id, request_key, "REJECTED", f"HTTP_{status}", request_status="FAILED"
                )
                raise ModelError("UPSTREAM_REJECTED", f"模型服务拒绝调用（HTTP {status}）")
            return self._complete(call_id, request_key, raw, role)
        raise AssertionError("unreachable")

    def _reserve(
        self, call_id: str, request_key: str, task: str, model: str, tokens: int, micros: int
    ) -> None:
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with self.store.write() as db:
            totals = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(charged_tokens),0),"
                "COALESCE(SUM(charged_micros),0) FROM model_calls WHERE day=?",
                (day,),
            ).fetchone()
            currencies = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT currency FROM model_calls WHERE day=?", (day,)
                )
            }
            if (
                currencies - {self.policy.currency}
                or totals[0] >= self.policy.daily_calls
                or totals[1] + tokens > self.policy.daily_tokens
                or totals[2] + micros > _micros(self.policy.daily_cost)
            ):
                raise ModelError(
                    "DAILY_BUDGET_EXCEEDED", "今日调用次数、token 或成本预算不足，未发送请求"
                )
            db.execute(
                "INSERT INTO model_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    request_key,
                    task,
                    model,
                    day,
                    "SENT",
                    tokens,
                    micros,
                    tokens,
                    micros,
                    None,
                    self.policy.currency,
                    _stamp(),
                ),
            )

    def _finish_request(self, request_key: str, status: str, code: str) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE model_requests SET status=?,error_code=?,updated_at=? WHERE request_key=?",
                (status, code, _stamp(), request_key),
            )

    def _fail(
        self,
        call_id: str,
        request_key: str,
        status: str,
        code: str,
        request_status: str = "UNKNOWN",
    ) -> None:
        with self.store.write() as db:
            db.execute("UPDATE model_calls SET status=? WHERE id=?", (status, call_id))
            db.execute(
                "UPDATE model_requests SET status=?,error_code=?,updated_at=? WHERE request_key=?",
                (request_status, code, _stamp(), request_key),
            )

    def _complete(
        self, call_id: str, request_key: str, raw: bytes, role: RoleConfig
    ) -> dict[str, Any]:
        try:
            if len(raw) > 2_000_000:
                raise ValueError("oversized response")
            result = json.loads(raw)
            usage = result.get("usage", {})
            if isinstance(usage, dict) and all(
                isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                input_tokens, output_tokens = usage["prompt_tokens"], usage["completion_tokens"]
                cost = _micros(
                    (
                        Decimal(input_tokens) * role.input_per_million
                        + Decimal(output_tokens) * role.output_per_million
                    )
                    / 1_000_000
                )
                with self.store.write() as db:
                    db.execute(
                        "UPDATE model_calls SET charged_tokens=?,charged_micros=?,"
                        "usage_json=? WHERE id=?",
                        (
                            input_tokens + output_tokens,
                            cost,
                            json.dumps(
                                {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}
                            ),
                            call_id,
                        ),
                    )
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete output")
            answer = json.loads(choice["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            self._fail(
                call_id,
                request_key,
                "INVALID_OUTPUT",
                "INVALID_MODEL_OUTPUT",
                request_status="FAILED",
            )
            raise ModelError(
                "INVALID_MODEL_OUTPUT", "模型返回截断或无效 JSON；调用已计入预算"
            ) from None
        with self.store.write() as db:
            db.execute("UPDATE model_calls SET status='SUCCEEDED' WHERE id=?", (call_id,))
            db.execute(
                "UPDATE model_requests SET status='SUCCEEDED',result_json=?,"
                "error_code=NULL,updated_at=? WHERE request_key=?",
                (json.dumps(answer, ensure_ascii=False), _stamp(), request_key),
            )
        return answer
