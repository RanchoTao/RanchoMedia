"""Bounded text scan. Emit locations/classifications only, never matching values."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from audit_baseline import PINS, scan

ROOT = Path(__file__).resolve().parents[1]


def git_files(directory: Path) -> list[Path]:
    result = subprocess.check_output(
        [
            "git",
            "-C",
            str(directory),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
    ).decode("utf-8")
    return [directory / name for name in result.split("\0") if name]


def run() -> dict:
    own_paths = [path for path in git_files(ROOT) if "upstream" not in path.relative_to(ROOT).parts]
    # Ignored .env/log/capture text requires an explicit scan as well. Runtime binary DB files
    # and virtual environments are outside this heuristic, and are never treated as inspected text.
    for directory in (
        ROOT,
        ROOT / ".runtime",
        ROOT / "fixtures",
        ROOT / "logs",
        ROOT / "screenshots",
    ):
        if not directory.exists():
            continue
        for pattern in (".env", ".env.*", "*.log", "*.txt", "*.json"):
            own_paths.extend(
                directory.glob(pattern) if directory == ROOT else directory.rglob(pattern)
            )
    own = scan(sorted(set(own_paths)), ROOT)
    upstream = []
    for name, pin in PINS.items():
        directory = ROOT / "upstream" / name
        head = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
        ).strip()
        result = scan(git_files(directory), directory)
        upstream.append(
            {
                "path": f"upstream/{name}",
                "pin_matches": head == pin,
                "files_scanned": result["files_scanned"],
                "findings": [
                    {
                        "path": f"upstream/{name}/{finding['file']}",
                        "classification": finding["rule"],
                        "status": "CANDIDATE_UNVERIFIED",
                    }
                    for finding in result["findings"]
                ],
            }
        )
    return {
        "own": own,
        "own_status": "PASS" if not own["findings"] else "FAIL",
        "upstream": upstream,
        "values_emitted": False,
        "limitations": "working tree text <=2MB; no secret validity check, binary/history scan",
    }


if __name__ == "__main__":
    report = run()
    (ROOT / "docs/evidence/phase2_secret_scan.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    raise SystemExit(
        0
        if report["own_status"] == "PASS"
        and all(item["pin_matches"] for item in report["upstream"])
        else 1
    )
