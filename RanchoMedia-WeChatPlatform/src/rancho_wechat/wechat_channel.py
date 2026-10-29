"""Draft-only wrapper around pinned wechatpy; no token acquisition or publication API."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import sys
from typing import Any

from rancho_wechat.wechat_diagnostics import configure_http, error_detail

SDK_COMMIT = "a695b89891e0473113c946a3a44d948fcf217404"
SDK_REQUIREMENT = "wechatpy @ git+https://github.com/wechatpy/wechatpy.git@" + SDK_COMMIT


class _RefreshForbidden(Exception):
    pass


def _no_refresh(*args: Any, **kwargs: Any) -> None:
    raise _RefreshForbidden("operator must renew credentials explicitly")


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class WechatDraftChannel:
    """Director persists the intent before invoking add_draft exactly once.

    Callers must never call add_draft again for an unknown intent. Reconciliation
    uses get/list only and must compare actual article contents, not title alone.
    """

    def __init__(
        self,
        account_id: str,
        *,
        enabled: bool = False,
        authorized_by: str = "",
        access_token_env: str = "",
        app_id_env: str = "",
        timeout_seconds: int = 20,
        use_env_proxy: bool = False,
        client: Any = None,
    ) -> None:
        self.account_id = account_id
        self.enabled = enabled
        self.authorized_by = authorized_by
        self.access_token_env, self.app_id_env = access_token_env, app_id_env
        if not 5 <= timeout_seconds <= 60:
            raise ValueError("WeChat timeout must be 5–60 seconds")
        self.timeout_seconds = timeout_seconds
        self.use_env_proxy = use_env_proxy
        self._provided_client = client
        self._sdk: Any = None

    def _result(
        self,
        operation: str,
        status: str,
        *,
        remote_id: str | None = None,
        blocked_reason: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "remote_id": remote_id,
            "remote_url": None,
            "operation": operation,
            "account_id": self.account_id,
            "blocked_reason": blocked_reason,
            "evidence": {
                "sdk": "wechatpy",
                "sdk_commit": SDK_COMMIT,
                "timeout_seconds": self.timeout_seconds,
                "automatic_retry": False,
                "network_mode": "ENVIRONMENT_PROXY" if self.use_env_proxy else "DIRECT",
                "http_responses": getattr(
                    getattr(self._sdk, "_http", None), "_rancho_http_responses", []
                ),
                "public_publish_enabled": False,
                **(evidence or {}),
            },
        }

    def _gate(self, operation: str) -> dict[str, Any] | None:
        if not self.enabled or not self.authorized_by or not self.account_id:
            return self._result(
                operation,
                "WAITING_AUTHORIZATION",
                blocked_reason="ACCOUNT_DRAFT_AUTHORIZATION_REQUIRED",
            )
        names = (self.access_token_env, self.app_id_env)
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", name) for name in names):
            return self._result(
                operation, "WAITING_CREDENTIALS", blocked_reason="CREDENTIAL_ENV_NAMES_REQUIRED"
            )
        if not all(os.environ.get(name, "").strip() for name in names):
            return self._result(
                operation, "WAITING_CREDENTIALS", blocked_reason="CREDENTIAL_ENV_MISSING"
            )
        return None

    def capabilities(self) -> dict[str, Any]:
        gated = self._gate("capabilities")
        if gated:
            return gated
        return self._result(
            "capabilities",
            "CONFIGURED_UNVERIFIED",
            evidence={
                "draft_add": "NEEDS_PERMISSION",
                "draft_read": "NEEDS_PERMISSION",
                "freepublish_submit": "DISABLED",
                "mass_send": "DISABLED",
                "comment_read": "DISABLED",
                "comment_reply": "DISABLED",
                "note": "Configuration is not an account permission probe; no request was sent.",
            },
        )

    def _client(self) -> Any:
        if self._provided_client is not None:
            return self._provided_client
        if self._sdk is not None:
            return self._sdk
        distribution = importlib.metadata.distribution("wechatpy")
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        if direct.get("vcs_info", {}).get("commit_id") != SDK_COMMIT:
            raise ImportError("unsupported SDK revision")
        module = importlib.import_module("wechatpy")
        client = module.WeChatClient(
            os.environ[self.app_id_env],
            secret="",
            access_token=os.environ[self.access_token_env],
            timeout=self.timeout_seconds,
            auto_retry=False,
        )
        # No AppSecret is supplied. Even if SDK state expires, it may not fetch a token.
        client.fetch_access_token = _no_refresh
        configure_http(client._http, self.timeout_seconds, use_env_proxy=self.use_env_proxy)
        if not all(hasattr(client.draft, method) for method in ("add", "get", "batchget")):
            raise ImportError("draft methods absent")
        self._sdk = client
        return client

    @staticmethod
    def _articles(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("One canary article is required")
        article = items[0]
        if (
            not isinstance(article.get("title"), str)
            or not 1 <= len(article["title"]) <= 64
            or not isinstance(article.get("content"), str)
            or not article["content"].strip()
            or not isinstance(article.get("thumb_media_id"), str)
            or not article["thumb_media_id"].strip()
            or len(article["content"].encode()) > 500_000
            or article.get("author", "") not in {"", "RanchoMedia 编辑部"}
        ):
            raise ValueError("Draft needs title, HTML, valid cover ID and neutral author")
        return [
            {
                key: article.get(key, "")
                for key in (
                    "title",
                    "content",
                    "thumb_media_id",
                    "author",
                    "digest",
                    "content_source_url",
                )
            }
            | {"need_open_comment": False, "only_fans_can_comment": False}
        ]

    def _error(self, operation: str, error: Exception, mutation: bool) -> dict[str, Any]:
        if isinstance(error, _RefreshForbidden):
            return self._result(
                operation, "WAITING_CREDENTIALS", blocked_reason="EXPLICIT_TOKEN_RENEWAL_REQUIRED"
            )
        detail = error_detail(
            error,
            operation,
            mutation=mutation,
            sensitive=(os.environ.get(self.access_token_env, ""),),
        )
        code = detail["errcode"]
        if code is not None:
            status = "REMOTE_REJECTED"
            if code in {40001, 40013, 40014, 41001, 42001, 40164}:
                status = "WAITING_CREDENTIALS"
            elif code in {48001, 48004}:
                status = "CAPABILITY_BLOCKED"
            elif code in {45009, 45011}:
                status = "RATE_LIMITED"
            return self._result(
                operation, status, blocked_reason="WECHAT_ERROR_CODE", evidence=detail
            )
        return self._result(
            operation,
            "UNKNOWN_REMOTE_STATUS" if mutation else "REMOTE_READ_FAILED",
            blocked_reason="RECONCILE_WITHOUT_RESENDING" if mutation else "BOUNDED_READ_FAILED",
            evidence=detail,
        )

    def add_draft(self, request: dict[str, Any]) -> dict[str, Any]:
        gated = self._gate("draft_add")
        if gated:
            return gated
        try:
            articles = self._articles(request.get("articles"))
        except ValueError:
            return self._result(
                "draft_add", "VALIDATION_BLOCKED", blocked_reason="INVALID_CANARY_DRAFT_OR_COVER"
            )
        try:
            client = self._client()
        except Exception:
            return self._result(
                "draft_add", "WAITING_DEPENDENCY", blocked_reason="PINNED_WECHATPY_UNAVAILABLE"
            )
        try:
            result = client.draft.add(articles)
            identifier = result.get("media_id") if isinstance(result, dict) else None
            if not isinstance(identifier, str) or not identifier.strip():
                return self._result(
                    "draft_add",
                    "UNKNOWN_REMOTE_STATUS",
                    blocked_reason="SUCCESS_NOT_ESTABLISHED_NO_MEDIA_ID",
                    evidence={"request_sha256": _sha({"articles": articles})},
                )
            return self._result(
                "draft_add",
                "REMOTE_DRAFT_CREATED",
                remote_id=identifier,
                evidence={
                    "request_sha256": _sha({"articles": articles}),
                    "public_visibility": "NOT_PUBLISHED",
                },
            )
        except Exception as error:
            result = self._error("draft_add", error, True)
            result["evidence"]["request_sha256"] = _sha({"articles": articles})
            return result

    def get_draft(self, media_id: str) -> dict[str, Any]:
        gated = self._gate("draft_get")
        if gated:
            return gated
        if not isinstance(media_id, str) or not media_id.strip() or len(media_id) > 512:
            return self._result(
                "draft_get", "VALIDATION_BLOCKED", blocked_reason="MEDIA_ID_REQUIRED"
            )
        try:
            client = self._client()
        except Exception:
            return self._result(
                "draft_get", "WAITING_DEPENDENCY", blocked_reason="PINNED_WECHATPY_UNAVAILABLE"
            )
        try:
            result = client.draft.get(media_id)
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("news_item"), list)
                or not result["news_item"]
            ):
                return self._result(
                    "draft_get", "REMOTE_READ_FAILED", blocked_reason="INVALID_DRAFT_RESPONSE"
                )
            return self._result(
                "draft_get",
                "REMOTE_DRAFT_CONFIRMED",
                remote_id=media_id,
                evidence={"articles": result["news_item"], "public_visibility": "NOT_PUBLISHED"},
            )
        except Exception as error:
            return self._error("draft_get", error, False)

    def list_drafts(self, offset: int = 0, count: int = 20) -> dict[str, Any]:
        gated = self._gate("draft_list")
        if gated:
            return gated
        if (
            not isinstance(offset, int)
            or offset < 0
            or not isinstance(count, int)
            or not 1 <= count <= 20
        ):
            return self._result("draft_list", "VALIDATION_BLOCKED", blocked_reason="INVALID_PAGE")
        try:
            client = self._client()
        except Exception:
            return self._result(
                "draft_list", "WAITING_DEPENDENCY", blocked_reason="PINNED_WECHATPY_UNAVAILABLE"
            )
        try:
            result = client.draft.batchget(offset=offset, count=count, no_content=0)
            if not isinstance(result, dict) or not isinstance(result.get("item"), list):
                return self._result(
                    "draft_list", "REMOTE_READ_FAILED", blocked_reason="INVALID_DRAFT_PAGE"
                )
            return self._result(
                "draft_list",
                "REMOTE_DRAFT_LIST_READ",
                evidence={
                    "items": result["item"],
                    "total_count": result.get("total_count"),
                    "offset": offset,
                    "count": count,
                },
            )
        except Exception as error:
            return self._error("draft_list", error, False)


def main() -> int:
    """Isolated SDK worker; stdin contains env names, never credential values."""
    try:
        line = sys.stdin.read(1_000_001)
        if len(line.encode()) > 1_000_000:
            raise ValueError("request too large")
        request = json.loads(line)
        channel = WechatDraftChannel(**request["account"])
        action = request.get("action", "capabilities")
        if action == "capabilities":
            result = channel.capabilities()
        elif action == "add_draft":
            result = channel.add_draft(request["request"])
        elif action == "get_draft":
            result = channel.get_draft(request["media_id"])
        elif action == "list_drafts":
            result = channel.list_drafts(request.get("offset", 0), request.get("count", 20))
        else:
            result = channel._result(
                str(action), "CAPABILITY_BLOCKED", blocked_reason="ONLY_DRAFT_OPERATIONS_ALLOWED"
            )
    except (ValueError, TypeError, KeyError):
        result = {
            "status": "VALIDATION_BLOCKED",
            "remote_id": None,
            "remote_url": None,
            "operation": "invalid_request",
            "blocked_reason": "INVALID_WORKER_REQUEST",
            "evidence": {},
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
