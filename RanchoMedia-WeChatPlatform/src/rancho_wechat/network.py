"""Director edge reusing the editor task ledger, reviewed content and installed channel tools."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from rancho_wechat.content import ContentPackage, StrictModel, import_package
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.production import WritingBrief, WritingResult, save_json

CHANNELS = ("website", "douyin", "xiaohongshu", "x", "zhihu", "wechat", "bilibili")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ChannelPolicy(StrictModel):
    account_id: str = ""
    enabled: bool = False
    authorized_by: str = ""
    actions: list[Literal["draft", "publish", "read_comments", "reply"]] = Field(
        default_factory=list
    )
    content_scope: list[str] = Field(default_factory=list)
    auto_approve_low_risk: bool = False
    paused: bool = False
    min_interval_seconds: int = Field(default=60, ge=30, le=86400)
    access_token_env: str = ""
    app_id_env: str = ""
    thumb_media_id: str = ""
    public_base_url: str = ""
    approved_variant_hashes: list[str] = Field(default_factory=list)


class OperatorPolicy(StrictModel):
    mode: Literal["DRY_RUN", "SHADOW", "CANARY", "PRODUCTION"] = "SHADOW"
    brand: str = "RanchoMedia"
    paused: bool = False
    channels: dict[str, ChannelPolicy] = Field(default_factory=dict)


def network_root(desk: EditorialDesk) -> Path:
    return Path(os.environ.get("RANCHOMEDIA_NETWORK_ROOT", str(desk.store.path.parent / "network")))


class Director:
    def __init__(self, desk: EditorialDesk, root: Path) -> None:
        self.desk, self.store, self.root = desk, desk.store, root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def policy(self) -> OperatorPolicy:
        path = self.root / "operator-policy.json"
        if not path.exists():
            return OperatorPolicy(channels={channel: ChannelPolicy() for channel in CHANNELS})
        return OperatorPolicy.model_validate_json(path.read_text(encoding="utf-8-sig"))

    def set_pause(self, paused: bool, channel: str | None = None) -> None:
        policy = self.policy()
        if channel is None:
            policy.paused = paused
        elif channel in CHANNELS:
            policy.channels.setdefault(channel, ChannelPolicy()).paused = paused
        else:
            raise ValueError("Unknown channel")
        save_json(self.root / "operator-policy.json", policy.model_dump(mode="json"))
        with self.store.write() as db:
            self.desk._audit(
                db,
                "network",
                channel or "all",
                "PAUSE" if paused else "RESUME",
                {"channel": channel, "mode": policy.mode},
            )

    def tasks(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tasks WHERE kind IN ('DIRECTOR_EDITORIAL','CHANNEL_INTENT') "
                    "ORDER BY created_at DESC LIMIT 200"
                )
            ]
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
            row["result"] = json.loads(row.pop("result_json")) if row["result_json"] else None
        return rows

    def status(self) -> dict[str, Any]:
        policy = self.policy()
        channels: dict[str, dict[str, Any]] = {}
        for name in CHANNELS:
            account = policy.channels.get(name, ChannelPolicy())
            configured = bool(account.account_id and account.authorized_by and account.enabled)
            channels[name] = {
                "account_id": account.account_id or None,
                "configured": configured,
                "authenticated": False,
                "web_ui_authenticated": False,
                "api_authenticated": False,
                "authentication_method": None,
                "draft_created": False,
                "published": False,
                "comments_read": False,
                "comment_count": None,
                "reply_succeeded": False,
                "state": "WAITING_AUTHORIZATION" if configured else "WAITING_CREDENTIALS",
                "paused": policy.paused or account.paused,
                "note": "Configuration and local tool success are not remote account receipts",
            }
        website = channels["website"]
        website["state"] = (
            "TARGET_IDENTIFIED_SCOPE_PENDING"
            if policy.channels.get("website", ChannelPolicy()).public_base_url
            else "WAITING_TARGET"
        )
        tasks = self.tasks()
        # Only durable remote receipts can elevate remote operation status.
        for task in reversed(tasks):
            if task["kind"] != "CHANNEL_INTENT" or not task["result"]:
                continue
            result, channel = task["result"], task["payload"]["channel"]
            if channel not in channels:
                continue
            if result.get("remote_id") and result.get("status") == "REMOTE_DRAFT_CREATED":
                channels[channel]["draft_created"] = True
                channels[channel]["authenticated"] = True
                channels[channel]["state"] = "REMOTE_DRAFT_CREATED"
                if channel == "wechat" and result.get("evidence", {}).get("sdk") == "wechatpy":
                    channels[channel]["api_authenticated"] = True
                    channels[channel]["authentication_method"] = "OFFICIAL_API"
            if result.get("remote_id") and result.get("status") == "PUBLICLY_VERIFIED":
                channels[channel]["published"] = True
                channels[channel]["authenticated"] = True
                channels[channel]["state"] = "PUBLICLY_VERIFIED"
                channels[channel]["remote_url"] = result.get("public_url")
                if channel == "website":
                    repository = result.get("repository", "")
                    if "/" in repository:
                        channels[channel]["account_id"] = repository.split("/", 1)[0]
                else:
                    channels[channel]["account_id"] = (
                        result.get("account_id")
                        or channels[channel]["account_id"]
                        or task["payload"].get("account_id")
                    )
                channels[channel]["note"] = (
                    "Historical single-article authorization; future publication remains disabled"
                )
            if result.get("remote_id") and result.get("status") in {
                "REMOTE_DRAFT_CREATED",
                "PUBLICLY_VERIFIED",
            }:
                executor = result.get("executor")
                if executor == "OFFICIAL_CREATOR_WEB_UI":
                    channels[channel]["web_ui_authenticated"] = True
                    channels[channel]["authentication_method"] = "OFFICIAL_CREATOR_WEB_UI"
                elif executor == "OFFICIAL_API":
                    channels[channel]["api_authenticated"] = True
                    channels[channel]["authentication_method"] = "OFFICIAL_API"
                if result.get("comments_read") is True:
                    channels[channel]["comments_read"] = True
                    count = result.get("comment_count")
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        channels[channel]["comment_count"] = count
                    comments = result.get("comments")
                    if isinstance(comments, list):
                        channels[channel]["comments"] = comments
                        if channels[channel]["comment_count"] is None:
                            channels[channel]["comment_count"] = len(comments)
        return {
            "mode": policy.mode,
            "paused": policy.paused,
            "channels": channels,
            "tasks": tasks,
            "billing_amount": "UNKNOWN",
            "legacy_approvals_changed": False,
        }

    @staticmethod
    def _call_output(call_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        call = json.loads(call_path.read_text(encoding="utf-8"))
        if call.get("status") != "SUCCEEDED" or call.get("tools_observed"):
            raise EditorialError("MODEL_CALL_NOT_QUALIFIED", "模型调用未完成或观察到工具副作用")
        output = Path(call["output"])
        if hashlib.sha256(output.read_bytes()).hexdigest() != call.get("output_sha256"):
            raise EditorialError("MODEL_OUTPUT_CHANGED", "模型原始结果已变化，不能沿用审查")
        return json.loads(output.read_text(encoding="utf-8")), call

    def ingest(self, brief_path: Path) -> dict[str, Any]:
        """Import actual separately reviewed output, without author approval."""
        brief = WritingBrief.model_validate_json(brief_path.read_text(encoding="utf-8-sig"))
        raw, writer_call = self._call_output(self.root / "writer-call.json")
        review_path = self.root / "review-recovery-call.json"
        if not review_path.exists():
            review_path = self.root / "review-call.json"
        review, review_call = self._call_output(review_path)
        draft = WritingResult.model_validate(raw)
        checks = review.get("claim_checks", [])
        supported = {item["claim_id"] for item in checks if item["status"] == "SOURCE_SUPPORTED"}
        required = {claim.ref for claim in brief.claims}
        findings = review.get("findings", [])
        cleared = (
            review.get("verdict") == "PASS"
            and review.get("risk") == "LOW"
            and required == supported
            and not any(item["severity"] == "BLOCKER" for item in findings)
        )
        identity = fingerprint(
            {
                "brief": brief.model_dump(mode="json"),
                "writer": writer_call["output_sha256"],
                "reviewer": review_call["output_sha256"],
            }
        )
        key = "director-editorial:" + identity
        with self.store.connection() as db:
            existing = db.execute(
                "SELECT result_json FROM tasks WHERE task_key=?", (key,)
            ).fetchone()
        if existing and existing[0]:
            return json.loads(existing[0])  # type: ignore[no-any-return]
        package = ContentPackage.model_validate(
            {
                "schema_version": "rancho-wechat.content/1.1",
                "package_id": str(uuid5(NAMESPACE_URL, key)),
                "execution": {
                    "executor": "codex-exec",
                    "generated_at": writer_call["finished_at"],
                    "model": "CODEX_CLI_DEFAULT_MODEL_NOT_OBSERVED",
                    "note": "New neutral canary; separate writer/reviewer processes. "
                    "No personal signature.",
                    "style_sample_status": "UNAVAILABLE_USER_CONSTRAINTS_ONLY",
                },
                "topic_title": brief.topic_title,
                "topic_idea": brief.angle,
                "origin": "EVERGREEN",
                "sources": [source.model_dump(mode="json") for source in brief.sources],
                "claims": [claim.model_dump() for claim in brief.claims],
                "research_note": draft.research_note,
                "article": draft.article.model_dump(),
                "fact_check": {
                    "status": "COMPLETE" if cleared else "NEEDS_CONFIRMATION",
                    "findings": findings,
                    "note": review["review_note"],
                    "independence": "SEPARATE_REVIEW",
                },
            }
        )
        variants = review.get("variants") or json.loads(
            (self.root / "variants.json").read_text(encoding="utf-8")
        )
        validate_variants(variants)
        article = import_package(self.desk, package)
        stamp = utc_now()
        result = {
            "article_id": article["id"],
            "version_id": article["current_version_id"],
            "canonical_content_id": str(package.package_id),
            "generation_job_key": key,
            "editorial_decision": "LOW_RISK_REVIEW_CLEARED" if cleared else "REVIEW_REQUIRED",
            "risk": review.get("risk", "UNKNOWN"),
            "article_status": "IN_REVIEW",
            "author_approved": False,
            "writer_run": writer_call["run_key"],
            "reviewer_run": review_call["run_key"],
            "variants": variants,
            "variant_review_status": "REVIEW_REQUIRED",
            "billing_amount": "UNKNOWN",
            "scope": "software_explainer",
        }
        payload = {
            "request_sha256": identity,
            "brief": brief.model_dump(mode="json"),
            "research_package": package.research_note,
            "created_by": "Director",
            "scheduled_at": stamp,
        }
        with self.store.write() as db:
            db.execute(
                "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    "DIRECTOR_EDITORIAL",
                    "REVIEWED",
                    json.dumps(payload, ensure_ascii=False),
                    1,
                    None,
                    json.dumps(result, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            self.desk._audit(
                db,
                "network",
                key,
                "NEW_CONTENT_REVIEWED",
                {"article_id": article["id"], "cleared": cleared},
            )
        save_json(self.root / "reviewed-production.json", result)
        save_json(self.root / "new-content.json", package.model_dump(mode="json"))
        return result

    def enqueue(self, production: dict[str, Any], scheduled_at: str) -> list[str]:
        scheduled = datetime.fromisoformat(scheduled_at)
        if scheduled.tzinfo is None:
            raise ValueError("Schedule must include timezone")
        slot = scheduled.astimezone(UTC).isoformat()
        with self.store.connection() as db:
            owner = db.execute(
                "SELECT result_json FROM tasks WHERE task_key=? AND kind='DIRECTOR_EDITORIAL'",
                (production["generation_job_key"],),
            ).fetchone()
        if not owner or fingerprint(json.loads(owner[0])) != fingerprint(production):
            raise EditorialError("LEGACY_CONTENT_FORBIDDEN", "只能排期本Director新建并审查的稿件")
        keys = []
        policy = self.policy()
        for channel in CHANNELS:
            account = policy.channels.get(channel, ChannelPolicy())
            payload = {
                "channel": channel,
                "account_id": account.account_id,
                "canonical_content_id": production["canonical_content_id"],
                "version_id": production["version_id"],
                "article_id": production["article_id"],
                "generation_job_key": production["generation_job_key"],
                "variant": channel_variant(channel, production),
                "scheduled_at": slot,
                "scope": production["scope"],
                "editorial_decision": production["editorial_decision"],
            }
            key = "channel-intent:" + fingerprint(
                {
                    name: payload[name]
                    for name in (
                        "channel",
                        "account_id",
                        "canonical_content_id",
                        "version_id",
                        "scheduled_at",
                    )
                }
            )
            stamp = utc_now()
            with self.store.write() as db:
                previous = db.execute(
                    "SELECT payload_json FROM tasks WHERE task_key=?", (key,)
                ).fetchone()
                if previous and fingerprint(json.loads(previous[0])) != fingerprint(payload):
                    raise EditorialError("INTENT_CONFLICT", "同一发布槽的载荷变化，未覆盖")
                db.execute(
                    "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        "CHANNEL_INTENT",
                        "PENDING",
                        json.dumps(payload, ensure_ascii=False),
                        0,
                        None,
                        None,
                        stamp,
                        stamp,
                    ),
                )
            keys.append(key)
        return keys

    def tick(self) -> dict[str, Any]:
        policy = self.policy()
        if policy.paused:
            return {"status": "PAUSED", "processed": 0}
        processed = []
        for task in self.tasks():
            policy = self.policy()
            if policy.paused:
                break
            if task["kind"] != "CHANNEL_INTENT":
                continue
            payload, key = task["payload"], task["task_key"]
            if task["status"] in {"SENDING", "LEASED"}:
                if task["lease_until"] and task["lease_until"] <= utc_now():
                    self._finish(
                        key,
                        "UNKNOWN_REMOTE_STATUS",
                        {
                            "status": "UNKNOWN_REMOTE_STATUS",
                            "blocked_reason": "EXPIRED_MUTATION_LEASE_RECONCILE_ONLY",
                        },
                    )
                continue
            if task["status"] not in {
                "PENDING",
                "WAITING_CREDENTIALS",
                "WAITING_AUTHORIZATION",
                "SHADOW_READY",
                "WAITING_TARGET",
                "WAITING_MEDIA",
            }:
                continue
            if payload["scheduled_at"] > utc_now():
                continue
            channel = payload["channel"]
            account = policy.channels.get(channel, ChannelPolicy())
            if account.paused:
                continue
            if channel == "website":
                result = {
                    "status": "WAITING_TARGET"
                    if not account.public_base_url
                    else "WAITING_AUTHORIZATION",
                    "blocked_reason": "WEBSITE_CANARY_SCOPE_REQUIRED",
                }
            elif not account.account_id or not account.enabled or not account.authorized_by:
                result = {"status": "WAITING_CREDENTIALS", "blocked_reason": "ACCOUNT_NOT_ENABLED"}
            elif policy.mode in {"DRY_RUN", "SHADOW"}:
                result = {"status": "SHADOW_READY", "blocked_reason": "NO_REMOTE_WRITE_IN_SHADOW"}
            elif account.account_id != payload["account_id"]:
                result = {
                    "status": "WAITING_AUTHORIZATION",
                    "blocked_reason": "ACCOUNT_CHANGED_REQUEUE_REQUIRED",
                }
            elif (
                payload["scope"] not in account.content_scope
                or not account.auto_approve_low_risk
                or payload["editorial_decision"] != "LOW_RISK_REVIEW_CLEARED"
            ):
                result = {
                    "status": "WAITING_AUTHORIZATION",
                    "blocked_reason": "CONTENT_SCOPE_OR_REVIEW_REQUIRED",
                }
            elif channel != "wechat" or "draft" not in account.actions:
                result = {
                    "status": "WAITING_AUTHORIZATION",
                    "blocked_reason": "TOOL_ADAPTER_WRITE_NOT_QUALIFIED",
                }
            elif fingerprint(payload["variant"]) not in account.approved_variant_hashes:
                result = {
                    "status": "WAITING_AUTHORIZATION",
                    "blocked_reason": "CHANNEL_VARIANT_REVIEW_REQUIRED",
                }
            elif not account.thumb_media_id:
                result = {
                    "status": "WAITING_MEDIA",
                    "blocked_reason": "WECHAT_THUMB_MEDIA_ID_REQUIRED",
                }
            else:
                if task["attempts"] >= 3:
                    self._finish(
                        key,
                        "DEAD_LETTER",
                        {
                            "status": "DEAD_LETTER",
                            "blocked_reason": "ATTEMPT_LIMIT_MANUAL_RECOVERY",
                        },
                    )
                    continue
                if task["attempts"]:
                    retry_after = datetime.fromisoformat(task["updated_at"]) + timedelta(
                        seconds=60 * 2 ** (task["attempts"] - 1)
                    )
                    if datetime.now(UTC) < retry_after:
                        continue
                article = self.desk.article(payload["article_id"])
                if article["current_version_id"] != payload["version_id"] or article[
                    "status"
                ] not in {"IN_REVIEW", "APPROVED"}:
                    self._finish(
                        key,
                        "REVIEW_REQUIRED",
                        {
                            "status": "REVIEW_REQUIRED",
                            "blocked_reason": "ARTICLE_CHANGED_REVIEW_AGAIN",
                        },
                    )
                    continue
                if (
                    not account.access_token_env
                    or not account.app_id_env
                    or not all(
                        os.environ.get(name)
                        for name in (account.access_token_env, account.app_id_env)
                    )
                ):
                    self._finish(
                        key,
                        "WAITING_CREDENTIALS",
                        {
                            "status": "WAITING_CREDENTIALS",
                            "blocked_reason": "CREDENTIAL_ENV_MISSING",
                        },
                    )
                    continue
                latest = self.policy()
                if latest.paused or latest.channels.get(channel, ChannelPolicy()).paused:
                    continue
                # Reserve durable mutation intent before any subprocess or network send.
                with self.store.write() as db:
                    current = db.execute(
                        "SELECT status FROM tasks WHERE task_key=?", (key,)
                    ).fetchone()
                    if current[0] in {"SENDING", "UNKNOWN_REMOTE_STATUS", "REMOTE_DRAFT_CREATED"}:
                        continue
                    recent = db.execute(
                        "SELECT task_key,payload_json,updated_at FROM tasks "
                        "WHERE kind='CHANNEL_INTENT' AND attempts>0 "
                        "ORDER BY updated_at DESC LIMIT 200"
                    ).fetchall()
                    cutoff = (
                        datetime.now(UTC) - timedelta(seconds=account.min_interval_seconds)
                    ).isoformat()
                    if any(
                        row["task_key"] != key
                        and row["updated_at"] > cutoff
                        and json.loads(row["payload_json"])["account_id"] == account.account_id
                        for row in recent
                    ):
                        continue
                    db.execute(
                        "UPDATE tasks SET status='SENDING',attempts=attempts+1,"
                        "lease_until=?,updated_at=? WHERE task_key=?",
                        ((datetime.now(UTC) + timedelta(seconds=90)).isoformat(), utc_now(), key),
                    )
                result = self._wechat_draft(account, payload)
            self._finish(key, result["status"], result)
            processed.append({"channel": channel, **result})
        return {"status": "TICK_COMPLETE", "processed": len(processed), "results": processed}

    def _finish(self, key: str, status: str, result: dict[str, Any]) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE tasks SET status=?,result_json=?,lease_until=NULL,"
                "updated_at=? WHERE task_key=?",
                (status, json.dumps(result, ensure_ascii=False), utc_now(), key),
            )

    def record_website_receipt(self, receipt_path: Path) -> None:
        """Import an operator's one-off publisher receipt into the existing intent ledger.

        This local administrative operation never grants future publishing permission.
        The publisher performs the actual remote checks; keep its authorization and HTML.
        """
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        auth = json.loads(
            (self.root / "website-canary-authorization.json").read_text(encoding="utf-8")
        )
        production = json.loads(
            (self.root / "reviewed-production.json").read_text(encoding="utf-8")
        )
        if not (
            receipt.get("status") == "PUBLICLY_VERIFIED"
            and receipt.get("remote_id") == auth.get("head")
            and receipt.get("path") == auth.get("path")
            and receipt.get("public_url") == auth.get("public_url")
            and receipt.get("repository") == "RanchoTao/RanchoTao.github.io"
            and receipt.get("file_sha256") == auth.get("file_sha256")
            and auth.get("permission") == "SINGLE_ARTICLE_PUBLIC_CANARY"
            and receipt.get("pushed")
            and receipt.get("deployed")
            and receipt.get("http_status") == 200
            and receipt.get("public_url_verified")
            and receipt.get("content_checks")
            and all(receipt["content_checks"].values())
        ):
            raise ValueError("Public website receipt and authorization do not match")
        snapshot = receipt_path.with_suffix(".public.html")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != receipt.get("public_html_sha256"):
            raise ValueError("Public verification snapshot changed")
        matches = [
            task
            for task in self.tasks()
            if task["kind"] == "CHANNEL_INTENT"
            and task["payload"]["channel"] == "website"
            and task["payload"]["canonical_content_id"] == production["canonical_content_id"]
            and task["payload"]["version_id"] == production["version_id"]
        ]
        if len(matches) != 1:
            raise ValueError("Exactly one matching new-content website intent required")
        self._finish(matches[0]["task_key"], "PUBLICLY_VERIFIED", receipt)
        with self.store.write() as db:
            self.desk._audit(
                db,
                "network",
                matches[0]["task_key"],
                "SINGLE_WEBSITE_CANARY_VERIFIED",
                {
                    "remote_id": receipt["remote_id"],
                    "public_url": receipt["public_url"],
                    "authorization": auth["permission"],
                },
            )

    def _wechat_draft(self, account: ChannelPolicy, payload: dict[str, Any]) -> dict[str, Any]:
        python = self.root.parent / "ecosystem" / "wechat-bili" / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            return {
                "status": "WAITING_CREDENTIALS",
                "blocked_reason": "PINNED_WECHAT_SDK_NOT_INSTALLED",
            }
        variant = payload["variant"]
        request = {
            "action": "add_draft",
            "account": {
                "account_id": account.account_id,
                "enabled": account.enabled,
                "authorized_by": account.authorized_by,
                "access_token_env": account.access_token_env,
                "app_id_env": account.app_id_env,
                "timeout_seconds": 20,
            },
            "request": {
                "articles": [
                    {
                        "title": variant["title"],
                        "author": "RanchoMedia 编辑部",
                        "digest": variant["summary"],
                        "content": variant["html"],
                        "thumb_media_id": account.thumb_media_id,
                    }
                ]
            },
        }
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            call = subprocess.run(
                [str(python), "-m", "rancho_wechat.wechat_channel"],
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=environment,
                timeout=45,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if call.returncode != 0:
                return {
                    "status": "UNKNOWN_REMOTE_STATUS",
                    "blocked_reason": "SDK_WORKER_EXIT_RECONCILE_ONLY",
                }
            result: dict[str, Any] = json.loads(call.stdout)
            if result.get("status") == "REMOTE_DRAFT_CREATED" and not result.get("remote_id"):
                return {"status": "UNKNOWN_REMOTE_STATUS", "blocked_reason": "NO_REMOTE_ID"}
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {
                "status": "UNKNOWN_REMOTE_STATUS",
                "blocked_reason": "SDK_WORKER_UNKNOWN_RECONCILE_ONLY",
            }


def validate_variants(variants: dict[str, Any]) -> None:
    posts = variants.get("x_posts", [])
    if not 2 <= len(posts) <= 4 or any(not text or len(text) > 230 for text in posts):
        raise ValueError("X thread is not qualified")
    if (
        not 1 <= len(variants.get("xhs_title", "")) <= 20
        or len(variants.get("xhs_body", "")) > 1000
    ):
        raise ValueError("XHS variant is not qualified")
    for key in ("zhihu_body", "wechat_body"):
        if len(variants.get(key, "")) < 300:
            raise ValueError("Long-form variant is incomplete")
    if not 3 <= len(variants.get("video_scenes", [])) <= 4:
        raise ValueError("Playable video still needs scenes and rendering")


def channel_variant(channel: str, production: dict[str, Any]) -> dict[str, Any]:
    import html

    variants = production["variants"]
    if channel == "x":
        return {"format": "thread", "posts": variants["x_posts"]}
    if channel == "xiaohongshu":
        return {
            "format": "note",
            "title": variants["xhs_title"],
            "body": variants["xhs_body"],
            "media_status": "LOCAL_ASSETS_REQUIRED",
        }
    if channel == "wechat":
        body = variants["wechat_body"]
        return {
            "format": "official_draft",
            "title": variants["wechat_title"],
            "summary": "Git提交、推送与网站可见性之间的区别。",
            "html": "\n".join("<p>" + html.escape(part) + "</p>" for part in body.split("\n\n")),
        }
    if channel == "zhihu":
        return {
            "format": "article",
            "title": variants["zhihu_title"],
            "body": variants["zhihu_body"],
        }
    if channel in {"douyin", "bilibili"}:
        return {
            "format": "video",
            "scenes": variants["video_scenes"],
            "media_status": "RENDER_REQUIRED_NOT_UPLOADED",
        }
    return {
        "format": "website_article",
        "article_id": production["article_id"],
        "version_id": production["version_id"],
    }
