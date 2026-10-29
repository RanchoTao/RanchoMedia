"""Check tracked/candidate paths and gitlink modes. Does not read or print secrets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {
    ".runtime",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "cookies",
    "cookiesfile",
    "sessions",
    "browser-profiles",
    "private-content",
    "upstream",
}
PRIVATE_FILES = {"credentials.json", "authorization.json", "operator-policy.json"}
SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-shm",
    ".db-wal",
    ".dpapi",
    ".pem",
    ".key",
    ".log",
)


def path_violation(name: str, mode: str = "100644") -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    parts = [part.lower() for part in path.parts]
    basename = parts[-1] if parts else ""
    if mode == "160000":
        return "GITLINK_SOURCE_NOT_INCLUDED"
    if any(part in DIRECTORIES for part in parts[:-1]):
        return "PRIVATE_OR_GENERATED_DIRECTORY"
    if basename != ".env.example" and (
        basename == ".env" or basename.startswith(".env.")
    ):
        return "REAL_ENVIRONMENT_FILE"
    if (
        basename in PRIVATE_FILES
        or basename.endswith(SUFFIXES)
        or "storage-state" in basename
        or basename.endswith(".cookie.json")
    ):
        return "RUNTIME_OR_CREDENTIAL_ARTIFACT"
    return None


def inspect(root: Path) -> dict:
    def git(*args: str) -> list[bytes]:
        return subprocess.check_output(["git", "-C", str(root), *args]).split(b"\0")

    entries = {}
    for row in git("ls-files", "--stage", "-z"):
        if row:
            meta, name = row.split(b"\t", 1)
            entries[name.decode("utf-8")] = meta.decode().split()[0]
    for row in git("ls-files", "--others", "--exclude-standard", "-z"):
        if row:
            entries.setdefault(row.decode("utf-8"), "100644")
    violations = [
        {"path": name, "reason": reason}
        for name, mode in entries.items()
        if (reason := path_violation(name, mode))
    ]
    return {
        "status": "PASS" if not violations else "BLOCKED",
        "candidate_files": len(entries),
        "violations": violations,
        "scope": "path/mode check only; full-history Gitleaks and manual review are separate",
    }


if __name__ == "__main__":
    report = inspect(ROOT)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
