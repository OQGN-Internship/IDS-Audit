"""The Phase 4 gate for backup and restore (ENG-01 7.2): a backup restored
into an empty database and an empty uploads volume, with every total and
every file unchanged.

It runs a second Compose project, ids-audit-gate, with its own image, volumes
and ports and made-up passwords, so the live project is never touched. It
builds the image and takes minutes, so it runs only with
`uv run pytest -m docker` (ENG-01 7.5).
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.scripts import ROOT, git_bash, posix, run_script
from tests.test_docker import DUMMY, without_settings

pytestmark = pytest.mark.docker

PROJECT = "ids-audit-gate"
SAMPLE = Path(__file__).parent / "fixtures" / "sample.xlsx"

# Receives the sample file from standard input and approves it, as a fixed
# administrator, inside the gate's app container.
LOAD_SAMPLE = """
import sys
from ids_audit.accounts.models import User
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
admin = User.objects.create(
    username="gate", full_name="Gate Test", is_fixed_admin=True,
    must_change_password=False,
)
report = create_report("Sample", admin)
upload = receive_file(report, admin, "sample.xlsx", sys.stdin.buffer.read())
commit_upload(stage_upload(upload, admin), admin)
"""

# Prints the newest event's action and details.
LAST_EVENT = """
import json
from ids_audit.audit.models import AuditEvent
event = AuditEvent.objects.latest("id")
print(json.dumps({"action": event.action, "details": event.details}))
"""


def compose(env: dict, *args: str, data: bytes | None = None) -> str:
    """Run docker compose on the gate's project, named here as well as in
    the environment, so nothing reaches the live one. Return its output.
    """
    result = subprocess.run(
        ["docker", "compose", "--project-name", PROJECT, *args],
        cwd=ROOT,
        env=env,
        input=data,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8")


def manage(env: dict, *args: str, data: bytes | None = None) -> str:
    """Run a manage.py command in the gate's app container."""
    return compose(env, "exec", "-T", "app", "python", "manage.py", *args, data=data)


@pytest.fixture(scope="module")
def gate(tmp_path_factory) -> dict:
    """The gate's Compose project, built from this working copy and started
    empty, and removed with its volumes at the end. Return its environment.
    """
    if shutil.which("docker") is None:
        pytest.skip("Docker isn't installed.")
    folder = tmp_path_factory.mktemp("gate")
    env_file = folder / "gate.env"
    # Made-up values only, and ports of its own, so .env is never read.
    values = {**DUMMY, "APP_PORT": "18765", "DB_PORT": "15765"}
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), newline="\n")
    env = {
        **without_settings(),
        "COMPOSE_PROJECT_NAME": PROJECT,
        "COMPOSE_ENV_FILES": str(env_file),
    }
    compose(env, "down", "--volumes")
    compose(env, "build")
    compose(env, "up", "--detach", "--wait")
    yield env
    compose(env, "down", "--volumes")


def test_a_backup_restores_into_an_empty_database_with_every_total(
    gate, tmp_path
) -> None:
    """Verify backup.sh backs up the sample report, and restore.sh brings it
    back into an empty database and volume with every total and file
    matching, recording database_restored, after keeping a safety backup.
    """
    bash = git_bash()
    if bash is None:
        pytest.skip("Bash isn't installed.")
    manage(gate, "shell", "--no-imports", "-c", LOAD_SAMPLE, data=SAMPLE.read_bytes())
    before = json.loads(manage(gate, "report_totals"))
    assert [report["name"] for report in before["reports"]] == ["Sample"]

    backups = tmp_path / "backups"
    made = run_script(bash, "backup.sh", posix(backups), env=gate)
    assert made.returncode == 0, made.stdout + made.stderr
    (folder,) = backups.iterdir()
    assert sorted(path.name for path in folder.iterdir()) == [
        "SHA256SUMS",
        "db.dump",
        "totals.json",
        "uploads.tar",
    ]
    assert json.loads((folder / "totals.json").read_text()) == before
    running = compose(gate, "ps", "--status", "running", "--services").split()
    assert "app" in running

    # An empty database and an empty uploads volume.
    compose(gate, "down", "--volumes")
    compose(gate, "up", "--detach", "--wait")
    assert json.loads(manage(gate, "report_totals")) == {"reports": []}

    restored = run_script(bash, "restore.sh", posix(folder), env=gate)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "every total and every file matches the backup" in restored.stdout
    assert json.loads(manage(gate, "report_totals")) == before
    assert "1 file checked" in manage(gate, "check_uploads")
    assert json.loads(manage(gate, "shell", "--no-imports", "-c", LAST_EVENT)) == {
        "action": "database_restored",
        "details": {
            "backup": folder.name,
            "totals_match": True,
            "files_match": True,
            "command": "restore.sh",
        },
    }
    # The safety backup, of the empty database the restore replaced.
    assert len(list(backups.iterdir())) == 2


def test_a_restore_whose_totals_differ_says_so_and_fails(gate, tmp_path) -> None:
    """Verify a backup whose totals.json doesn't match its database, with
    SHA256SUMS written to match, restores but fails, saying the totals
    differ, and records database_restored with totals_match false.
    """
    bash = git_bash()
    if bash is None:
        pytest.skip("Bash isn't installed.")
    backups = tmp_path / "backups"
    made = run_script(bash, "backup.sh", posix(backups), env=gate)
    assert made.returncode == 0, made.stdout + made.stderr
    (folder,) = backups.iterdir()
    (folder / "totals.json").write_text('{"reports": "changed"}\n')
    sums = [
        f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  {name}\n"
        for name in ("db.dump", "uploads.tar", "totals.json")
    ]
    (folder / "SHA256SUMS").write_text("".join(sums), newline="\n")
    restored = run_script(
        bash, "restore.sh", posix(folder), "--no-safety-backup", env=gate
    )
    assert restored.returncode == 1
    assert "the totals differ from its totals.json" in restored.stderr
    assert json.loads(manage(gate, "shell", "--no-imports", "-c", LAST_EVENT))[
        "details"
    ] == {
        "backup": folder.name,
        "totals_match": False,
        "files_match": True,
        "command": "restore.sh",
    }
    assert len(list(backups.iterdir())) == 1
