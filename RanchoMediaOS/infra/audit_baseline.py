"""Read-only upstream audit and Phase 0/1 artifact checks; no platform actions.

Writes scrubbed metadata to docs/evidence. Requires only Python's standard library.
This is a bounded working-tree heuristic secret scan, not a history/security audit.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "postiz": "528f6ea37c8cf1156ad4a12de4e7aed853c019f2",
    "social-auto-upload": "0012d2c355f88f683cc38dde2a2db209e14091bc",
}
DOCS = [
    "README.md", "LICENSE_DECISIONS.md", "ARCHITECTURE.md", "ROADMAP.md",
    "docs/UPSTREAM_AUDIT_POSTIZ.md", "docs/UPSTREAM_AUDIT_SOCIAL_AUTO_UPLOAD.md",
    "docs/PLATFORM_CAPABILITY_MATRIX.md", "docs/DATA_MODEL.md",
    "docs/AGENT_ARCHITECTURE.md", "docs/CONTENT_GENOME.md",
    "docs/ANALYTICS_DESIGN.md", "docs/SECURITY_MODEL.md",
    "docs/AUTONOMY_POLICY.md", "docs/MVP_PLAN.md",
    "docs/REPOSITORY_LAYOUT.md", "docs/VERIFICATION.md",
]
PLATFORMS = ["X", "YouTube", "TikTok", "Instagram", "Threads", "Reddit",
             "LinkedIn", "Medium", "WordPress", "小红书", "知乎", "抖音",
             "Bilibili", "微信公众号", "视频号", "微博", "快手", "百家号",
             "Personal Website"]
FEATURES = ["publish_text", "publish_image", "publish_video", "schedule",
            "fetch_post", "fetch_metrics", "fetch_comments", "reply_comment",
            "search", "trending", "delete", "edit", "webhook"]
TABLES = ["users", "platform_accounts", "platform_credentials", "trend_items",
          "sources", "content_ideas", "content_briefs", "content_items",
          "content_variants", "content_genomes", "media_assets", "publication_jobs",
          "publications", "metric_snapshots", "comments", "comment_replies",
          "experiments", "experiment_arms", "style_signatures", "strategy_snapshots",
          "agent_runs", "model_calls", "workflow_runs", "audit_logs"]
SECRET_RULES = {
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_key_literal": re.compile(r"\b(?:sk-proj-|sk-ant-|sk-)[A-Za-z0-9_-]{24,}"),
    "github_token_literal": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}"),
    "aws_access_id": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "secret_assignment_literal": re.compile(
        r'''(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]\s*["'][A-Za-z0-9_+/=.-]{24,}["']'''
    ),
}


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True,
                                   encoding="utf-8").strip()


def scan(paths: list[Path], base: Path) -> dict:
    findings = []
    scanned = 0
    skipped = 0
    for path in paths:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            skipped += 1
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            skipped += 1
            continue
        try:
            body = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped += 1
            continue
        scanned += 1
        for rule, pattern in SECRET_RULES.items():
            for match in pattern.finditer(body):
                findings.append({"file": path.relative_to(base).as_posix(),
                                 "line": body.count("\n", 0, match.start()) + 1,
                                 "rule": rule})
    return {"files_scanned": scanned, "files_skipped": skipped,
            "findings": findings, "matches_redacted": True,
            "scope": "working tree text files <=2MB; no history/binaries/private runtime"}


def upstream(name: str, expected: str) -> dict:
    path = ROOT / "upstream" / name
    files = [path / p for p in git(path, "ls-files").splitlines()]
    license_data = (path / "LICENSE").read_bytes()
    parsed = 0
    syntax_errors = []
    class_evidence = []
    for file in files:
        if file.suffix != ".py" or not file.is_file():
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8-sig"), filename=str(file))
            parsed += 1
            if "uploader" in file.parts:
                class_evidence.extend({"file": file.relative_to(path).as_posix(),
                                       "class": node.name, "line": node.lineno,
                                       "methods": [{"name": n.name, "line": n.lineno}
                                                   for n in node.body
                                                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]}
                                      for node in tree.body if isinstance(node, ast.ClassDef))
        except (SyntaxError, UnicodeError) as exc:
            syntax_errors.append({"file": file.relative_to(path).as_posix(),
                                  "error_type": type(exc).__name__,
                                  "line": getattr(exc, "lineno", None)})
    commit = git(path, "rev-parse", "HEAD")
    result = {"commit": commit, "expected_commit": expected, "pin_matches": commit == expected,
              "commit_time": git(path, "show", "-s", "--format=%cI", "HEAD"),
              "origin": git(path, "remote", "get-url", "origin"),
              "shallow": git(path, "rev-parse", "--is-shallow-repository") == "true",
              "head_reachable_commits": int(git(path, "rev-list", "--count", "HEAD")),
              "commits_since_2026_07_01": int(git(path, "rev-list", "--count", "--since=2026-07-01", "HEAD")),
              "tracked_status": git(path, "status", "--porcelain", "--untracked-files=no"),
              "tracked_file_count": len(files),
              "license_sha256_working_tree": hashlib.sha256(license_data).hexdigest(),
              "license_git_blob": git(path, "rev-parse", "HEAD:LICENSE"),
              "python_ast_parsed": parsed, "python_syntax_errors": syntax_errors,
              "uploader_classes": class_evidence,
              "secret_scan": scan(files, path),
              "full_build": "NOT_RUN", "live_platform_test": "NOT_RUN"}
    if name == "postiz":
        pkg = json.loads((path / "package.json").read_text(encoding="utf-8"))
        result["manifest"] = {"node": pkg["engines"]["node"], "package_manager": pkg["packageManager"],
                              "license": pkg["license"], "next": pkg["dependencies"]["next"],
                              "temporal": pkg["dependencies"]["@temporalio/workflow"]}
        directory = path / "libraries/nestjs-libraries/src/integrations/social"
        result["provider_method_evidence"] = {}
        for provider in ["x", "youtube", "tiktok", "instagram", "instagram.standalone",
                         "threads", "reddit", "linkedin", "linkedin.page", "medium", "wordpress"]:
            file = directory / f"{provider}.provider.ts"
            body = file.read_text(encoding="utf-8")
            result["provider_method_evidence"][provider] = [
                {"method": m.group(1), "line": body.count("\n", 0, m.start()) + 1}
                for m in re.finditer(r"(?m)^  (?:override )?(?:async )?(post|postPending|comment|analytics|postAnalytics|delete|update|search|trends|missing|checkPostStatus|finalizePost)\(", body)
            ]
    return result


def main() -> int:
    evidence = ROOT / "docs/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    upstreams = {name: upstream(name, pin) for name, pin in PINS.items()}
    missing = [doc for doc in DOCS if not (ROOT / doc).is_file()]
    bad_links = []
    for doc in DOCS:
        file = ROOT / doc
        if not file.is_file():
            continue
        body = file.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#")[0]
            if target and not (file.parent / target).exists():
                bad_links.append({"file": doc, "target": target})
    matrix = (ROOT / "docs/PLATFORM_CAPABILITY_MATRIX.md").read_text(encoding="utf-8")
    data_model = (ROOT / "docs/DATA_MODEL.md").read_text(encoding="utf-8")
    feature_coverage = {name: all(f in (ROOT / f"docs/{name}").read_text(encoding="utf-8") for f in FEATURES)
                        for name in ["UPSTREAM_AUDIT_POSTIZ.md", "UPSTREAM_AUDIT_SOCIAL_AUTO_UPLOAD.md"]}
    checks = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "phase": "0/1", "missing_docs": missing, "broken_local_links": bad_links,
              "platform_rows": {name: f"| {name} |" in matrix for name in PLATFORMS},
              "required_table_rows": {name: f"| {name} |" in data_model for name in TABLES},
              "feature_coverage": feature_coverage,
              "upstream_pin_and_clean": {n: u["pin_matches"] and not u["shallow"] and not u["tracked_status"]
                                         for n, u in upstreams.items()},
              "own_secret_scan": scan([p for p in ROOT.rglob("*") if p.is_file()
                                        and "upstream" not in p.relative_to(ROOT).parts
                                        and ".git" not in p.relative_to(ROOT).parts
                                        and ".runtime" not in p.relative_to(ROOT).parts], ROOT)}
    checks["phase_baseline_pass"] = (not missing and not bad_links and all(checks["platform_rows"].values())
                                     and all(checks["required_table_rows"].values())
                                     and all(feature_coverage.values())
                                     and all(checks["upstream_pin_and_clean"].values())
                                     and not checks["own_secret_scan"]["findings"])
    for filename, value in [("upstreams.json", upstreams), ("baseline_checks.json", checks)]:
        (evidence / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"phase_baseline_pass": checks["phase_baseline_pass"],
                      "missing_docs": missing, "broken_local_links": bad_links,
                      "platform_count": sum(checks["platform_rows"].values()),
                      "required_table_count": sum(checks["required_table_rows"].values()),
                      "own_secret_findings": len(checks["own_secret_scan"]["findings"]),
                      "upstreams": {n: {"pin_and_clean": checks["upstream_pin_and_clean"][n],
                                         "ast_parsed": u["python_ast_parsed"],
                                         "syntax_errors": u["python_syntax_errors"],
                                         "secret_findings": u["secret_scan"]["findings"]}
                                    for n, u in upstreams.items()}}, ensure_ascii=False, indent=2))
    return 0 if checks["phase_baseline_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
