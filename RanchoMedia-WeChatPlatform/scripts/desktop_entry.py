"""Windowed executable entry. The workspace and user data are never bundled."""

import sys
from pathlib import Path

from rancho_wechat.desktop import main

if __name__ == "__main__":
    if getattr(sys, "frozen", False) and "--workspace" not in sys.argv:
        sys.argv.extend(["--workspace", str(Path(sys.executable).resolve().parents[2])])
    main()
