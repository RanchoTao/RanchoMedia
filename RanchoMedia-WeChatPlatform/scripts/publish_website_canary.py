"""One explicitly authorized GitHub Pages canary; no force push or mutation retry.

The local authorization file binds account, base/head commits, one path and public URL.
Existing remote head equal to the target is reconciliation, never another push.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def command(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8", timeout=45).strip()


def publish(repo: Path, auth: dict[str, Any], output: Path) -> dict[str, Any]:
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    expected_repo = "RanchoTao/RanchoTao.github.io"
    if auth.get("repository") != expected_repo or auth.get("account") != "RanchoTao":
        raise ValueError("Explicit authorized repository/account required")
    if auth.get("permission") != "SINGLE_ARTICLE_PUBLIC_CANARY":
        raise ValueError("Single article public authorization required")
    relative = auth["path"]
    if not re.fullmatch(r"content/rancho/[0-9a-f-]{36}\.md", relative):
        raise ValueError("Only one generated article path allowed")
    url = auth["public_url"]
    if url != "https://ranchotao.com/" + relative.removesuffix(".md") + "/":
        raise ValueError("Public URL must match the authorized article")
    if command(["git", "status", "--porcelain"], repo):
        raise ValueError("Isolated website checkout must be clean")
    head, base = auth["head"], auth["base"]
    if command(["git", "rev-parse", "HEAD"], repo) != head:
        raise ValueError("Prepared commit changed")
    names = command(["git", "diff", "--name-only", base, head], repo).splitlines()
    if names != [relative]:
        raise ValueError("Change exceeds the single authorized article")
    body = (repo / relative).read_bytes()
    if hashlib.sha256(body).hexdigest() != auth["file_sha256"]:
        raise ValueError("Authorized file bytes changed")
    if command(["git", "remote", "get-url", "origin"], repo) != (
        "https://github.com/" + expected_repo + ".git"
    ):
        raise ValueError("Unexpected remote")
    remote = command(
        ["gh", "api", f"repos/{expected_repo}/git/ref/heads/master", "--jq", ".object.sha"], repo
    )
    if remote not in {base, head}:
        raise ValueError("Remote advanced; human review/rebase required")
    if previous and previous.get("remote_id") != head:
        raise ValueError("Existing receipt belongs to a different immutable intent")
    if previous and remote == base:
        # A previous process may have died after sending but before writing its result.
        # A base ref does not prove that the server will never complete an in-flight push.
        previous["status"] = "UNKNOWN_REMOTE_STATUS_RECONCILE_ONLY"
        output.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        return previous
    result: dict[str, Any] = {
        "status": "AUTHORIZED_PREPARED",
        "repository": expected_repo,
        "remote_id": head,
        "public_url": url,
        "path": relative,
        "file_sha256": auth["file_sha256"],
        "remote_head_before": remote,
        "authorization": auth["permission"],
        "pushed": False,
        "deployed": False,
        "public_url_verified": False,
        "started_at": datetime.now(UTC).isoformat(),
    }

    def save() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    save()  # Durable intent before external mutation.
    if remote == base:
        policy_path = output.parent / "operator-policy.json"
        if policy_path.exists():
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            if policy.get("paused") or policy.get("channels", {}).get("website", {}).get("paused"):
                result["status"] = "PAUSED"
                save()
                return result
        try:
            command(["git", "push", "origin", head + ":refs/heads/master"], repo)
        except (subprocess.SubprocessError, OSError):
            result["status"] = "UNKNOWN_REMOTE_STATUS_RECONCILE_ONLY"
            save()
            return result  # No mutation retry on uncertain response.
    result["pushed"] = True
    result["status"] = "REMOTE_COMMIT_ACCEPTED"
    save()
    for _ in range(16):  # Bounded read-only reconciliation, at most four minutes.
        runs = json.loads(
            command(
                ["gh", "api", f"repos/{expected_repo}/actions/runs?head_sha={head}&per_page=10"],
                repo,
            )
        )["workflow_runs"]
        matching = [run for run in runs if run["name"] == "Build and Deploy Jekyll Site"]
        if matching:
            run = matching[0]
            result.update(
                workflow_url=run["html_url"],
                workflow_status=run["status"],
                workflow_conclusion=run["conclusion"],
            )
            if run["status"] == "completed":
                result["deployed"] = run["conclusion"] == "success"
                break
        save()
        time.sleep(15)
    if result["deployed"]:
        for _ in range(6):
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "RanchoMedia-canary/2.0"}
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    page = response.read(2_000_000).decode("utf-8")
                    result["http_status"] = response.status
                plain = html.unescape(re.sub(r"<[^>]+>", "", page))
                required = [
                    "暂存区也称索引",
                    "一个假设性的Markdown修改",
                    "git-scm.com/docs/git-commit",
                    "git-scm.com/docs/git-push",
                ]
                result["content_checks"] = {
                    text: text in plain or text in page for text in required
                }
                if all(result["content_checks"].values()):
                    snapshot = output.with_suffix(".public.html")
                    snapshot.write_text(page, encoding="utf-8")
                    result["public_url_verified"] = True
                    result["public_html_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
                    break
            except (urllib.error.URLError, TimeoutError):
                result["public_read"] = "PENDING_OR_UNAVAILABLE"
            time.sleep(10)
    result["status"] = "PUBLICLY_VERIFIED" if result["public_url_verified"] else "DEPLOYMENT_HOLD"
    result["finished_at"] = datetime.now(UTC).isoformat()
    save()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("authorization", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            publish(
                args.repository.resolve(),
                json.loads(args.authorization.read_text(encoding="utf-8")),
                args.receipt.resolve(),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
