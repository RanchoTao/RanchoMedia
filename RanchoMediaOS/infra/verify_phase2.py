"""Execute gates and save scrubbed, machine-readable actual outcomes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


def main() -> int:
    RUNTIME.mkdir(exist_ok=True)
    stages = {
        "format": ["-m", "ruff", "format", "--check", "."],
        "lint": ["-m", "ruff", "check", ".", "--output-format", "concise"],
        "typecheck": ["-m", "mypy"],
        "tests": ["-m", "pytest", "-q", "-s", "--junitxml=.runtime/phase2-junit.xml"],
        "secret_scan": ["infra/secret_scan.py"],
    }
    results = {}
    hard_evidence = {}
    for name, arguments in stages.items():
        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        results[name] = {
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "exit_code": result.returncode,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
        print(f"{name}: {results[name]['status']}", flush=True)
        # Only fixture test output is used to extract the explicit hard gates.
        if name == "tests":
            for line in result.stdout.splitlines():
                index = line.find('{"')
                if index >= 0:
                    try:
                        item = json.loads(line[index:])
                    except json.JSONDecodeError:
                        continue
                    if (
                        "EXTERNAL_POSTS_AFTER_5_IDENTICAL_RETRIES" in item
                        or "UNKNOWN_OUTCOME_EXTERNAL_POSTS" in item
                    ):
                        hard_evidence.update(item)
            hard_evidence["real_api_process_restart"] = (
                "PASS" if "REAL_API_PROCESS_RESTART: PASS" in result.stdout else "NOT_CONFIRMED"
            )
        if result.returncode:
            # The scanner already emits only scrubbed fields; test data is synthetic.
            print(result.stdout[-5000:] + result.stderr[-2000:])
    counts = {}
    junit = RUNTIME / "phase2-junit.xml"
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            suite = case.attrib["classname"].split(".")[1]
            count = counts.setdefault(suite, {"passed": 0, "failed": 0, "skipped": 0})
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            count[status] += 1
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "baseline_commit": "3af20b3",
        "stages": results,
        "test_counts": counts,
        "hard_evidence": hard_evidence,
        "postgres_runtime": "PENDING_DOCKER",
        "temporal_runtime": "PENDING_ENVIRONMENT",
    }
    (ROOT / "docs/evidence/phase2_quality.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"test_counts": counts, "hard_evidence": hard_evidence}))
    return (
        0
        if all(item["status"] == "PASS" for item in results.values())
        and hard_evidence.get("EXTERNAL_POSTS_AFTER_5_IDENTICAL_RETRIES") == 1
        and hard_evidence.get("UNKNOWN_OUTCOME_EXTERNAL_POSTS") == 1
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
