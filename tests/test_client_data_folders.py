"""The client-data folders stay out of Git (ENG-01 6.4, Tested by)."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DATA_FOLDERS = ["backups", "real-export"]
READMES = [f"{folder}/README.md" for folder in CLIENT_DATA_FOLDERS]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a Git command in the repository and return its result."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, encoding="utf-8"
    )


def test_git_tracks_no_file_but_the_readmes() -> None:
    """Verify Git tracks nothing in the client-data folders except their READMEs."""
    listed = git("ls-files", "-z", "--", *CLIENT_DATA_FOLDERS)
    assert listed.returncode == 0, listed.stderr
    tracked = [path for path in listed.stdout.split("\0") if path]
    unexpected = [path for path in tracked if path not in READMES]
    assert unexpected == [], "Client data is in Git. Read CONTRIBUTING 7 first."


@pytest.mark.parametrize("folder", CLIENT_DATA_FOLDERS)
def test_git_ignores_every_new_file_but_the_readme(folder: str) -> None:
    """Verify .gitignore hides any new file in the folder, but not its README."""
    for path in ["export.xlsx", ".hidden", "2026-10-23_1800/db.dump"]:
        ignored = git("check-ignore", "--no-index", "-q", f"{folder}/{path}")
        assert ignored.returncode == 0, f"{folder}/{path} is not ignored"
    readme = git("check-ignore", "--no-index", "-q", f"{folder}/README.md")
    assert readme.returncode == 1, f"{folder}/README.md is ignored"
