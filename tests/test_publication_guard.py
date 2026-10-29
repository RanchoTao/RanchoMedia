import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "publication_guard", ROOT / "scripts/check_public_tree.py"
)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        "a/.runtime/policy.json",
        "a/account.db",
        "a/account.sqlite3",
        "a/credentials.dpapi",
        "a/cookies/state.json",
        "a/authorization.json",
        "a/browser-profiles/Default/config",
        "a/operator-policy.json",
        "a/token.storage-state.json",
        "a/private-content/draft.md",
    ],
)
def test_private_and_generated_paths_rejected(name):
    assert guard.path_violation(name)


def test_gitlink_is_not_misrepresented_as_source():
    assert guard.path_violation("RanchoMediaOS", "160000")
    assert guard.path_violation("src/config.py") is None
    assert guard.path_violation("a/.env.example") is None


def test_gitignore_covers_private_data_without_hiding_safe_examples(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for name in [
        ".env.local",
        "a/.runtime/editor.db",
        "a/cookies/state.json",
        "a/credentials.dpapi",
        "a/authorization.json",
        "a/private-content/draft.md",
    ]:
        assert (
            subprocess.run(
                ["git", "-C", str(tmp_path), "check-ignore", "-q", name]
            ).returncode
            == 0
        )
    assert (
        subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", "-q", ".env.example"]
        ).returncode
        == 1
    )
