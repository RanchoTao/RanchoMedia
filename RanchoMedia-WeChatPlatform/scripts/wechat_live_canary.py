"""Single-account official SDK draft preparation with Windows-local encrypted input.

No freepublish, mass-send or comment operation. A material/draft mutation with an
uncertain response is never automatically repeated, including after process restart.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import ipaddress
import json
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime/social-live/wechat"
VARIANTS = ROOT / ".runtime/network/variants.json"
COVER = ROOT / ".runtime/network/assets/wechat-cover.png"
PIN = "a695b89891e0473113c946a3a44d948fcf217404"
sys.path.insert(0, str(ROOT / "src"))

from rancho_wechat.wechat_diagnostics import (  # noqa: E402
    configure_http,
    error_code,
    error_detail,
    request_route,
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def dump(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Preserve the exact old bytes before changing any JSON receipt or pointer.
        history = path.parent / "history"
        history.mkdir(exist_ok=True)
        with (history / f"{path.stem}-{uuid4().hex}.json").open("xb") as backup:
            backup.write(path.read_bytes())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("This local entry requires Windows DPAPI")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if encrypt:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)
        )
    else:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)
        )
    if not ok:
        raise OSError("Windows credential protection failed")
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        kernel.LocalFree(destination.data)


def store_credentials(credentials: dict[str, Any]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    encrypted = dpapi(json.dumps(credentials).encode("utf-8"), True)
    temporary = STATE / "credentials.dpapi.tmp"
    temporary.write_bytes(encrypted)
    temporary.replace(STATE / "credentials.dpapi")


def credentials() -> dict[str, Any]:
    return json.loads(dpapi((STATE / "credentials.dpapi").read_bytes(), False))


def variant() -> dict[str, Any]:
    import html

    data = load(VARIANTS)
    return {
        "title": data["wechat_title"],
        "author": "RanchoMedia 编辑部",
        "digest": "Git提交、推送与网站可见性之间的区别。",
        "content": "\n".join(
            "<p>" + html.escape(part) + "</p>" for part in data["wechat_body"].split("\n\n")
        ),
    }


def variant_sha() -> str:
    return digest(json.dumps(variant(), ensure_ascii=False, sort_keys=True).encode("utf-8"))


def error_receipt(
    error: Exception,
    mutation: bool = False,
    *,
    stage: str = "local",
    sensitive: tuple[str, ...] = (),
) -> dict[str, Any]:
    return error_detail(error, stage, mutation=mutation, sensitive=sensitive)


def token_state(receipt: dict[str, Any]) -> str | None:
    """Repair only a definite rejection; a timeout/crash stays uncertain."""
    detail = receipt.get("token_error") or {}
    if detail.get("status") == "REMOTE_REJECTED" and error_code(detail.get("errcode")):
        return "REJECTED"
    return receipt.get("token_request_state")


def repair_state() -> dict[str, Any]:
    """Offline recovery overlay; original failure receipt and DPAPI stay untouched."""
    path = STATE / "receipt.json"
    previous = load(path) if path.exists() else {}
    result = {
        "status": "OFFLINE_STATE_RECONCILED",
        "app_id_sha256": previous.get("app_id_sha256"),
        "source_receipt_sha256": digest(path.read_bytes()) if path.exists() else None,
        "original_token_request_state": previous.get("token_request_state"),
        "token_request_state": token_state(previous),
        "reason": "EXPLICIT_REMOTE_REJECTION"
        if token_state(previous) == "REJECTED"
        else ("NO_DEFINITE_REJECTION_PRESERVE_UNCERTAINTY"),
        "real_platform_requests": 0,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    dump(STATE / "recovery-state.json", result)
    return result


def pinned_client(secret: dict[str, Any], *, use_env_proxy: bool = False) -> Any:
    distribution = importlib.metadata.distribution("wechatpy")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct.get("vcs_info", {}).get("commit_id") != PIN:
        raise ImportError("SDK_PIN_MISMATCH")
    from wechatpy import WeChatClient

    client = WeChatClient(secret["app_id"], secret["app_secret"], timeout=20, auto_retry=False)
    configure_http(client._http, use_env_proxy=use_env_proxy)
    return client


def verified_account(secret: dict[str, Any]) -> bool:
    auth = load(STATE / "authorization.json")
    return bool(
        auth.get("account_self_confirmed")
        and auth.get("app_id_sha256") == digest(secret.get("app_id", "").encode())
        and auth.get("scope")
        in {"ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY", "DESKTOP_ACCOUNT_DIAGNOSTICS_ONLY"}
    )


def valid_token_response(fetched: Any) -> bool:
    return bool(
        isinstance(fetched, dict)
        and isinstance(fetched.get("access_token"), str)
        and fetched["access_token"]
        and isinstance(fetched.get("expires_in"), int)
        and not isinstance(fetched["expires_in"], bool)
        and fetched["expires_in"] > 60
    )


def new_attempt(mode: str, authorization_id: str) -> Path | None:
    # An operator-chosen ID is a one-use permit, not a credential or path.
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", authorization_id):
        return None
    path = STATE / "attempts" / f"{mode}-{digest(authorization_id.encode())}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        return None
    return path


def record_attempt(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    event = {**result, "updated_at": datetime.now(UTC).isoformat()}
    # Exclusive, append-only events; never replace a started or failed attempt.
    with (path / f"event-{time.time_ns()}-{uuid4().hex}.json").open("x", encoding="utf-8") as out:
        json.dump(event, out, ensure_ascii=False, indent=2)
    return event


def validate_token_once(
    authorization_id: str,
    whitelist_confirmed: bool,
    *,
    use_env_proxy: bool = False,
) -> dict[str, Any]:
    """Exactly one explicitly authorized token GET; no cover/draft calls reachable."""
    if not whitelist_confirmed or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", authorization_id):
        return {"status": "WAITING_AUTHORIZATION", "real_platform_requests": 0}
    secret = credentials()
    if not verified_account(secret) or not secret.get("app_secret"):
        return {
            "status": "WAITING_CREDENTIALS_OR_ACCOUNT_AUTHORIZATION",
            "real_platform_requests": 0,
        }
    previous = load(STATE / "receipt.json") if (STATE / "receipt.json").exists() else {}
    if token_state(previous) in {"SENDING", "UNKNOWN"}:
        return {
            "status": "TOKEN_OUTCOME_UNKNOWN_MANUAL_RECOVERY_REQUIRED",
            "real_platform_requests": 0,
        }
    latest = STATE / "token-validation.json"
    if latest.exists() and load(latest).get("token_request_state") in {"SENDING", "UNKNOWN"}:
        return {
            "status": "TOKEN_OUTCOME_UNKNOWN_MANUAL_RECOVERY_REQUIRED",
            "real_platform_requests": 0,
        }
    client = pinned_client(secret, use_env_proxy=True) if use_env_proxy else pinned_client(secret)
    attempt = new_attempt("token", authorization_id)
    if attempt is None:
        return {"status": "AUTHORIZATION_ALREADY_USED", "real_platform_requests": 0}
    result = {
        "status": "TOKEN_REQUESTING",
        "stage": "token",
        "token_request_state": "SENDING",
        "app_id_sha256": digest(secret["app_id"].encode()),
        "attempt": str(attempt.relative_to(STATE)),
        "automatic_retry": False,
        "network_mode": "ENVIRONMENT_PROXY" if use_env_proxy else "DIRECT",
        "request_route": request_route(
            getattr(client, "_http", None), "https://api.weixin.qq.com/cgi-bin/token"
        ),
        "public_publish_enabled": False,
        "cover_upload_enabled": False,
        "draft_add_enabled": False,
    }
    record_attempt(attempt, result)
    dump(latest, result)
    try:
        fetched = client.fetch_access_token()
    except Exception as error:
        detail = error_receipt(
            error,
            True,
            stage="token",
            sensitive=(secret["app_secret"], secret.get("access_token", "")),
        )
        result.update(
            status=detail["status"],
            token_error=detail,
            token_request_state="REJECTED" if detail["errcode"] is not None else "UNKNOWN",
        )
    else:
        # A malformed nominal success is uncertain, never a confirmed token.
        if not valid_token_response(fetched):
            result.update(status="UNKNOWN_REMOTE_STATUS", token_request_state="UNKNOWN")
        else:
            result.update(status="TOKEN_OBTAINED", token_request_state="CONFIRMED")
            record_attempt(attempt, {**result, "credentials_saved": False})
            secret.update(
                access_token=fetched["access_token"],
                token_expires_at=time.time() + fetched["expires_in"],
            )
            try:
                store_credentials(secret)
            except Exception:
                result.update(status="TOKEN_CONFIRMED_LOCAL_SAVE_FAILED", credentials_saved=False)
            else:
                result["credentials_saved"] = True
    result["real_platform_requests"] = 1
    result["http_responses"] = getattr(getattr(client, "_http", None), "_rancho_http_responses", [])
    result = record_attempt(attempt, result)
    dump(latest, result)
    return result


def probe_read_only_once(authorization_id: str) -> dict[str, Any]:
    """One batchget after a confirmed, persisted token; no implicit renewal."""
    latest = STATE / "token-validation.json"
    if not latest.exists():
        return {"status": "CONFIRMED_TOKEN_REQUIRED", "real_platform_requests": 0}
    verification = load(latest)
    secret = credentials()
    if not (
        verification.get("token_request_state") == "CONFIRMED"
        and verification.get("credentials_saved") is True
        and verification.get("app_id_sha256") == digest(secret["app_id"].encode())
        and secret.get("access_token")
        and secret.get("token_expires_at", 0) > time.time() + 60
        and verified_account(secret)
    ):
        return {"status": "CONFIRMED_TOKEN_REQUIRED", "real_platform_requests": 0}
    attempt = new_attempt("probe", authorization_id)
    if attempt is None:
        return {"status": "AUTHORIZATION_REQUIRED_OR_ALREADY_USED", "real_platform_requests": 0}
    from rancho_wechat.wechat_channel import WechatDraftChannel

    names = ("WECHAT_LOCAL_CANARY_APPID", "WECHAT_LOCAL_CANARY_TOKEN")
    old = {name: os.environ.get(name) for name in names}
    os.environ[names[0]], os.environ[names[1]] = secret["app_id"], secret["access_token"]
    try:
        channel = WechatDraftChannel(
            "LOCAL_CONFIRMED_ACCOUNT",
            enabled=True,
            authorized_by="EXPLICIT_READ_ONLY_PROBE",
            app_id_env=names[0],
            access_token_env=names[1],
            use_env_proxy=verification.get("network_mode") == "ENVIRONMENT_PROXY",
        )
        record_attempt(attempt, {"status": "PROBE_SENDING", "stage": "draft_list"})
        try:
            probe = channel.list_drafts(count=1)
            # Existing article bodies are unnecessary diagnostic evidence.
            probe.get("evidence", {}).pop("items", None)
        except Exception as error:
            probe = error_receipt(error, stage="draft_list", sensitive=(secret["access_token"],))
        result = {**probe, "stage": "draft_list", "draft_add_enabled": False}
        result = record_attempt(attempt, result)
        dump(STATE / "permission-probe.json", result)
        return result
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def network_check() -> dict[str, Any]:
    """Same requests/transport policy; an echo IP is not a WeChat observation."""
    import requests

    result: dict[str, Any] = {
        "stage": "network",
        "trust_env": False,
        "proxies": {},
        "redirects": False,
        "automatic_retry": False,
        "timeout_seconds": 20,
        "proxy_environment_present": {
            name: bool(os.environ.get(name)) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
        },
        "wechat_observed_ip": "UNKNOWN",
        "original_whitelist_match": "UNKNOWN",
        "wechat_api_requests": 0,
        "observations": [],
    }
    with requests.Session() as session:
        configure_http(session)
        for endpoint in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
            entry: dict[str, Any] = {"endpoint": endpoint, "same_http_policy": True}
            try:
                response = session.get(endpoint)
                response.raise_for_status()
                entry.update(status="OBSERVED", ip=str(ipaddress.ip_address(response.text.strip())))
            except Exception as error:
                entry.update(status="FAILED", error_type=type(error).__name__)
            result["observations"].append(entry)
    result["updated_at"] = datetime.now(UTC).isoformat()
    dump(STATE / "network-check.json", result)
    return result


def execute() -> dict[str, Any]:
    """Use only the exact locally confirmed account and existing WeChat variant."""
    STATE.mkdir(parents=True, exist_ok=True)
    if not (STATE / "credentials.dpapi").exists():
        return {"status": "WAITING_CREDENTIALS", "real_platform_requests": 0}
    auth = load(STATE / "authorization.json")
    secret = credentials()
    if not (
        auth.get("account_self_confirmed")
        and auth.get("app_id_sha256") == digest(secret["app_id"].encode())
        and auth.get("variant_sha256") == variant_sha()
        and auth.get("scope") == "ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY"
    ):
        return {"status": "WAITING_AUTHORIZATION", "blocked_reason": "ACCOUNT_OR_VARIANT_CHANGED"}
    distribution = importlib.metadata.distribution("wechatpy")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct.get("vcs_info", {}).get("commit_id") != PIN:
        return {"status": "WAITING_DEPENDENCY", "blocked_reason": "SDK_PIN_MISMATCH"}
    previous = load(STATE / "receipt.json") if (STATE / "receipt.json").exists() else {}
    result = {
        **previous,
        "account_id": auth["account_name"],
        "app_id_sha256": auth["app_id_sha256"],
        "variant_sha256": variant_sha(),
        "public_publish_enabled": False,
        "mass_send_enabled": False,
        "automatic_retry": False,
        "sdk_commit": PIN,
    }
    if previous and (
        previous.get("app_id_sha256") != auth["app_id_sha256"]
        or previous.get("variant_sha256") != variant_sha()
    ):
        return {"status": "WAITING_AUTHORIZATION", "blocked_reason": "EXISTING_INTENT_CONFLICT"}

    def save(status: str, **fields: Any) -> dict[str, Any]:
        result.update(status=status, updated_at=datetime.now(UTC).isoformat(), **fields)
        dump(STATE / "receipt.json", result)
        return result

    token = secret.get("access_token", "")
    if not token or secret.get("token_expires_at", float("inf")) <= time.time() + 60:
        state = token_state(result)
        if state == "REJECTED":
            return save("TOKEN_REJECTED_EXPLICIT_TOKEN_ONLY_REQUIRED", token_request_state=state)
        if state in {"SENDING", "UNKNOWN"}:
            return save("TOKEN_OUTCOME_UNKNOWN_MANUAL_RECOVERY_REQUIRED")
        validation = STATE / "token-validation.json"
        if validation.exists() and load(validation).get("token_request_state") in {
            "SENDING",
            "UNKNOWN",
            "REJECTED",
        }:
            return save("EXPLICIT_TOKEN_ONLY_RECOVERY_REQUIRED")
        if not secret.get("app_secret"):
            return save(
                "WAITING_CREDENTIALS", blocked_reason="APP_SECRET_OR_CURRENT_TOKEN_REQUIRED"
            )
        client = pinned_client(secret)
        save("TOKEN_REQUESTING", token_request_state="SENDING")
        try:
            fetched = client.fetch_access_token()
        except Exception as error:
            detail = error_receipt(
                error,
                True,
                stage="token",
                sensitive=(secret.get("app_secret", ""), secret.get("access_token", "")),
            )
            return save(
                detail["status"],
                token_request_state="REJECTED" if detail["errcode"] is not None else "UNKNOWN",
                token_error=detail,
                blocked_reason="CHECK_OFFICIAL_ACCOUNT_SCOPE_AND_IP_WHITELIST",
            )
        if not valid_token_response(fetched):
            return save("UNKNOWN_REMOTE_STATUS", token_request_state="UNKNOWN")
        token = fetched["access_token"]
        # Remote success and local persistence are distinct outcomes.
        save("TOKEN_OBTAINED", token_request_state="CONFIRMED", token_error=None)
        secret.update(access_token=token, token_expires_at=time.time() + fetched["expires_in"])
        try:
            store_credentials(secret)
        except Exception:
            return save("TOKEN_CONFIRMED_LOCAL_SAVE_FAILED", credentials_saved=False)
        save("TOKEN_OBTAINED", credentials_saved=True, token_printed=False)
    from rancho_wechat.wechat_channel import WechatDraftChannel

    os.environ["WECHAT_LOCAL_CANARY_APPID"] = secret["app_id"]
    os.environ["WECHAT_LOCAL_CANARY_TOKEN"] = token
    validation_path = STATE / "token-validation.json"
    route = load(validation_path) if validation_path.exists() else {}
    channel = WechatDraftChannel(
        auth["account_name"],
        enabled=True,
        authorized_by="LOCAL_OPERATOR_SELF_CONFIRMATION",
        app_id_env="WECHAT_LOCAL_CANARY_APPID",
        access_token_env="WECHAT_LOCAL_CANARY_TOKEN",
        use_env_proxy=route.get("app_id_sha256") == auth["app_id_sha256"]
        and route.get("network_mode") == "ENVIRONMENT_PROXY",
    )
    probe = channel.list_drafts(count=1)
    dump(STATE / "permission-probe.json", probe)
    if probe["status"] != "REMOTE_DRAFT_LIST_READ":
        return save("DRAFT_PERMISSION_PROBE_BLOCKED", probe=probe)
    if result.get("draft_media_id"):
        checked = channel.get_draft(result["draft_media_id"])
        dump(STATE / "draft-get.json", checked)
        return save(checked["status"], draft_get=checked, repeated_add=False)
    if result.get("draft_state") in {"SENDING", "UNKNOWN"}:
        return save("UNKNOWN_REMOTE_STATUS", blocked_reason="DRAFT_RECONCILE_ONLY_NO_RESEND")
    media_id = secret.get("thumb_media_id") or result.get("thumb_media_id")
    if not media_id:
        if result.get("cover_state") in {"SENDING", "UNKNOWN"}:
            return save("UNKNOWN_REMOTE_STATUS", blocked_reason="COVER_RECONCILE_ONLY_NO_REUPLOAD")
        cover_hash = digest(COVER.read_bytes())
        save("COVER_UPLOADING", cover_state="SENDING", cover_sha256=cover_hash)
        try:
            with COVER.open("rb") as picture:
                uploaded = channel._client().material.add("image", picture)
            media_id = uploaded.get("media_id")
            if not isinstance(media_id, str) or not media_id:
                return save(
                    "UNKNOWN_REMOTE_STATUS",
                    cover_state="UNKNOWN",
                    blocked_reason="COVER_ID_MISSING",
                )
            save("PERMANENT_COVER_CREATED", thumb_media_id=media_id, cover_state="CONFIRMED")
        except Exception as error:
            detail = error_receipt(
                error,
                True,
                stage="cover_upload",
                sensitive=(secret.get("app_secret", ""), token),
            )
            return save(
                detail["status"],
                cover_state="REJECTED" if detail.get("errcode") else "UNKNOWN",
                cover_error=detail,
            )
    request = {"articles": [{**variant(), "thumb_media_id": media_id}]}
    save("DRAFT_SENDING", draft_state="SENDING", thumb_media_id=media_id)
    added = channel.add_draft(request)
    dump(STATE / "draft-add.json", added)
    if added["status"] != "REMOTE_DRAFT_CREATED" or not added.get("remote_id"):
        return save(
            added["status"],
            draft_state="UNKNOWN" if added["status"] == "UNKNOWN_REMOTE_STATUS" else "REJECTED",
            draft_add=added,
        )
    save("REMOTE_DRAFT_CREATED", draft_state="CONFIRMED", draft_media_id=added["remote_id"])
    checked = channel.get_draft(added["remote_id"])
    dump(STATE / "draft-get.json", checked)
    return save(checked["status"], draft_get=checked, public_visibility="NOT_PUBLISHED")


def run_locked(
    mode: str = "execute",
    *,
    authorization_id: str = "",
    whitelist_confirmed: bool = False,
    use_env_proxy: bool = False,
) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    lock = STATE / "running.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"status": "HOLD_EXISTING_RUN_LOCK_MANUAL_CHECK_REQUIRED"}
    try:
        os.write(handle, str(os.getpid()).encode())
        if mode == "token-only":
            return validate_token_once(
                authorization_id,
                whitelist_confirmed,
                use_env_proxy=use_env_proxy,
            )
        if mode == "probe-read-only":
            return probe_read_only_once(authorization_id)
        if mode == "repair-state":
            return repair_state()
        if mode == "network-check":
            return network_check()
        if mode != "execute":
            return {"status": "INVALID_OPERATION", "real_platform_requests": 0}
        return execute()
    except Exception as error:
        detail = error_receipt(error, stage="local_" + mode)
        dump(STATE / "local-error.json", detail)
        return detail
    finally:
        os.close(handle)
        lock.unlink()


def ui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    window = tk.Tk()
    window.title("RanchoMedia：公众号单稿草稿接入（不公开发表）")
    window.geometry("760x620")
    panel = ttk.Frame(window, padding=24)
    panel.pack(fill="both", expand=True)
    ttk.Label(
        panel,
        text="只操作你本人确认的公众号：官方 token → 权限核验 → 封面 → 草稿 → 读取核对。",
        wraplength=700,
    ).pack(anchor="w", pady=8)
    ttk.Label(panel, text="当前稿件：" + variant()["title"], wraplength=700).pack(
        anchor="w", pady=8
    )
    entries = {}
    for key, label, hidden in [
        ("account_name", "公众号名称（用于绑定账号）", False),
        ("app_id", "AppID", False),
        ("app_secret", "AppSecret（不回显；已有合法 token 时可空）", True),
        ("access_token", "现有 access token（不回显；与 AppSecret 二选一）", True),
        ("thumb_media_id", "永久图片 media_id（可空，将上传已有原创封面）", False),
    ]:
        ttk.Label(panel, text=label).pack(anchor="w", pady=(8, 0))
        entry = ttk.Entry(panel, width=90, show="*" if hidden else "")
        entry.pack(fill="x")
        entries[key] = entry
    confirmed = tk.BooleanVar(value=False)
    tk.Checkbutton(
        panel,
        variable=confirmed,
        text="确认这是本人有权操作的公众号，仅为上述现有单稿上传封面并建草稿；不发表、不群发。",
        wraplength=700,
    ).pack(anchor="w", pady=12)
    status = tk.StringVar(
        value="凭据仅保存在当前 Windows 用户的 DPAPI 加密文件，不打印到聊天或日志。"
    )
    ttk.Label(panel, textvariable=status, wraplength=700).pack(anchor="w", pady=8)

    def start() -> None:
        values = {key: entry.get().strip() for key, entry in entries.items()}
        if (
            not confirmed.get()
            or not values["account_name"]
            or not values["app_id"]
            or not (values["app_secret"] or values["access_token"])
        ):
            messagebox.showerror(
                "需要本地配置", "请填写账号、AppID及 AppSecret/token，并确认单稿范围。"
            )
            return
        if (STATE / "receipt.json").exists():
            prior = load(STATE / "receipt.json")
            if prior.get("app_id_sha256") != digest(values["app_id"].encode()):
                messagebox.showerror("账号不匹配", "既有运行绑定了另一账号，不会覆盖或发送。")
                return
        store_credentials(values)
        dump(
            STATE / "authorization.json",
            {
                "account_name": values["account_name"],
                "account_self_confirmed": True,
                "app_id_sha256": digest(values["app_id"].encode()),
                "variant_sha256": variant_sha(),
                "scope": "ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY",
                "confirmed_at": datetime.now(UTC).isoformat(),
            },
        )
        for key in ("app_secret", "access_token"):
            entries[key].delete(0, tk.END)
        button.configure(state="disabled")
        status.set("正在通过官方 SDK 执行一次有界运行；请勿重复点击。")

        def work() -> None:
            result = run_locked()
            window.after(
                0,
                lambda: status.set(
                    "结果："
                    + result["status"]
                    + "。证据在 .runtime/social-live/wechat/，未公开发表。"
                ),
            )
            window.after(0, lambda: button.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    button = ttk.Button(panel, text="加密保存本地配置，并执行一次官方草稿准备", command=start)
    button.pack(fill="x", pady=12)
    window.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--ui", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--token-only", action="store_true")
    modes.add_argument("--probe-read-only", action="store_true")
    modes.add_argument("--repair-state", action="store_true")
    modes.add_argument("--network-check", action="store_true")
    parser.add_argument("--authorization-id", default="")
    parser.add_argument("--whitelist-confirmed", action="store_true")
    parser.add_argument("--use-env-proxy", action="store_true")
    args = parser.parse_args()
    if args.use_env_proxy and not args.token_only:
        parser.error(
            "--use-env-proxy is only valid for --token-only; probes inherit the token route"
        )
    if args.ui:
        ui()
    else:
        selected = next(
            name
            for name in (
                "execute",
                "token_only",
                "probe_read_only",
                "repair_state",
                "network_check",
            )
            if getattr(args, name)
        )
        print(
            json.dumps(
                run_locked(
                    selected.replace("_", "-"),
                    authorization_id=args.authorization_id,
                    whitelist_confirmed=args.whitelist_confirmed,
                    use_env_proxy=args.use_env_proxy,
                ),
                ensure_ascii=False,
            )
        )
