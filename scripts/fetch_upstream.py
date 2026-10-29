"""Optional, pinned source checkout. Never installs or executes an upstream application."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=("postiz", "social-auto-upload"))
    args = parser.parse_args()
    pin = json.loads((ROOT / "integrations/upstreams.json").read_text())["projects"][
        args.name
    ]
    destination = (ROOT / pin["destination"]).resolve()
    if not destination.is_relative_to(ROOT):
        raise ValueError("Destination escapes repository")
    if destination.exists():
        head = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != pin["commit"]:
            raise ValueError("Existing checkout differs; refusing to overwrite it")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                pin["url"],
                str(destination),
            ],
            check=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(destination), "checkout", "--detach", pin["commit"]],
            check=True,
            timeout=180,
        )
    print(
        f"Pinned {args.name} source ready. No install, submodule recursion or platform execution."
    )


if __name__ == "__main__":
    main()
