"""Windows desktop host and narrow, credential-safe IPC. No HTTP business server."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .desktop_worker import legacy_worker

ERRORS = {
    40164: "微信拒绝了此次请求的出口 IP。请核对该域名的代理分流与实际出口。",
    40013: "AppID 无效，请核对当前公众号。",
    40125: "AppSecret 无效，请在本机重新输入。",
    40001: "访问令牌无效，请手动验证凭据。",
    42001: "令牌已过期，请手动验证凭据。",
    48001: "当前账号没有所需接口权限，请在公众号后台核对。",
    45009: "接口调用额度不足，请稍后手动重试。",
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size > 2_000_000:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DesktopError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(code)


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_name: str = Field(min_length=1, max_length=80)
    app_id: str = Field(pattern=r"^wx[A-Za-z0-9]{16}$")
    app_secret: SecretStr = Field(default=SecretStr(""), max_length=512)
    access_token: SecretStr = Field(default=SecretStr(""), max_length=2048)
    thumb_media_id: str = Field(default="", max_length=512)


class OperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    confirmed: bool
    app_id_sha256: str
    variant_sha256: str
    cover_sha256: str


class DesktopService:
    def __init__(self, workspace: Path, state: Path | None = None, no_network: bool = False):
        self.workspace = workspace.resolve()
        self.state = state or self.workspace / ".runtime/social-live/wechat"
        self.no_network = no_network
        self._worker = legacy_worker(self.workspace, self.state)
        self._selected_cover: Path | None = None
        self._process: subprocess.Popen[str] | None = None
        self._mutex = threading.RLock()

    def _credentials(self) -> dict[str, Any]:
        if not (self.state / "credentials.dpapi").exists():
            return {}
        try:
            value = self._worker.credentials()
            return value if isinstance(value, dict) else {}
        except Exception:
            raise DesktopError(
                "DPAPI_UNAVAILABLE", "无法解密本机凭据。请使用保存它的 Windows 账户。"
            ) from None

    def _event(self, action: str, code: str = "OK") -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "desktop-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"time": datetime.now(UTC).isoformat(), "action": action, "code": code},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _cover(self, credentials: dict[str, Any]) -> Path:
        return self._selected_cover or Path(credentials.get("cover_path") or self._worker.COVER)

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            config = ConfigInput.model_validate(payload)
        except ValidationError:
            raise DesktopError(
                "INVALID_CONFIG", "请填写名称、有效的 wx 开头 18 位 AppID；敏感字段长度超限。"
            ) from None
        with self._mutex:
            self.state.mkdir(parents=True, exist_ok=True)
            if (self.state / "desktop-dispatch.lock").exists():
                raise DesktopError("BUSY", "桌面任务正在执行或待核对，暂不能更改配置。")
            lock = self.state / "running.lock"
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise DesktopError("BUSY", "有任务执行或待核对，请先查看运行日志。") from None
            try:
                old = self._credentials()
                changed_account = bool(old.get("app_id") and old["app_id"] != config.app_id)
                if old.get("app_id") and old["app_id"] != config.app_id:
                    if read_json(self.state / "receipt.json").get("app_id_sha256"):
                        raise DesktopError(
                            "ACCOUNT_BOUND", "当前回执已绑定账号，请使用独立配置目录接入另一账号。"
                        )
                secret = config.app_secret.get_secret_value()
                token = config.access_token.get_secret_value()
                if changed_account and not secret:
                    raise DesktopError(
                        "SECRET_REQUIRED", "更换账号时必须同时输入该账号的 AppSecret。"
                    )
                merged = {
                    **old,
                    "account_name": config.account_name,
                    "app_id": config.app_id,
                    "app_secret": secret or old.get("app_secret", ""),
                    "access_token": token or ("" if secret else old.get("access_token", "")),
                    "thumb_media_id": config.thumb_media_id,
                }
                if self._selected_cover:
                    merged["cover_path"] = str(self._selected_cover)
                self._worker.store_credentials(merged)
                self._event("CONFIG_ENCRYPTED")
            finally:
                os.close(descriptor)
                lock.unlink()
        return self.snapshot()

    def set_cover(self, path: Path) -> dict[str, Any]:
        path = path.resolve()
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise DesktopError("INVALID_COVER", "请选择不超过 2 MB 的 PNG 或 JPEG 封面。")
        header = path.read_bytes()[:12]
        if not (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff")):
            raise DesktopError("INVALID_COVER", "文件内容不是 PNG 或 JPEG。")
        self._selected_cover = path
        self._event("COVER_SELECTED")
        return self.snapshot()

    def _receipt(self) -> dict[str, Any]:
        candidates = [
            self.state / name
            for name in (
                "receipt.json",
                "token-validation.json",
                "permission-probe.json",
            )
            if (self.state / name).exists()
        ]

        def receipt_order(candidate: Path) -> tuple[float, int, str]:
            value = read_json(candidate).get("updated_at")
            try:
                stamp = (
                    datetime.fromisoformat(value).timestamp() if isinstance(value, str) else None
                )
            except ValueError:
                stamp = None
            return (
                stamp if stamp is not None else candidate.stat().st_mtime,
                candidate.stat().st_mtime_ns,
                candidate.name,
            )

        path = max(candidates, key=receipt_order) if candidates else None
        raw = read_json(path) if path else {}
        code = None
        for part in (
            raw,
            raw.get("token_error"),
            raw.get("cover_error"),
            raw.get("probe"),
            raw.get("draft_add"),
            raw.get("draft_get"),
        ):
            if isinstance(part, dict):
                detail = part.get("evidence", part)
                if isinstance(detail, dict):
                    candidate = detail.get("errcode")
                    if isinstance(candidate, (int, str)) and str(candidate).lstrip("-").isdigit():
                        code = int(candidate) or None
                        if code:
                            break
        original = read_json(self.state / "receipt.json")
        token = read_json(self.state / "token-validation.json")
        permission = read_json(self.state / "permission-probe.json")
        normalized_original = {
            **original,
            "token_request_state": self._worker.token_state(original),
        }
        normalized_token = {**token, "token_request_state": self._worker.token_state(token)}
        current_token_state = normalized_token.get(
            "token_request_state"
        ) or normalized_original.get("token_request_state")
        unknown = current_token_state in {"SENDING", "UNKNOWN"} or any(
            original.get(field) in {"SENDING", "UNKNOWN"}
            for field in ("draft_state", "cover_state")
        )
        history = []
        historical_paths = [self.state / "receipt.json"]
        historical_paths.extend(sorted((self.state / "history").glob("*.json")))
        for historical_path in historical_paths:
            historical = read_json(historical_path)
            detail = historical.get("token_error") or historical.get("cover_error") or {}
            historical_code = self._worker.error_code(detail.get("errcode"))
            if historical_code:
                entry = {
                    "errcode": historical_code,
                    "time": historical.get("updated_at"),
                    "message": ERRORS.get(historical_code, "微信曾拒绝此请求。"),
                }
                if entry not in history:
                    history.append(entry)
        responses = raw.get("http_responses") or raw.get("evidence", {}).get("http_responses", [])
        last_http = responses[-1] if responses else {}
        media_id = original.get("draft_media_id")
        return {
            "status": str(raw.get("status", "NOT_RUN"))[:100],
            "errcode": code,
            "message": ERRORS.get(code or 0, "接口返回失败，请核对权限与配置。" if code else ""),
            "time": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat() if path else None,
            "draft_media_id": media_id if isinstance(media_id, str) else None,
            "token_state": current_token_state,
            "token_time": token.get("updated_at"),
            "permission_time": permission.get("updated_at"),
            "http_status": last_http.get("http_status"),
            "elapsed_ms": last_http.get("elapsed_ms"),
            "network_mode": token.get("network_mode", "DIRECT"),
            "history": history[-5:],
            "cover_state": original.get("cover_state"),
            "draft_state": original.get("draft_state"),
            "unknown": unknown,
            "permission_verified": permission.get("status") == "REMOTE_DRAFT_LIST_READ"
            and str(permission.get("updated_at", "")) >= str(token.get("updated_at", "")),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            credential_error = None
            try:
                secret = self._credentials()
            except DesktopError as error:
                secret, credential_error = {}, error.message
            cover = self._cover(secret)
            try:
                content = self._worker.variant()
                title, variant_hash = content["title"], self._worker.variant_sha()
            except (OSError, ValueError, KeyError):
                title, variant_hash = "未找到已准备稿件", ""
            events = []
            event_path = self.state / "desktop-events.jsonl"
            if event_path.exists():
                for line in event_path.read_text(encoding="utf-8").splitlines()[-30:]:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            job = read_json(self.state / "desktop-job.json")
            receipt = self._receipt()
            validation = read_json(self.state / "token-validation.json") or read_json(
                self.state / "receipt.json"
            )
            if receipt["token_state"] == "CONFIRMED" and not (
                secret.get("access_token")
                and secret.get("token_expires_at", 0) > time.time() + 60
                and validation.get("app_id_sha256") == sha(secret.get("app_id", "").encode())
                and validation.get("credentials_saved") is True
            ):
                receipt["token_state"] = "NOT_CURRENT"
            if receipt["token_state"] != "CONFIRMED":
                receipt["permission_verified"] = False
            return {
                "config": {
                    "account_name": secret.get("account_name", ""),
                    "app_id": secret.get("app_id", ""),
                    "app_id_sha256": sha(secret.get("app_id", "").encode()),
                    "has_secret": bool(secret.get("app_secret")),
                    "has_token": bool(secret.get("access_token")),
                    "thumb_media_id": secret.get("thumb_media_id", ""),
                    "encrypted": (self.state / "credentials.dpapi").exists(),
                    "credential_error": credential_error,
                    "cover_name": cover.name if cover.is_file() else None,
                    "cover_sha256": sha(cover.read_bytes()) if cover.is_file() else "",
                    "article_title": title,
                    "variant_sha256": variant_hash,
                    "sdk_ready": self._sdk_ready(),
                },
                "receipt": receipt,
                "events": events,
                "run": {
                    "running": bool(self._process and self._process.poll() is None),
                    "locked": (self.state / "running.lock").exists()
                    or (self.state / "desktop-dispatch.lock").exists(),
                    "operation": job.get("operation"),
                    "status": job.get("status"),
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                },
                "network_disabled": self.no_network,
                "workspace": self._workspace_snapshot(),
            }

    def _workspace_snapshot(self) -> dict[str, Any]:
        database = self.workspace / ".runtime/v1.1/editor.db"
        articles: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        if database.exists():
            try:
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                    connection.row_factory = sqlite3.Row
                    articles = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT a.id,a.status,v.title FROM articles a LEFT JOIN versions v "
                            "ON a.current_version_id=v.id ORDER BY a.rowid DESC LIMIT 30"
                        )
                    ]
                    tasks = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT task_key AS id,kind,status FROM tasks "
                            "ORDER BY rowid DESC LIMIT 20"
                        )
                    ]
            except sqlite3.Error:
                # A different schema is unavailable, never represented as successful empty data.
                return {"articles": [], "tasks": [], "database_status": "SCHEMA_UNAVAILABLE"}
        return {
            "articles": articles,
            "tasks": tasks,
            "database_status": "READ_ONLY" if database.exists() else "NOT_FOUND",
        }

    def _sdk_python(self) -> Path:
        return self.workspace / ".runtime/ecosystem/wechat-bili/.venv/Scripts/python.exe"

    def _sdk_ready(self) -> bool:
        if not self._sdk_python().is_file():
            return False
        packages = self._sdk_python().parents[1] / "Lib/site-packages"
        return any(
            read_json(path).get("vcs_info", {}).get("commit_id") == self._worker.PIN
            for path in packages.glob("wechatpy-*.dist-info/direct_url.json")
        )

    def start_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = OperationInput.model_validate(payload)
        except ValidationError:
            raise DesktopError("INVALID_REQUEST", "操作参数无效，请刷新后重试。") from None
        if not request.confirmed or request.operation not in {
            "token-only",
            "probe-read-only",
            "execute",
            "reconcile",
        }:
            raise DesktopError("AUTHORIZATION_REQUIRED", "需要确认本次账号、内容与草稿操作范围。")
        if self.no_network:
            raise DesktopError("NETWORK_DISABLED", "验收隔离模式禁止访问微信接口。")
        with self._mutex:
            snapshot = self.snapshot()
            config = snapshot["config"]
            if snapshot["run"]["running"] or snapshot["run"]["locked"]:
                raise DesktopError("BUSY", "已有任务或未释放执行锁，请先核对日志。")
            if not config["has_secret"] or not config["sdk_ready"]:
                raise DesktopError("NOT_READY", "请保存凭据并确认已固定版本的微信 SDK 环境。")
            if any(
                getattr(request, field) != config[field]
                for field in ("app_id_sha256", "variant_sha256", "cover_sha256")
            ):
                raise DesktopError("CONTENT_CHANGED", "账号、正文或封面已变化，请刷新并重新确认。")
            if snapshot["receipt"]["unknown"] and request.operation != "reconcile":
                raise DesktopError(
                    "OUTCOME_UNKNOWN", "上次结果不明，禁止重发。请先凭草稿 ID 核对。"
                )
            if request.operation == "reconcile" and not snapshot["receipt"]["draft_media_id"]:
                raise DesktopError("DRAFT_ID_REQUIRED", "缺少远端草稿 ID，请在公众号后台人工核对。")
            if request.operation in {"execute", "reconcile"} and not (
                config["variant_sha256"] and config["cover_sha256"]
            ):
                raise DesktopError("CONTENT_REQUIRED", "缺少既有文章或本地封面。")
            permit = uuid4().hex
            secret = self._credentials()
            self.state.mkdir(parents=True, exist_ok=True)
            dispatch = self.state / "desktop-dispatch.lock"
            try:
                handle = os.open(dispatch, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise DesktopError("BUSY", "另一个窗口已有任务或结果待核对。") from None
            os.close(handle)
            try:
                self._worker.dump(
                    self.state / "authorization.json",
                    {
                        "account_name": config["account_name"],
                        "account_self_confirmed": True,
                        "app_id_sha256": config["app_id_sha256"],
                        "variant_sha256": config["variant_sha256"],
                        "cover_sha256": config["cover_sha256"],
                        "desktop_permit": permit,
                        "scope": "ONE_EXISTING_VARIANT_DRAFT_AND_COVER_ONLY"
                        if request.operation in {"execute", "reconcile"}
                        else "DESKTOP_ACCOUNT_DIAGNOSTICS_ONLY",
                        "operation": request.operation,
                        "confirmed_at": datetime.now(UTC).isoformat(),
                    },
                )
            except Exception:
                dispatch.unlink(missing_ok=True)
                raise
            command = [
                str(self._sdk_python()),
                "-m",
                "rancho_wechat.desktop_worker",
                "--workspace",
                str(self.workspace),
                "--state",
                str(self.state),
                "--cover",
                str(self._cover(secret)),
                "--operation",
                request.operation,
                "--permit",
                permit,
            ]
            environment = {
                **os.environ,
                "PYTHONPATH": str(self.workspace / "src"),
                "PYTHONIOENCODING": "utf-8",
            }
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=self.workspace,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                dispatch.unlink(missing_ok=True)
                raise DesktopError(
                    "WORKER_START_FAILED", "无法启动 SDK 进程，未发送接口请求。"
                ) from None
            atomic_json(
                self.state / "desktop-job.json",
                {
                    "operation": request.operation,
                    "status": "RUNNING",
                    "pid": self._process.pid,
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )
            self._event("RUN_STARTED", request.operation)
            threading.Thread(target=self._watch, daemon=True).start()
        return self.snapshot()

    def _watch(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            output, _ = process.communicate(timeout=150)
            result = json.loads(output) if process.returncode == 0 else {}
            status = result.get("status", "LOCAL_WORKER_FAILED")
            if not isinstance(status, str) or not status.replace("_", "").isalnum():
                status = "LOCAL_WORKER_FAILED"
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            status = "UNKNOWN_PROCESS_TIMEOUT_MANUAL_CHECK_REQUIRED"
        except (ValueError, OSError):
            status = "LOCAL_WORKER_FAILED"
        with self._mutex:
            job = read_json(self.state / "desktop-job.json")
            atomic_json(
                self.state / "desktop-job.json",
                {**job, "status": status, "finished_at": datetime.now(UTC).isoformat()},
            )
            # The child result contains only a closed status, never raw SDK output.
            atomic_json(self.state / "desktop-result.json", {"status": status})
            self._event("RUN_FINISHED", status)
            # Crashes/timeouts retain the dispatch marker across host restarts.
            # An operator must inspect the remote state before removing it.
            if status != "LOCAL_WORKER_FAILED" and not status.startswith("UNKNOWN_PROCESS_"):
                (self.state / "desktop-dispatch.lock").unlink(missing_ok=True)


def trusted_document(current: str | None, expected: str | None) -> bool:
    if not current or not expected:
        return False
    first, second = urlsplit(current), urlsplit(expected)
    return first.scheme in {"http", "https", "file"} and (
        first.scheme,
        first.netloc,
        first.path,
    ) == (second.scheme, second.netloc, second.path)


class DesktopBridge:
    def __init__(self, service: DesktopService):
        self._service = service
        self._window: Any = None

    def _call(self, method: Any, *args: Any) -> dict[str, Any]:
        if self._window is None or not trusted_document(
            self._window.get_current_url(), self._window.real_url
        ):
            return {
                "ok": False,
                "error": {"code": "UNTRUSTED_DOCUMENT", "message": "页面来源未获授权。"},
            }
        try:
            return {"ok": True, "data": method(*args)}
        except DesktopError as error:
            return {"ok": False, "error": {"code": error.code, "message": error.message}}
        except Exception:
            return {
                "ok": False,
                "error": {"code": "LOCAL_ERROR", "message": "本机操作失败，请检查配置或重试。"},
            }

    def get_snapshot(self) -> dict[str, Any]:
        return self._call(self._service.snapshot)

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(self._service.save_config, payload)

    def start_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(self._service.start_operation, payload)

    def choose_cover(self) -> dict[str, Any]:
        def choose() -> dict[str, Any]:
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("图片 (*.png;*.jpg;*.jpeg)",),
            )
            return (
                self._service.set_cover(Path(selected[0])) if selected else self._service.snapshot()
            )

        return self._call(choose)

    def open_docs(self, page: str) -> dict[str, Any]:
        def open_allowed() -> dict[str, Any]:
            urls = {
                "account": "https://mp.weixin.qq.com/",
                "draft": "https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html",
            }
            if page not in urls:
                raise DesktopError("INVALID_LINK", "不支持的帮助页面。")
            webbrowser.open(urls[page])
            return {"opened": True}

        return self._call(open_allowed)


def main() -> None:
    parser = argparse.ArgumentParser(description="RanchoMedia Desktop · 草稿接入")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--width", type=int, default=1320)
    parser.add_argument("--height", type=int, default=920)
    args = parser.parse_args()
    if os.name == "nt":
        # Keep native controls and WebView crisp when moving across DPI scales.
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass  # pywebview retains its system-DPI fallback on older Windows.
    import webview

    service = DesktopService(args.workspace, args.state_dir, args.no_network)
    bridge = DesktopBridge(service)
    bundled = Path(getattr(sys, "_MEIPASS", args.workspace)) / "desktop/index.html"
    ui = bundled if getattr(sys, "frozen", False) else args.workspace / "desktop/dist/index.html"
    if not ui.is_file():
        parser.error("缺少前端构建，请先运行 desktop 目录中的 npm run build。")
    window = webview.create_window(
        "RanchoMedia · 隔离验收" if args.no_network else "RanchoMedia",
        str(ui),
        js_api=bridge,
        width=args.width,
        height=args.height,
        min_size=(900, 640),
        text_select=True,
        background_color="#f7f7f5",
    )
    bridge._window = window
    assert window is not None

    # No untrusted document may replace the privileged application document.
    def guard() -> None:
        native_window: Any = window.native
        for control in native_window.Controls:
            if hasattr(control, "CoreWebView2"):

                def navigation(sender: Any, event: Any) -> None:
                    if not trusted_document(event.Uri, window.real_url):
                        event.Cancel = True

                control.NavigationStarting += navigation

    window.events.before_show += guard
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.start(
        gui="edgechromium", debug=False, private_mode=True, icon=str(ui.parent / "rancho.ico")
    )


if __name__ == "__main__":
    main()
