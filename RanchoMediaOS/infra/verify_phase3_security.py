"""Bounded local source/history credential screening without emitting candidate values."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from audit_baseline import SECRET_RULES
from secret_scan import run as working_tree_scan

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True, encoding="utf-8", errors="replace"
    )


def main() -> int:
    current = working_tree_scan()
    history = git("log", "--all", "--patch", "--", ".", ":!upstream")
    counts = Counter(
        name for name, pattern in SECRET_RULES.items() for _ in pattern.finditer(history)
    )
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "current_own_status": current["own_status"],
        "current_own_files_scanned": current["own"]["files_scanned"],
        "current_own_findings": current["own"]["findings"],
        "history_commits": len(git("rev-list", "--all").splitlines()),
        "history_patch_rule_counts": dict(counts),
        "remote_names": git("remote").splitlines(),
        "upstream_pins_match": all(item["pin_matches"] for item in current["upstream"]),
        "values_emitted": False,
        "limitations": (
            "Heuristic text patterns on current own files and Git patches; "
            "not a full security or binary/credential validity audit"
        ),
    }
    destination = ROOT / "docs" / "evidence" / "phase3_security.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({key: value for key, value in report.items() if key != "current_own_findings"})
    )
    return (
        0 if current["own_status"] == "PASS" and not counts and report["upstream_pins_match"] else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
