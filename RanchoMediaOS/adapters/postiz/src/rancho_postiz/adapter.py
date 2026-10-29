"""Isolated HTTP contract wrapper for a pinned Postiz public API.

Creating a Postiz draft is not proof of publication on any social platform.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from rancho_core.domain import payload_hash
from rancho_platform_sdk.contracts import (
    AuthenticationRequiredError,
    BeforeSendError,
    ExternalOutcomeUnknownError,
    HumanInterventionRequiredError,
    PublishRequest,
    RateLimitError,
    TerminalPlatformError,
)


@dataclass(frozen=True)
class PostizSubmission:
    post_ids: tuple[str, ...]
    state: str
    # This is an internal Postiz receipt. No public platform publication is confirmed.


class PostizAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        integration_id: str,
        provider: str,
        transport: httpx.AsyncBaseTransport | None = None,
        journal_path: Path | None = None,
        enabled: bool = False,
        external_side_effects_enabled: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Postiz qualification is restricted to a local HTTP service")
        if not api_key or not integration_id or not provider:
            raise ValueError("Postiz API key, integration ID and provider are required")
        self.client = httpx.AsyncClient(transport=transport, trust_env=False)
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.integration_id, self.provider = integration_id, provider
        self.enabled = enabled and external_side_effects_enabled
        if self.enabled and journal_path is None:
            raise ValueError("Enabled Postiz wrapper requires a durable intent journal")
        self.journal_path = journal_path
        if journal_path is not None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(journal_path) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS postiz_intents ("
                    "account TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                    "status TEXT NOT NULL, PRIMARY KEY (account, key))"
                )
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> PostizAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    def _reserve(self, request: PublishRequest) -> None:
        assert self.journal_path is not None
        account = str(request.account_id)
        fingerprint = payload_hash(request.approved_payload())
        with sqlite3.connect(self.journal_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT fingerprint FROM postiz_intents WHERE account=? AND key=?",
                (account, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise TerminalPlatformError("Postiz idempotency key has a different payload")
                raise HumanInterventionRequiredError("Postiz request needs reconciliation")
            connection.execute(
                "INSERT INTO postiz_intents VALUES (?, ?, ?, 'INTENT')",
                (account, request.idempotency_key, fingerprint),
            )

    def _journal_state(self, request: PublishRequest, state: str) -> None:
        assert self.journal_path is not None
        with sqlite3.connect(self.journal_path, timeout=10) as connection:
            if state == "RELEASE":
                connection.execute(
                    "DELETE FROM postiz_intents WHERE account=? AND key=?",
                    (str(request.account_id), request.idempotency_key),
                )
            else:
                connection.execute(
                    "UPDATE postiz_intents SET status=? WHERE account=? AND key=?",
                    (state, str(request.account_id), request.idempotency_key),
                )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._api_key}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = await self.client.request(
                method,
                self.base_url + path,
                headers=self._headers(),
                follow_redirects=False,
                timeout=5,
                json=json_body,
                params=params,
            )
        except httpx.ConnectError as error:
            raise BeforeSendError("Postiz connection failed before a response") from error
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise ExternalOutcomeUnknownError("Postiz request outcome unknown") from error
        if response.status_code in {401, 403}:
            raise AuthenticationRequiredError("Postiz authentication or authorization failed")
        if response.status_code == 429:
            raw = response.headers.get("retry-after", "60")
            try:
                seconds = max(1, min(int(raw), 3600))
            except ValueError:
                seconds = 60
            raise RateLimitError(seconds)
        if response.status_code >= 500:
            raise ExternalOutcomeUnknownError("Postiz server error after request")
        if response.status_code >= 400 or response.is_redirect:
            raise TerminalPlatformError(f"Postiz rejected request with HTTP {response.status_code}")
        return response

    async def create_draft(self, request: PublishRequest) -> PostizSubmission:
        if not self.enabled:
            raise BeforeSendError("Postiz HTTP effects are disabled")
        if request.platform != "postiz" or request.media_assets:
            raise TerminalPlatformError("Unqualified Postiz payload")
        body = {
            "type": "draft",
            "shortLink": False,
            "date": request.scheduled_at.isoformat(),
            "tags": [],
            "posts": [
                {
                    "integration": {"id": self.integration_id},
                    "value": [{"content": request.text, "image": []}],
                    "settings": {"__type": self.provider},
                }
            ],
            "creationMethod": "API",
        }
        async with self._lock:
            self._reserve(request)
            try:
                response = await self._request("POST", "/public/v1/posts", json_body=body)
            except BeforeSendError:
                self._journal_state(request, "RELEASE")
                raise
            except BaseException:
                self._journal_state(request, "UNKNOWN")
                raise
            try:
                items = response.json()
                if not isinstance(items, list) or not items:
                    raise ValueError("No posts in response")
                ids = tuple(item["id"] for item in items if isinstance(item.get("id"), str))
                if len(ids) != len(items):
                    raise ValueError("Missing post ID")
            except (ValueError, KeyError, TypeError) as error:
                self._journal_state(request, "UNKNOWN")
                raise ExternalOutcomeUnknownError(
                    "Postiz accepted request with unreadable receipt"
                ) from error
            self._journal_state(request, "DRAFT_IN_POSTIZ")
            return PostizSubmission(post_ids=ids, state="DRAFT_IN_POSTIZ")

    async def reconcile_candidates(self, request: PublishRequest) -> list[dict[str, str]]:
        # GET /posts has no idempotency-key filter. A match is only a lead for human review.
        start = (request.scheduled_at - timedelta(days=1)).isoformat()
        end = (request.scheduled_at + timedelta(days=1)).isoformat()
        response = await self._request(
            "GET", "/public/v1/posts", params={"startDate": start, "endDate": end}
        )
        try:
            posts = response.json()["posts"]
            if not isinstance(posts, list):
                raise ValueError("Invalid posts list")
            return [
                {"id": str(post["id"]), "state": str(post.get("state", "UNKNOWN"))}
                for post in posts
                if post.get("content") == request.text and post.get("id")
            ]
        except (ValueError, KeyError, TypeError) as error:
            raise HumanInterventionRequiredError(
                "Postiz reconciliation response is invalid"
            ) from error
