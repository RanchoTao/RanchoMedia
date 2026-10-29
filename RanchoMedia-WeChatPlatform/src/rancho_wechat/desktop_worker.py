"""Isolated SDK process. Reuse the existing draft-only worker without changing it."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def legacy_worker(workspace: Path, state: Path, cover: Path | None = None) -> Any:
    spec = importlib.util.spec_from_file_location(
        "rancho_desktop_legacy", workspace / "scripts/wechat_live_canary.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("LEGACY_WORKER_UNAVAILABLE")
    worker: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker.STATE = state
    if cover is not None:
        worker.COVER = cover
    return worker


def run(workspace: Path, state: Path, cover: Path, operation: str, permit: str) -> dict[str, Any]:
    worker = legacy_worker(workspace, state, cover)
    if operation not in {"token-only", "probe-read-only", "execute", "reconcile"}:
        return {"status": "INVALID_OPERATION", "real_platform_requests": 0}
    auth = worker.load(state / "authorization.json")
    if auth.get("desktop_permit") != permit:
        return {"status": "WAITING_AUTHORIZATION", "real_platform_requests": 0}
    if operation in {"execute", "reconcile"}:
        if auth.get("cover_sha256") != worker.digest(cover.read_bytes()):
            return {"status": "COVER_CHANGED", "real_platform_requests": 0}
        if operation == "reconcile":
            receipt = worker.load(state / "receipt.json")
            if not receipt.get("draft_media_id"):
                return {"status": "DRAFT_ID_REQUIRED", "real_platform_requests": 0}
    validation_path = state / "token-validation.json"
    validation = worker.load(validation_path) if validation_path.exists() else {}
    result: dict[str, Any] = worker.run_locked(
        "execute" if operation == "reconcile" else operation,
        authorization_id=permit,
        whitelist_confirmed=True,
        use_env_proxy=validation.get("network_mode") == "ENVIRONMENT_PROXY",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cover", type=Path, required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--permit", required=True)
    args = parser.parse_args()
    # Never print SDK responses; only a closed status code leaves this process.
    try:
        result = run(args.workspace, args.state, args.cover, args.operation, args.permit)
        status = result.get("status", "UNKNOWN")
        if not isinstance(status, str) or not status.replace("_", "").isalnum():
            status = "UNKNOWN"
    except Exception:
        status = "LOCAL_WORKER_FAILED"
    print(json.dumps({"status": status}))


if __name__ == "__main__":
    main()
