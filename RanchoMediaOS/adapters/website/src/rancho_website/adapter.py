"""Offline Git website adapter. It never contacts a remote or creates a PR."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from rancho_core.domain import payload_hash
from rancho_platform_sdk.contracts import (
    BeforeSendError,
    Capability,
    ExternalOutcomeUnknownError,
    HumanInterventionRequiredError,
    PlatformAdapter,
    PlatformCapabilities,
    PublishRequest,
    PublishResult,
    ResultMetadata,
    TerminalPlatformError,
)


class WebsiteAdapter(PlatformAdapter):
    def __init__(
        self, repository: Path, *, format: str = "md", mode: str = "CREATE_COMMIT"
    ) -> None:
        if format not in {"md", "mdx", "json"} or mode not in {"DRY_RUN", "CREATE_COMMIT"}:
            raise ValueError("Unsupported website format or mode")
        self.repository = repository.resolve(strict=True)
        self.format, self.mode = format, mode
        if not (self.repository / ".git").is_dir():
            raise ValueError("Website target must be a local Git checkout")
        config_names = self._git("config", "--local", "--list", "--name-only").splitlines()
        if any(name.startswith(("filter.", "include.", "includeif.")) for name in config_names):
            raise ValueError("Website Git configuration contains executable or included extensions")
        if (self.repository / ".git" / "info" / "attributes").exists():
            raise ValueError("Website Git info attributes are not qualified")
        if list(self.repository.rglob(".gitattributes")):
            raise ValueError("Website Git attributes are not qualified")
        self._git("rev-parse", "--verify", "HEAD")

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            supported=frozenset({Capability.PUBLISH, Capability.FETCH_POST, Capability.RECONCILE}),
            native_idempotency=False,
        )

    def _git(self, *args: str) -> str:
        safe_environment = {
            name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")
        }
        safe_environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_PAGER": "cat",
            }
        )
        with TemporaryDirectory(prefix="rancho-git-hooks-") as hooks_dir:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository),
                    "-c",
                    f"core.hooksPath={hooks_dir}",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "commit.gpgsign=false",
                    *args,
                ],
                env=safe_environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=20,
            )
        if result.returncode:
            raise TerminalPlatformError(f"Git operation failed: {args[0]}")
        return result.stdout.strip()

    def _path(self, variant: UUID) -> Path:
        return Path("content") / "rancho" / f"{variant}.{self.format}"

    def _commits(self) -> list[tuple[str, str, str]]:
        raw = self._git("log", "--format=%H%x1f%cI%x1f%B%x1e", "HEAD")
        result = []
        for entry in raw.split("\x1e"):
            fields = entry.strip().split("\x1f", 2)
            if len(fields) == 3:
                result.append((fields[0], fields[1], fields[2]))
        return result

    def _find(self, account: UUID, key: str) -> tuple[str, str, str] | None:
        for commit, date, message in self._commits():
            trailers = dict(
                line.split(": ", 1)
                for line in message.splitlines()
                if line.startswith("Rancho-") and ": " in line
            )
            if trailers.get("Rancho-Idempotency-Key") == key and trailers.get(
                "Rancho-Account-ID"
            ) == str(account):
                return commit, date, trailers.get("Rancho-Payload-SHA256", "")
        return None

    def _result(self, commit: str, date: str, variant: UUID) -> PublishResult:
        return PublishResult(
            external_id=commit,
            external_url=(self.repository / self._path(variant)).as_uri(),
            published_at=datetime.fromisoformat(date),
            raw_metadata=ResultMetadata(simulation=True, adapter="website_local_git"),
        )

    def _content(self, request: PublishRequest) -> str:
        body = {
            "title": request.title,
            "text": request.text,
            "account_id": str(request.account_id),
            "content_variant_id": str(request.content_variant_id),
            "scheduled_at": request.scheduled_at.isoformat(),
            "approved_payload_sha256": payload_hash(request.approved_payload()),
        }
        if self.format == "json":
            return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
        front = "\n".join(
            f"{name}: {json.dumps(value, ensure_ascii=False)}"
            for name, value in body.items()
            if name != "text"
        )
        return f"---\n{front}\n---\n\n{request.text}\n"

    def preview(self, request: PublishRequest) -> tuple[Path, str]:
        if request.platform != "website":
            raise TerminalPlatformError("Website request has wrong platform")
        if request.media_assets:
            raise HumanInterventionRequiredError("Website media assets are not qualified")
        markup = request.title + "\n" + request.text
        if self.format in {"md", "mdx"} and (
            any(mark in markup for mark in ("<", ">", "{", "}"))
            or re.search(r"(?im)^\s*(?:import|export)\b|(?:javascript|data):", markup)
        ):
            raise HumanInterventionRequiredError("Website markup needs manual template review")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", request.idempotency_key):
            raise TerminalPlatformError("Invalid website idempotency key")
        return self._path(request.content_variant_id), self._content(request)

    def _publish_sync(self, request: PublishRequest) -> PublishResult:
        relative, content = self.preview(request)
        if self.mode == "DRY_RUN":
            raise BeforeSendError("DRY_RUN has no commit result; use preview")
        lock = self.repository / ".git" / "rancho-website.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise BeforeSendError("Website Git operation already in progress") from error
        os.close(fd)
        try:
            matched = self._find(request.account_id, request.idempotency_key)
            fingerprint = payload_hash(request.approved_payload())
            if matched:
                if matched[2] != fingerprint:
                    raise TerminalPlatformError("IDEMPOTENCY_CONFLICT")
                return self._result(matched[0], matched[1], request.content_variant_id)
            if self._git("status", "--porcelain", "--untracked-files=all"):
                raise BeforeSendError("Website checkout must be clean")
            target = self.repository / relative
            if (
                target.exists()
                or target.is_symlink()
                or target.parent.is_symlink()
                or target.parent.parent.is_symlink()
            ):
                raise TerminalPlatformError("Website target path already exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            try:
                self._git("add", "--", relative.as_posix())
                self._git(
                    "-c",
                    "user.name=Rancho Media OS",
                    "-c",
                    "user.email=rancho@localhost",
                    "commit",
                    "-m",
                    "Rancho website publication",
                    "-m",
                    f"Rancho-Idempotency-Key: {request.idempotency_key}\n"
                    f"Rancho-Account-ID: {request.account_id}\n"
                    f"Rancho-Payload-SHA256: {fingerprint}",
                    "--",
                    relative.as_posix(),
                )
            except (TerminalPlatformError, subprocess.TimeoutExpired) as error:
                # A commit may have succeeded even if its response was lost.
                # Never issue it again here.
                raise ExternalOutcomeUnknownError(
                    "Website commit outcome requires reconciliation"
                ) from error
            matched = self._find(request.account_id, request.idempotency_key)
            if matched is None:
                raise ExternalOutcomeUnknownError("Website commit not visible after write")
            return self._result(matched[0], matched[1], request.content_variant_id)
        finally:
            lock.unlink(missing_ok=True)

    async def publish(self, request: PublishRequest) -> PublishResult:
        return await asyncio.to_thread(self._publish_sync, request)

    async def lookup_by_idempotency_key(self, account: UUID, key: str) -> PublishResult | None:
        found = await asyncio.to_thread(self._find, account, key)
        if not found:
            return None
        commit, date, _ = found
        # The commit itself is authoritative; its variant is read from the committed path.
        path = await asyncio.to_thread(self._git, "show", "--format=", "--name-only", commit)
        names = [line for line in path.splitlines() if line.startswith("content/rancho/")]
        if len(names) != 1:
            raise HumanInterventionRequiredError("Website commit path is ambiguous")
        return self._result(commit, date, UUID(Path(names[0]).stem))

    async def fetch_post(self, external_id: str) -> PublishResult:
        if len(external_id) != 40 or any(c not in "0123456789abcdef" for c in external_id):
            raise TerminalPlatformError("Invalid commit ID")
        details = await asyncio.to_thread(self._git, "show", "-s", "--format=%cI", external_id)
        path = await asyncio.to_thread(self._git, "show", "--format=", "--name-only", external_id)
        names = [line for line in path.splitlines() if line.startswith("content/rancho/")]
        if len(names) != 1:
            raise HumanInterventionRequiredError("Website commit path is ambiguous")
        return self._result(external_id, details, UUID(Path(names[0]).stem))
