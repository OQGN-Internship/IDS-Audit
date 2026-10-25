"""Running scripts/backup.sh and scripts/restore.sh from the tests (ENG-01
7.2).
"""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_bash() -> str | None:
    """Return the Bash the scripts run in, or None without one.

    On Windows that's Git's, found beside git. The bash.exe in System32
    starts WSL instead, which has neither these paths nor Docker's CLI.
    """
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    for folder in Path(git).resolve().parents:
        bash = folder / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    return None


def posix(path: Path) -> str:
    """Write a path as Bash takes it on any machine: C:/Users/... on Windows."""
    return str(path).replace("\\", "/")


def run_script(
    bash: str, name: str, *args: str, env: dict
) -> subprocess.CompletedProcess:
    """Run scripts/<name> with Bash from the repository root."""
    return subprocess.run(
        [bash, f"scripts/{name}", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
